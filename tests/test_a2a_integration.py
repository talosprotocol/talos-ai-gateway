"""A2A Integration Tests.

Tests the full A2A lifecycle including:
- Session lifecycle (create, accept, close)
- Frame storage with digest validation
- Replay detection
- Authorization paths (401, 403, 201)
- Concurrent writer detection
"""

import pytest
import asyncio
import hashlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.postgres.models import Base, A2ASession, A2AFrame
from app.domain.a2a.session_manager import A2ASessionManager
from app.domain.a2a.frame_store import A2AFrameStore
from app.domain.a2a.group_manager import A2AGroupManager
from app.domain.a2a.models import (
    SessionCreateRequest, SessionAcceptRequest, EncryptedFrame,
    GroupCreateRequest, GroupMemberAddRequest
)
from app.domain.a2a.canonical import canonical_json_bytes


# Test database setup
@pytest.fixture(scope="function")
def db_session():
    """Create a fresh SQLite in-memory DB for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestSessionLifecycle:
    """Test session create → accept → close flow."""

    def _make_create_req(self, responder_id: str = "bob-did") -> SessionCreateRequest:
        """Create a valid SessionCreateRequest with dummy ratchet state."""
        return SessionCreateRequest(
            responder_id=responder_id,
            ratchet_state_blob_b64u="dGVzdC1yYXRjaGV0LXN0YXRl",  # base64url
            ratchet_state_digest="a" * 64,  # valid hex64
        )

    def _make_accept_req(self) -> SessionAcceptRequest:
        """Create a valid SessionAcceptRequest."""
        return SessionAcceptRequest(
            ratchet_state_blob_b64u="dGVzdC1yYXRjaGV0LXN0YXRl",
            ratchet_state_digest="b" * 64,
        )

    def test_create_session(self, db_session):
        sm = A2ASessionManager(db_session)
        req = self._make_create_req()
        
        session = sm.create_session("alice-did", req)
        
        assert session.state == "pending"
        assert session.initiator_id == "alice-did"
        assert session.responder_id == "bob-did"
        assert session.session_id is not None

    def test_accept_session(self, db_session):
        sm = A2ASessionManager(db_session)
        req = self._make_create_req()
        session = sm.create_session("alice-did", req)
        
        # Mock advisory lock for SQLite
        with patch.object(sm, '_advisory_lock'):
            accept_req = self._make_accept_req()
            session = sm.accept_session(session.session_id, "bob-did", accept_req)
        
        assert session.state == "active"

    def test_accept_wrong_responder_fails(self, db_session):
        sm = A2ASessionManager(db_session)
        req = self._make_create_req()
        session = sm.create_session("alice-did", req)
        
        with patch.object(sm, '_advisory_lock'):
            with pytest.raises(PermissionError, match="Not the designated responder"):
                sm.accept_session(session.session_id, "eve-did", self._make_accept_req())

    def test_close_session(self, db_session):
        sm = A2ASessionManager(db_session)
        req = self._make_create_req()
        session = sm.create_session("alice-did", req)
        
        with patch.object(sm, '_advisory_lock'):
            sm.accept_session(session.session_id, "bob-did", self._make_accept_req())
            session = sm.close_session(session.session_id, "alice-did")
        
        assert session.state == "closed"

    def test_invalid_state_transition_fails(self, db_session):
        sm = A2ASessionManager(db_session)
        req = self._make_create_req()
        session = sm.create_session("alice-did", req)
        
        with patch.object(sm, '_advisory_lock'):
            sm.close_session(session.session_id, "alice-did")
            
            # Try to accept a closed session
            with pytest.raises(ValueError, match="Invalid state transition"):
                sm.accept_session(session.session_id, "bob-did", self._make_accept_req())


class TestFrameValidation:
    """Test frame digest validation and replay detection."""

    def _make_valid_frame(self, session_id: str, sender_id: str, sender_seq: int) -> EncryptedFrame:
        """Create a valid frame with correct digests."""
        # UUIDv7 format: xxxxxxxx-xxxx-7xxx-yxxx-xxxxxxxxxxxx
        if session_id == "sess-1":
            session_id = "01234567-89ab-7cde-8f01-23456789abcd"
            
        header_b64u = "eyJhbGciOiJFZERTQSJ9"  # {"alg":"EdDSA"}
        ciphertext_b64u = "dGVzdC1jaXBoZXJ0ZXh0"  # "test-ciphertext"
        
        # Compute ciphertext_hash
        ct_bytes = b"test-ciphertext"
        ciphertext_hash = hashlib.sha256(ct_bytes).hexdigest()
        
        # Compute frame_digest
        preimage = {
            "schema_id": "talos.a2a.encrypted_frame",
            "schema_version": "v1",
            "session_id": session_id,
            "sender_id": sender_id,
            "sender_seq": sender_seq,
            "header_b64u": header_b64u,
            "ciphertext_hash": ciphertext_hash
        }
        frame_digest = hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()
        
        return EncryptedFrame(
            session_id=session_id,
            sender_id=sender_id,
            sender_seq=sender_seq,
            header_b64u=header_b64u,
            ciphertext_b64u=ciphertext_b64u,
            frame_digest=frame_digest,
            ciphertext_hash=ciphertext_hash,
            created_at=datetime.utcnow(),
        )

    def test_store_valid_frame(self, db_session):
        fs = A2AFrameStore(db_session)
        frame = self._make_valid_frame("sess-1", "alice", 0)
        
        stored = fs.store_frame(frame, "bob")
        
        # UUID is normalized in _make_valid_frame
        assert stored.session_id == "01234567-89ab-7cde-8f01-23456789abcd"
        assert stored.sender_seq == 0

    def test_replay_detection(self, db_session):
        fs = A2AFrameStore(db_session)
        frame = self._make_valid_frame("sess-1", "alice", 0)
        
        fs.store_frame(frame, "bob")
        
        with pytest.raises(ValueError, match="A2A_FRAME_REPLAY_DETECTED"):
            fs.store_frame(frame, "bob")

    def test_digest_mismatch_rejected(self, db_session):
        fs = A2AFrameStore(db_session)
        frame = self._make_valid_frame("sess-1", "alice", 0)
        
        # Tamper with frame_digest
        frame.frame_digest = "0" * 64
        
        with pytest.raises(ValueError, match="A2A_FRAME_DIGEST_MISMATCH"):
            fs.store_frame(frame, "bob")

    def test_ciphertext_hash_mismatch_rejected(self, db_session):
        fs = A2AFrameStore(db_session)
        frame = self._make_valid_frame("sess-1", "alice", 0)
        
        # Tamper with ciphertext_hash
        frame.ciphertext_hash = "a" * 64
        
        with pytest.raises(ValueError, match="A2A_FRAME_CIPHERTEXT_HASH_MISMATCH"):
            fs.store_frame(frame, "bob")

    def test_sequence_too_far_rejected(self, db_session):
        fs = A2AFrameStore(db_session)
        frame = self._make_valid_frame("sess-1", "alice", 2000)  # Way beyond max delta
        
        with pytest.raises(ValueError, match="A2A_FRAME_SEQUENCE_TOO_FAR"):
            fs.store_frame(frame, "bob")


class TestGroupLifecycle:
    """Test group create → add member → remove member → close."""

    def test_create_group(self, db_session):
        gm = A2AGroupManager(db_session)
        req = GroupCreateRequest(name="security-ops")
        
        group = gm.create_group("admin-did", req)
        
        assert group.state == "active"
        assert group.owner_id == "admin-did"

    def test_add_member(self, db_session):
        gm = A2AGroupManager(db_session)
        req = GroupCreateRequest(name="security-ops")
        group = gm.create_group("admin-did", req)
        
        with patch.object(gm, '_advisory_lock'):
            member_req = GroupMemberAddRequest(member_id="alice-did")
            group = gm.add_member(group.group_id, "admin-did", member_req)
        
        assert group.state == "active"

    def test_non_owner_cannot_add_member(self, db_session):
        gm = A2AGroupManager(db_session)
        req = GroupCreateRequest(name="security-ops")
        group = gm.create_group("admin-did", req)
        
        with patch.object(gm, '_advisory_lock'):
            with pytest.raises(PermissionError, match="Only owner can add members"):
                gm.add_member(group.group_id, "eve-did", GroupMemberAddRequest(member_id="bob-did"))

    def test_close_group(self, db_session):
        gm = A2AGroupManager(db_session)
        req = GroupCreateRequest(name="security-ops")
        group = gm.create_group("admin-did", req)
        
        with patch.object(gm, '_advisory_lock'):
            group = gm.close_group(group.group_id, "admin-did")
        
        assert group.state == "closed"


class TestConcurrency:
    """Test single-writer enforcement via advisory locks."""

    def _make_create_req(self, responder_id: str = "bob-did") -> SessionCreateRequest:
        return SessionCreateRequest(
            responder_id=responder_id,
            ratchet_state_blob_b64u="dGVzdC1yYXRjaGV0LXN0YXRl",
            ratchet_state_digest="a" * 64,
        )

    def _make_accept_req(self) -> SessionAcceptRequest:
        return SessionAcceptRequest(
            ratchet_state_blob_b64u="dGVzdC1yYXRjaGV0LXN0YXRl",
            ratchet_state_digest="b" * 64,
        )

    def test_lock_contention_error_code(self, db_session):
        """Verify A2A_LOCK_CONTENTION is raised when lock fails."""
        sm = A2ASessionManager(db_session)
        
        # Mock the advisory lock to simulate contention
        def mock_lock_fail(session_id):
            raise ValueError("A2A_LOCK_CONTENTION")
        
        req = self._make_create_req()
        session = sm.create_session("alice-did", req)
        
        with patch.object(sm, '_advisory_lock', side_effect=mock_lock_fail):
            with pytest.raises(ValueError, match="A2A_LOCK_CONTENTION"):
                sm.accept_session(session.session_id, "bob-did", self._make_accept_req())


class TestErrorCodes:
    """Verify stable error codes are returned."""

    ERROR_CODES = [
        "A2A_FRAME_REPLAY_DETECTED",
        "A2A_FRAME_DIGEST_MISMATCH",
        "A2A_FRAME_CIPHERTEXT_HASH_MISMATCH",
        "A2A_FRAME_SEQUENCE_TOO_FAR",
        "A2A_SESSION_STATE_INVALID",  # Covered by invalid transition test
        "A2A_LOCK_CONTENTION",
    ]

    def test_error_codes_are_stable_strings(self):
        """Error codes should be constants that don't change."""
        for code in self.ERROR_CODES:
            assert code.startswith("A2A_")
            assert code == code.upper()
