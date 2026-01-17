import logging
import hashlib
import base64
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session

from app.domain.a2a.models import EncryptedFrame
from app.adapters.postgres.models import A2AFrame
from app.domain.a2a.canonical import canonical_json_bytes

logger = logging.getLogger(__name__)

A2A_MAX_FRAME_BYTES = 1_048_576  # 1 MiB
A2A_MAX_FUTURE_DELTA = 1024

class A2AFrameStore:
    def __init__(self, write_db: Session, read_db: Session = None):
        self.write_db = write_db
        self.read_db = read_db if read_db else write_db

    def _verify_size(self, b64u_str: str):
        # Approximate size check or decode check
        if len(b64u_str) > A2A_MAX_FRAME_BYTES * 1.4:
             raise ValueError("Frame too large (pre-decode)")
        
        try:
            padded = b64u_str + '=' * (-len(b64u_str) % 4)
            data = base64.urlsafe_b64decode(padded)
            if len(data) > A2A_MAX_FRAME_BYTES:
                raise ValueError("Frame too large")
            return data
        except Exception as e:
            raise ValueError(f"Invalid base64url: {str(e)}")

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
        consistency: str = "strong" # Phase 12 Add
    ) -> Tuple[List[A2AFrame], Optional[str]]:
        
        # Determine DB Source
        # Spec A1: Default Strong (write_db). Eventual allowed if explicitly requested.
        db = self.write_db
        if consistency == "eventual":
            db = self.read_db
            
        query = db.query(A2AFrame).filter(
            A2AFrame.session_id == session_id,
            A2AFrame.recipient_id == recipient_id
        )
        
        query = query.order_by(A2AFrame.created_at.asc(), A2AFrame.sender_id.asc(), A2AFrame.sender_seq.asc())
        
        if cursor:
             # Cursor impl placeholder
             pass
             
        frames = query.limit(limit).all()
        next_cursor = None
        
        return frames, next_cursor
