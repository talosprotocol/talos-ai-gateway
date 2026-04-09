import os

import pytest

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DEV_MODE"] = "true"
os.environ["MODE"] = "dev"
os.environ["USE_JSON_STORES"] = "true"
os.environ["TALOS_KEY_PEPPER"] = "test-pepper"
os.environ["TALOS_PEPPER_ID"] = "p1"

from app.main import app

@pytest.fixture(autouse=True)
def clear_overrides():
    """Automatically clear FastAPI dependency overrides before each test."""
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}
