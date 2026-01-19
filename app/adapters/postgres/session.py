"""Postgres Session Management with Read/Write Splitting."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import logging

logger = logging.getLogger(__name__)

DATABASE_WRITE_URL = os.getenv("DATABASE_WRITE_URL") or os.getenv("DATABASE_URL")
DATABASE_READ_URL = os.getenv("DATABASE_READ_URL") or DATABASE_WRITE_URL

write_engine = None
read_engine = None
SessionWrite = None
SessionRead = None

if DATABASE_WRITE_URL:
    try:
        write_engine = create_engine(DATABASE_WRITE_URL, pool_pre_ping=True)
        SessionWrite = sessionmaker(autocommit=False, autoflush=False, bind=write_engine)
        logger.info("Initialized Write Database Engine")
    except Exception as e:
        logger.error(f"Failed to create Write DB engine: {e}")

if DATABASE_READ_URL:
    try:
        read_engine = create_engine(DATABASE_READ_URL, pool_pre_ping=True)
        SessionRead = sessionmaker(autocommit=False, autoflush=False, bind=read_engine)
        logger.info("Initialized Read Database Engine")
    except Exception as e:
        logger.error(f"Failed to create Read DB engine: {e}")
        # Fallback to write engine for reads if read engine fails (optional, usually better to fail or warn)
        if write_engine:
            logger.warning("Falling back to Write Engine for Reads")
            SessionRead = SessionWrite
else:
    # Fallback if no read URL provided
    SessionRead = SessionWrite

if not write_engine:
    logger.warning("No Write Database configured. Postgres disabled.")

SessionLocal = SessionWrite

def get_db():
    """Deprecated: Use get_write_db or get_read_db."""
    return get_write_db()


def get_write_db():
    """Dependency for Write operations (Primary)."""
    if SessionWrite is None:
        if os.getenv("MODE", "dev").lower() == "dev" or os.getenv("DEV_MODE", "false").lower() == "true":
            yield None
            return
        raise RuntimeError("Write Database not configured")
    db = SessionWrite()
    try:
        yield db
    finally:
        db.close()


def get_read_db():
    """Dependency for Read operations (Replica)."""
    if SessionRead is None:
         # Fallback to write if read is critically missing (should be handled by init logic)
        if SessionWrite:
             db = SessionWrite()
             try:
                yield db
             finally:
                db.close()
             return

        if os.getenv("MODE", "dev").lower() == "dev" or os.getenv("DEV_MODE", "false").lower() == "true":
            yield None
            return
        raise RuntimeError("Read Database not configured")
    
    db = SessionRead()
    try:
        yield db
    finally:
        db.close()

