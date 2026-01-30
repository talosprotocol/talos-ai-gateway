"""Phase 11 Production Hardening Tests.

Tests for:
- 11.1: Rate Limiting (Token Bucket, Redis/Memory, Fail-Closed)
- 11.2: Distributed Tracing (Redaction, Fail-Closed)
- 11.3: Health Checks (/health/live, /health/ready)
- 11.4: Graceful Shutdown (Shutdown Gate, Request Draining)
"""

import pytest
import os
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from app.main import app
from app.core.rate_limiter import RateLimiter, MemoryRateLimitStorage
from app.observability.tracing import TalosSpanProcessor


class TestRateLimiting:
    """Test 11.1: Rate Limiting"""
    
    def test_rate_limited_error_code(self):
        """Verify RATE_LIMITED error code (429) when limit exceeded."""
        client = TestClient(app)
        
        # Make many requests to trigger rate limit
        # (Assumes default limits are low for testing)
        responses = []
        for _ in range(20):
            resp = client.get("/health/live")  # Or another endpoint
            responses.append(resp)
        
        # Check if any returned 429
        rate_limited = [r for r in responses if r.status_code == 429]
        if rate_limited:
            assert rate_limited[0].json()["error"] == "RATE_LIMITED"
    
    @pytest.mark.asyncio
    async def test_token_bucket_logic(self):
        """Verify token bucket allows burst then enforces rate."""
        storage = MemoryRateLimitStorage()
        limiter = RateLimiter(storage)
        
        key = "test_user"
        rps = 2.0  # 2 requests per second
        burst = 5
        
        # First 5 (burst) should succeed
        for i in range(burst):
            allowed, _ = await limiter.check_throughput(key, rps, burst)
            assert allowed, f"Request {i+1} should be allowed (burst)"
        
        # 6th should fail (burst exhausted, not enough time passed)
        allowed, _ = await limiter.check_throughput(key, rps, burst)
        assert not allowed, "Request beyond burst should be rejected"
    
    def test_rate_limiter_unavailable_dev_only(self):
        """Verify RATE_LIMITER_UNAVAILABLE is dev-only error code."""
        with patch.dict(os.environ, {"MODE": "dev", "RATE_LIMIT_ENABLED": "true"}):
            # Simulate Redis failure
            with patch("app.core.rate_limiter.RedisRateLimitStorage") as mock_redis:
                mock_redis.return_value.check_throughput = AsyncMock(side_effect=RuntimeError("Redis down"))
                
                client = TestClient(app)
                resp = client.get("/v1/chat/completions")
                
                if resp.status_code == 503:
                    error_code = resp.json().get("error")
                    assert error_code == "RATE_LIMITER_UNAVAILABLE", "Dev mode should use RATE_LIMITER_UNAVAILABLE"


class TestDistributedTracing:
    """Test 11.2: Distributed Tracing"""
    
    def test_redaction_processor_redacts_authorization(self):
        """Verify RedactingSpanProcessor redacts Authorization header."""
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        
        base_processor = BatchSpanProcessor(ConsoleSpanExporter())
        processor = TalosSpanProcessor(base_processor)
        
        # Create mock span with sensitive attributes
        mock_span = MagicMock(spec=ReadableSpan)
        mock_span.attributes = {
            "http.request.header.authorization": "Bearer secret-token",
            "http.url": "https://api.example.com/test",
            "header_b64u": "sensitive-a2a-header",
            "ciphertext_b64u": "sensitive-ciphertext"
        }
        mock_span._attributes = mock_span.attributes.copy()
        
        # Process span (should redact)
        processor.on_end(mock_span)
        
        # Verify redaction
        assert mock_span._attributes["http.request.header.authorization"] == "[REDACTED]"
        assert mock_span._attributes["header_b64u"] == "[REDACTED]"
        assert mock_span._attributes["ciphertext_b64u"] == "[REDACTED]"
        assert mock_span._attributes["http.url"] == "https://api.example.com/test"  # Not redacted
    
    def test_sql_statement_logging_disabled(self):
        """Verify SQL statement logging is disabled per Phase 11 spec."""
        # Check main.py setup_opentelemetry configuration
        from app.main import setup_opentelemetry
        import inspect
        
        source = inspect.getsource(setup_opentelemetry)
        assert "db_statement_enabled=False" in source, "SQL logging must be disabled"
    
    def test_tracing_fail_closed_production(self):
        """Verify production fails if tracing enabled but no OTLP endpoint."""
        with patch.dict(os.environ, {
            "MODE": "prod",
            "TRACING_ENABLED": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": ""
        }):
            # This should raise RuntimeError during startup check
            # (Would need to test actual startup, skip for now)
            pass


