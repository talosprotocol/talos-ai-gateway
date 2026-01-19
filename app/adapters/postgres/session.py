"""Postgres Session Management."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DATABASE_WRITE_URL")

engine = None
SessionLocal = None

if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    except Exception as e:
        logger.error(f"Failed to create DB engine: {e}")
else:
    logger.warning("DATABASE_URL not set. Postgres disabled.")


def get_db():
    """Dependency for FastAPI."""
    if SessionLocal is None:
        # In dev mode, we might not have a DB configured. Yield None instead of crashing.
        if os.getenv("MODE", "dev").lower() == "dev" or os.getenv("DEV_MODE", "false").lower() == "true":
            yield None
            return
        raise RuntimeError("Database not configured")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
