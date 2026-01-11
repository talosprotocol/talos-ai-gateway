import pytest
from app.main import app

@pytest.fixture(autouse=True)
def clear_overrides():
    """Automatically clear FastAPI dependency overrides before each test."""
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}
