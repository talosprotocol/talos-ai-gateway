import os
import pytest
from app.domain.audit import AuditLogger
from app.dependencies import get_audit_logger
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True)
def reset_audit_singleton():
    """Reset the audit logger singleton before each test."""
    import app.dependencies
    app.dependencies._audit_logger_instance = None
    yield

def test_audit_logger_hmac_key_prod_fail():
    """Ensure AuditLogger fails if HMAC key is missing in production."""
    mock_sink = MagicMock()
    with patch.dict(os.environ, {"ENV": "production", "AUDIT_IP_HMAC_KEY": ""}):
        with pytest.raises(RuntimeError, match="AUDIT_IP_HMAC_KEY must be set in production"):
            AuditLogger(sink=mock_sink)

def test_audit_logger_hmac_key_too_short_prod_fail():
    """Ensure AuditLogger fails if HMAC key is too short in production."""
    mock_sink = MagicMock()
    with patch.dict(os.environ, {"ENV": "production", "AUDIT_IP_HMAC_KEY": "too-short"}):
        with pytest.raises(RuntimeError, match="AUDIT_IP_HMAC_KEY must be at least 32 characters in production"):
            AuditLogger(sink=mock_sink)

def test_audit_logger_hmac_key_mode_prod_fail():
    """Ensure AuditLogger fails if HMAC key is missing in MODE=prod."""
    mock_sink = MagicMock()
    with patch.dict(os.environ, {"MODE": "prod", "AUDIT_IP_HMAC_KEY": ""}):
        with pytest.raises(RuntimeError, match="AUDIT_IP_HMAC_KEY must be set in production"):
            AuditLogger(sink=mock_sink)

def test_audit_logger_hmac_key_dev_fallback():
    """Ensure AuditLogger has a fallback HMAC key in development."""
    with patch.dict(os.environ, {"ENV": "development", "AUDIT_IP_HMAC_KEY": ""}):
        logger = AuditLogger()
        assert logger.ip_hmac_key == "dev-ip-key-secret-32-chars-long-!!!"

def test_audit_sink_bootstrap_prod_fail_if_no_url():
    """Ensure get_audit_logger fails in production if AUDIT_SINK_URL is missing."""
    with patch.dict(os.environ, {"ENV": "production", "AUDIT_SINK_URL": "", "AUDIT_IP_HMAC_KEY": "secure-key-that-is-long-enough-1234567890"}):
        with pytest.raises(RuntimeError, match="AUDIT_SINK_URL must be set in production"):
            get_audit_logger()

def test_audit_sink_bootstrap_prod_success():
    """Ensure get_audit_logger succeeds in production with full config."""
    with patch.dict(os.environ, {
        "ENV": "production", 
        "AUDIT_SINK_URL": "http://audit-service", 
        "AUDIT_IP_HMAC_KEY": "secure-key-that-is-long-enough-1234567890"
    }):
        from app.domain.sink import HttpSink
        logger = get_audit_logger()
        assert isinstance(logger.sink, HttpSink)
        assert logger.sink.url == "http://audit-service/events"

def test_audit_sink_bootstrap_dev_fallback():
    """Ensure get_audit_logger falls back to StdOutSink in development."""
    with patch.dict(os.environ, {"ENV": "development", "AUDIT_SINK_URL": ""}):
        from app.domain.sink import StdOutSink
        logger = get_audit_logger()
        assert isinstance(logger.sink, StdOutSink)

def test_audit_logger_no_sink_prod_fail():
    """Ensure AuditLogger fails if no sink is provided in production."""
    with patch.dict(os.environ, {"ENV": "production"}):
        with pytest.raises(RuntimeError, match="Audit sink must be explicitly provided in production"):
            AuditLogger(sink=None)

@pytest.mark.asyncio
async def test_http_sink_uses_async_aiohttp_session(monkeypatch):
    """Ensure HttpSink does not block the event loop with synchronous requests."""
    from app.domain import sink as sink_module
    from app.domain.sink import HttpSink

    calls = []

    class FakeResponse:
        status = 202

        async def __aenter__(self):
            calls.append(("response_enter",))
            return self

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("response_exit",))

        async def text(self):
            return ""

    class FakeSession:
        def __init__(self, *, timeout):
            calls.append(("session_init", timeout.total))

        async def __aenter__(self):
            calls.append(("session_enter",))
            return self

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("session_exit",))

        def post(self, url, *, json, headers):
            calls.append(("post", url, json, headers))
            return FakeResponse()

    monkeypatch.setattr(sink_module.aiohttp, "ClientSession", FakeSession)

    await HttpSink("http://audit-service", "audit-key").emit({"event_id": "e-1"})

    assert ("session_init", 5.0) in calls
    assert (
        "post",
        "http://audit-service/events",
        {"event_id": "e-1"},
        {"Content-Type": "application/json", "Authorization": "Bearer audit-key"},
    ) in calls