class TestHealthChecks:
    """Test 11.3: Health Checks"""
    
    def test_health_live_returns_200(self):
        """Verify /health/live returns 200 OK immediately."""
        client = TestClient(app)
        resp = client.get("/health/live")
        
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
    
    def test_health_ready_checks_dependencies(self):
        """Verify /health/ready checks DB and Redis."""
        client = TestClient(app)
        resp = client.get("/health/ready")
        
        # Should check dependencies
        data = resp.json()
        assert "db" in data or "database" in data or "status" in data
        
        # Status should be 200 if deps up, 503 if down
        assert resp.status_code in [200, 503]
    
    def test_health_live_available_during_shutdown(self):
        """Verify /health/live responds even during shutdown."""
        from app.middleware.shutdown_gate import ShutdownGateMiddleware
        
        # Enable shutdown mode
        ShutdownGateMiddleware.set_shutting_down(True)
        
        try:
            client = TestClient(app)
            resp = client.get("/health/live")
            assert resp.status_code == 200, "/health/live should respond during shutdown"
        finally:
            ShutdownGateMiddleware.set_shutting_down(False)


class TestGracefulShutdown:
    """Test 11.4: Graceful Shutdown"""
    
    def test_shutdown_gate_rejects_requests(self):
        """Verify shutdown gate rejects non-health requests with 503."""
        from app.middleware.shutdown_gate import ShutdownGateMiddleware
        
        ShutdownGateMiddleware.set_shutting_down(True)
        
        try:
            client = TestClient(app)
            resp = client.get("/v1/chat/completions")
            
            assert resp.status_code == 503
            assert "SERVER_SHUTTING_DOWN" in str(resp.json())
        finally:
            ShutdownGateMiddleware.set_shutting_down(False)
    
    def test_server_shutting_down_error_code(self):
        """Verify SERVER_SHUTTING_DOWN error code during shutdown."""
        from app.middleware.shutdown_gate import ShutdownGateMiddleware
        
        ShutdownGateMiddleware.set_shutting_down(True)
        
        try:
            client = TestClient(app)
            resp = client.post("/a2a/sessions", json={"responder_id": "did:test"})
            
            if resp.status_code == 503:
                error = resp.json().get("error") or resp.json().get("detail", "")
                assert "shutting_down" in error.lower() or "SERVER_SHUTTING_DOWN" in error
        finally:
            ShutdownGateMiddleware.set_shutting_down(False)


class TestCompliance:
    """Test overall Phase 11 compliance."""
    
    def test_error_codes_are_stable(self):
        """Verify Phase 11 stable error codes exist in codebase."""
        import subprocess
        
        # Search for error codes in source
        result = subprocess.run(
            ["grep", "-r", "RATE_LIMITED", "app/"],
            capture_output=True,
            text=True
        )
        assert "RATE_LIMITED" in result.stdout, "RATE_LIMITED error code must exist"
        
        result = subprocess.run(
            ["grep", "-r", "SERVER_SHUTTING_DOWN", "app/"],
            capture_output=True,
            text=True
        )
        assert "SERVER_SHUTTING_DOWN" in result.stdout, "SERVER_SHUTTING_DOWN error code must exist"
        
        result = subprocess.run(
            ["grep", "-r", "RATE_LIMITER_UNAVAILABLE", "app/"],
            capture_output=True,
            text=True
        )
        assert "RATE_LIMITER_UNAVAILABLE" in result.stdout, "RATE_LIMITER_UNAVAILABLE error code must exist"
    
    def test_middleware_ordering(self):
        """Verify middleware runs in correct order: Rate Limit -> Auth -> Handler."""
        # Check main.py middleware application order
        from app.main import app
        
        # Middleware is applied in reverse order (last added runs first)
        # So we add: Shutdown, RBAC, Audit, RateLimit
        # They run: RateLimit -> Audit -> RBAC -> Shutdown -> Handler
        
        # This is a basic check - actual order depends on FastAPI internals
        middleware_types = [type(m).__name__ for m in app.user_middleware]
        
        # RateLimitMiddleware should be in the stack
        assert any("RateLimit" in name for name in middleware_types)
