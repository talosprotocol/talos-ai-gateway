import pytest
from app.settings import settings

def test_a2a_v1_strict_mode_enabled():
    """Verify that settings default to v1 strict mode."""
    assert settings.a2a_protocol_mode == "v1"

@pytest.mark.asyncio
async def test_a2a_v1_strict_methods_only():
    """
    In a real integration test, we would verify that legacy RPC methods 
    return 404 or Method Not Found in strict mode.
    """
    # This is a unit test for settings alignment
    from app.api.a2a_v1.service import STRICT_V1_METHODS, ALLOWED_V1_METHODS
    
    allowed = STRICT_V1_METHODS if settings.a2a_protocol_mode == "v1" else ALLOWED_V1_METHODS
    
    # In strict mode, we should NOT have legacy methods like 'agent/getLegacyCard'
    if settings.a2a_protocol_mode == "v1":
        assert "agent/getLegacyCard" not in allowed
