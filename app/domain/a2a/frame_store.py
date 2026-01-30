import logging
import hashlib
import base64
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session

from app.domain.a2a.models import EncryptedFrame
from app.adapters.postgres.models import A2AFrame
from app.domain.a2a.canonical import canonical_json_bytes

logger = logging.getLogger(__name__)

A2A_MAX_FRAME_BYTES = 1_048_576  # 1 MiB
A2A_MAX_FUTURE_DELTA = 1024

class A2AFrameStore:
    def __init__(self, write_db: Session, read_db: Optional[Session] = None):
        self.write_db = write_db
        self.read_db = read_db if read_db else write_db

    def _verify_size(self, b64u_str: str):
        # Precise size check
        try:
            # base64url padding check and decode
            padded = b64u_str + '=' * (-len(b64u_str) % 4)
            data = base64.urlsafe_b64decode(padded)
            if len(data) > A2A_MAX_FRAME_BYTES:
                raise ValueError("A2A_FRAME_SIZE_EXCEEDED")
            return data
        except ValueError as e:
            if str(e) == "A2A_FRAME_SIZE_EXCEEDED":
                raise e
            raise ValueError("A2A_FRAME_SCHEMA_INVALID")
        except Exception:
            raise ValueError("A2A_FRAME_SCHEMA_INVALID")

    def _verify_digests(self, frame: EncryptedFrame, ciphertext_bytes: bytes):
        # 1. ciphertext_hash
        ct_hash = hashlib.sha256(ciphertext_bytes).hexdigest()
        if ct_hash != frame.ciphertext_hash:
            raise ValueError("A2A_FRAME_CIPHERTEXT_HASH_MISMATCH")
            
        # 2. frame_digest
        preimage = {
            "schema_id": frame.schema_id,
            "schema_version": frame.schema_version,
            "session_id": frame.session_id,
            "sender_id": frame.sender_id,
            "sender_seq": frame.sender_seq,
            "header_b64u": frame.header_b64u,
            "ciphertext_hash": frame.ciphertext_hash
        }
        digest_bytes = canonical_json_bytes(preimage)
        calculated_digest = hashlib.sha256(digest_bytes).hexdigest()
        
        if calculated_digest != frame.frame_digest:
             raise ValueError("A2A_FRAME_DIGEST_MISMATCH")

    def store_frame(self, frame: EncryptedFrame, recipient_id: str) -> A2AFrame:
        # Enforce WRITE DB
        # 1. Size Check
        ct_bytes = self._verify_size(frame.ciphertext_b64u)
        
        # 2. Digest Check
        self._verify_digests(frame, ct_bytes)
        
        # 3. Replay & Sequence Check
        existing = self.write_db.query(A2AFrame).filter(
            A2AFrame.session_id == frame.session_id,
            A2AFrame.sender_id == frame.sender_id,
            A2AFrame.sender_seq == frame.sender_seq
        ).first()
        
        if existing:
            raise ValueError("A2A_FRAME_REPLAY_DETECTED")
            
        # Check max future delta
        last_seq_row = self.write_db.query(A2AFrame.sender_seq).filter(
            A2AFrame.session_id == frame.session_id,
            A2AFrame.sender_id == frame.sender_id
        ).order_by(A2AFrame.sender_seq.desc()).first()
        
        last_seq = last_seq_row[0] if last_seq_row else -1
        
        if frame.sender_seq > last_seq + A2A_MAX_FUTURE_DELTA:
             raise ValueError("A2A_FRAME_SEQUENCE_TOO_FAR")
             
        # 4. Store
        db_frame = A2AFrame(
            session_id=frame.session_id,
            sender_id=frame.sender_id,
            sender_seq=frame.sender_seq,
            recipient_id=recipient_id,
            frame_digest=frame.frame_digest,
            ciphertext_hash=frame.ciphertext_hash,
            header_b64u=frame.header_b64u,
            ciphertext_b64u=frame.ciphertext_b64u,
            created_at=frame.created_at
        )
        self.write_db.add(db_frame)
        return db_frame

    def list_frames(
        self, 
        session_id: str, 
        recipient_id: str, 
        cursor: Optional[str] = None, 
        limit: int = 100,
        consistency: str = "strong"
    ) -> Tuple[List[A2AFrame], Optional[str]]:
        
        db = self.write_db
        if consistency == "eventual":
            db = self.read_db
            
        query = db.query(A2AFrame).filter(
            A2AFrame.session_id == session_id,
            A2AFrame.recipient_id == recipient_id
        )
        
        # Cursor logic: (created_at, sender_id, sender_seq)
        if cursor:
            try:
                # b64u decode
                padded = cursor + '=' * (-len(cursor) % 4)
                raw = base64.urlsafe_b64decode(padded).decode('utf-8')
                ts_str, s_id, s_seq = raw.split('|')
                ts = datetime.fromisoformat(ts_str)
                seq = int(s_seq)
                
                # Filter rows strictly after the cursor
                # (created_at > ts) OR (created_at == ts AND sender_id > s_id) OR (created_at == ts AND sender_id == s_id AND sender_seq > seq)
                from sqlalchemy import or_, and_
                query = query.filter(
                    or_(
                        A2AFrame.created_at > ts,
                        and_(A2AFrame.created_at == ts, A2AFrame.sender_id > s_id),
                        and_(A2AFrame.created_at == ts, A2AFrame.sender_id == s_id, A2AFrame.sender_seq > seq)
                    )
                )
            except Exception as e:
                logger.warning(f"Invalid A2A cursor: {cursor}, error: {e}")
                # Fallback to beginning on invalid cursor
        
        query = query.order_by(A2AFrame.created_at.asc(), A2AFrame.sender_id.asc(), A2AFrame.sender_seq.asc())
        frames = query.limit(limit).all()
        
        next_cursor = None
        if len(frames) == limit:
            last = frames[-1]
            # Encode next cursor
            raw_next = f"{last.created_at.isoformat()}|{last.sender_id}|{last.sender_seq}"
            next_cursor = base64.urlsafe_b64encode(raw_next.encode('utf-8')).decode('ascii').rstrip('=')
        
        return frames, next_cursor
