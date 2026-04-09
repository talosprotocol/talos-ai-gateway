import os
import pytest
from unittest.mock import patch
from app.dependencies import get_capability_validator
from app.core.config import settings as core_settings
from app.settings import settings as a2a_settings

def test_capability_validator_uses_tga_prefixed_key():
    """Test that the validator uses the TGA_SUPERVISOR_PUBLIC_KEY from settings."""
    test_key = "dummy-public-key-for-test"
    
    # Mock settings to have the test key
    with patch.object(core_settings, "TGA_SUPERVISOR_PUBLIC_KEY", test_key):
        validator = get_capability_validator()
        assert validator.public_key == test_key

def test_capability_validator_fallback_to_dev_placeholder():
    """Test that the validator falls back to dev-placeholder when the key is missing."""
    # Mock settings to be None
    with patch.object(core_settings, "TGA_SUPERVISOR_PUBLIC_KEY", None):
        # Also ensure env var is not set
        with patch.dict(os.environ, {}, clear=False):
            if "SUPERVISOR_PUBLIC_KEY" in os.environ:
                 del os.environ["SUPERVISOR_PUBLIC_KEY"]
            if "TGA_SUPERVISOR_PUBLIC_KEY" in os.environ:
                 del os.environ["TGA_SUPERVISOR_PUBLIC_KEY"]
            
            validator = get_capability_validator()
            assert validator.public_key == "dev-placeholder"

def test_settings_consistency():
    """Test that both settings modules pick up TGA_SUPERVISOR_PUBLIC_KEY from the environment."""
    test_key = "test-key-123"
    with patch.dict(os.environ, {"TGA_SUPERVISOR_PUBLIC_KEY": test_key}):
        # We re-instantiate the Settings classes to pick up the mocked environment
        from app.core.config import Settings as CoreSettings
        from app.settings import Settings as A2ASettings
        
        core_s = CoreSettings()
        a2a_s = A2ASettings()
        
        # Check core settings
        assert core_s.TGA_SUPERVISOR_PUBLIC_KEY == test_key
        
        # Check a2a settings
        assert hasattr(a2a_s, "TGA_SUPERVISOR_PUBLIC_KEY")
        assert a2a_s.TGA_SUPERVISOR_PUBLIC_KEY == test_key
        
        # Ensure the old inconsistent field is gone (optional, but good for unification)
        assert not hasattr(a2a_s, "supervisor_public_key")
