import pytest
from unittest.mock import MagicMock, ANY
from datetime import datetime, timedelta, timezone
from app.domain.a2a.session_manager import A2ASessionManager
from app.domain.a2a.models import SessionCreateRequest, SessionAcceptRequest, SessionRotateRequest
from app.adapters.postgres.models import A2ASession, A2ASessionEvent

@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def session_manager(mock_db):
    return A2ASessionManager(mock_db)

valid_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
valid_b64 = "aGVsbG8"

def test_create_session(session_manager, mock_db):
    req = SessionCreateRequest(
        responder_id="responder",
        ratchet_state_blob_b64u=valid_b64,
        ratchet_state_digest=valid_digest
    )
    
    # Mock sequence query
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    
    session = session_manager.create_session("initiator", req)
    
    assert session.initiator_id == "initiator"
    assert session.responder_id == "responder"
    assert session.state == "pending"
    assert session.ratchet_state_blob == valid_b64
    
    # Check DB adds
    assert mock_db.add.call_count == 2 # Session + Event
    
def test_accept_session(session_manager, mock_db):
    session_id = "sess-1"
    existing = A2ASession(
        session_id=session_id,
        state="pending",
        initiator_id="initiator",
        responder_id="responder",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    mock_db.query.return_value.filter.return_value.first.return_value = existing
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None # No prev events found
    
    req = SessionAcceptRequest(
        ratchet_state_blob_b64u=valid_b64,
        ratchet_state_digest=valid_digest
    )
    
    session_manager.accept_session(session_id, "responder", req)
    
    assert existing.state == "active"
    assert existing.ratchet_state_blob == valid_b64
    
def test_accept_session_wrong_responder(session_manager, mock_db):
    session_id = "sess-1"
    existing = A2ASession(
        session_id=session_id,
        state="pending",
        initiator_id="initiator",
        responder_id="responder"
    )
    mock_db.query.return_value.filter.return_value.first.return_value = existing
    
    req = SessionAcceptRequest(
        ratchet_state_blob_b64u=valid_b64,
        ratchet_state_digest=valid_digest
    )
    
    with pytest.raises(PermissionError):
        session_manager.accept_session(session_id, "wrong-responder", req)

def test_rotate_session(session_manager, mock_db):
    session_id = "sess-1"
    existing = A2ASession(
        session_id=session_id,
        state="active",
        initiator_id="initiator",
        responder_id="responder",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    mock_db.query.return_value.filter.return_value.first.return_value = existing
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    
    req = SessionRotateRequest(
        ratchet_state_blob_b64u=valid_b64,
        ratchet_state_digest=valid_digest
    )
    
    session_manager.rotate_session(session_id, "initiator", req)
    
    assert existing.ratchet_state_blob == valid_b64

def test_rotate_session_invalid_state(session_manager, mock_db):
    session_id = "sess-1"
    existing = A2ASession(
        session_id=session_id,
        state="pending", # Cannot rotate pending
        initiator_id="initiator",
        responder_id="responder"
    )
    mock_db.query.return_value.filter.return_value.first.return_value = existing
    
    req = SessionRotateRequest(
        ratchet_state_blob_b64u=valid_b64,
        ratchet_state_digest=valid_digest
    )
    
    with pytest.raises(ValueError, match="A2A_SESSION_STATE_INVALID"):
        session_manager.rotate_session(session_id, "initiator", req)
