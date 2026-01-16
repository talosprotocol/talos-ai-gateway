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
    def __init__(self, db: Session):
        self.db = db

    def _verify_size(self, b64u_str: str):
        # Approximate size check or decode check
        # len(b64) * 3/4 approx bytes.
        # Strict check: decode and measure.
        if len(b64u_str) > A2A_MAX_FRAME_BYTES * 1.4: # Optimization
             raise ValueError("Frame too large (pre-decode)")
        
        try:
            # Add padding back if missing? No, strict b64u usually?
            # Contracts define "base64url (no padding)".
            # Python standard b64decode 'urlsafe_b64decode' handles it if padding is correct or we add it.
            # But 'no padding' means stripped.
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
        # Preimage: {schema_id, schema_version, session_id, sender_id, sender_seq, header_b64u, ciphertext_hash}
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
             # logger.error(f"Digest mismatch. Calc: {calculated_digest}, Recvd: {frame.frame_digest}, Preimage: {preimage}")
             raise ValueError("A2A_FRAME_DIGEST_MISMATCH")

    def store_frame(self, frame: EncryptedFrame, recipient_id: str) -> A2AFrame:
        # 1. Size Check
        ct_bytes = self._verify_size(frame.ciphertext_b64u)
        
        # 2. Digest Check
        self._verify_digests(frame, ct_bytes)
        
        # 3. Replay & Sequence Check
        # Check if exists
        existing = self.db.query(A2AFrame).filter(
            A2AFrame.session_id == frame.session_id,
            A2AFrame.sender_id == frame.sender_id,
            A2AFrame.sender_seq == frame.sender_seq
        ).first()
        
        if existing:
            raise ValueError("A2A_FRAME_REPLAY_DETECTED")
            
        # Check max future delta
        # Get max sender_seq seen for this session/sender
        last_seq_row = self.db.query(A2AFrame.sender_seq).filter(
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
        self.db.add(db_frame)
        return db_frame

    def list_frames(self, session_id: str, recipient_id: str, cursor: Optional[str] = None, limit: int = 100) -> Tuple[List[A2AFrame], Optional[str]]:
        query = self.db.query(A2AFrame).filter(
            A2AFrame.session_id == session_id,
            A2AFrame.recipient_id == recipient_id
        )
        
        # Cursor logic: (created_at, sender_id, sender_seq) usually for deterministic order if multiple senders?
        # Contract says cursor deterministic key: (created_at, frame_id) ?? There is no global frame_id.
        # For A2A, (created_at, sender_id, sender_seq) is unique.
        
        # If cursor provided, decode it.
        # Assuming simple ordering by created_at asc
        query = query.order_by(A2AFrame.created_at.asc(), A2AFrame.sender_id.asc(), A2AFrame.sender_seq.asc())
        
        if cursor:
             # Basic implementation: implement proper cursor later. 
             # For MVP, maybe offset? No, "contracts helpers".
             # app.infrastructure.canonical.Cursor? 
             # I need to check how to use it.
             pass
             
        frames = query.limit(limit).all()
        next_cursor = None # placeholder
        
        return frames, next_cursor
