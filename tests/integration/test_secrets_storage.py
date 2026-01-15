import pytest
import os
from sqlalchemy.orm import sessionmaker
from app.adapters.postgres.session import engine
from app.adapters.postgres.secret_store import PostgresSecretStore
from app.domain.secrets.models import EncryptedEnvelope
from app.dependencies import get_kek_provider

from sqlalchemy import create_engine
from app.adapters.postgres.models import Base

@pytest.fixture(scope="module")
def test_engine():
    # Use in-memory SQLite for unit/integration logic verification
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return engine

@pytest.fixture
def db_session(test_engine):
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()

@pytest.fixture
def secret_store(db_session):
    return PostgresSecretStore(db_session, get_kek_provider())

def test_db_encryption_roundtrip(secret_store, db_session):
    secret_name = "test-integration-secret"
    secret_value = "top-secret-12345"
    
    # 1. Set secret
    secret_store.set_secret(secret_name, secret_value)
    
    # 2. Verify in DB (direct query)
    from app.adapters.postgres.models import Secret
    obj = db_session.query(Secret).filter(Secret.name == secret_name).first()
    assert obj is not None
    assert obj.ciphertext != secret_value
    assert len(obj.nonce) == 24 # 12 bytes hex
    assert len(obj.tag) == 32    # 16 bytes hex
    
    # 3. Decrypt through store
    recovered = secret_store.get_secret_value(secret_name)
    assert recovered == secret_value
    
    # 4. Cleanup
    secret_store.delete_secret(secret_name)
    assert secret_store.get_secret_value(secret_name) is None

def test_rotation_service(secret_store, db_session):
    from app.domain.secrets.rotation import RotationService
    from app.adapters.postgres.models import Secret
    rotator = RotationService(secret_store, get_kek_provider())
    
    name = "rotation-test"
    secret_store.set_secret(name, "initial-value")
    
    # Verify it can be rotated (even with same KEK, it should re-wrap with new IV)
    obj_before = db_session.query(Secret).filter(Secret.name == name).first()
    iv_before = obj_before.nonce
    
    count = rotator.rotate_all()
    assert count >= 1
    
    db_session.refresh(obj_before)
    assert obj_before.nonce != iv_before
    assert secret_store.get_secret_value(name) == "initial-value"
    
    secret_store.delete_secret(name)
