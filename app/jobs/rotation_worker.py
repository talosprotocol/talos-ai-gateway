import asyncio
import logging
import os
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.adapters.postgres.models import RotationOperation
from app.adapters.postgres.secret_store import PostgresSecretStore
from app.adapters.postgres.stores import PostgresRotationStore
from app.domain.secrets.rotation import RotationService
from app.dependencies import get_write_db, get_kek_provider

logger = logging.getLogger(__name__)

# Deterministic lock ID: derived from "talos.secrets.rotation"
ROTATION_LOCK_ID = 8273649827364982

async def rotation_worker(shutdown_event: asyncio.Event):
    """Background worker for secret rotation."""
    logger.info("Starting rotation worker...")
    
    while not shutdown_event.is_set():
        try:
            # Get a fresh DB session
            db_gen = get_write_db()
            db = next(db_gen)
            
            try:
                # Try to acquire the leader lock
                # We use pg_try_advisory_lock to avoid blocking the worker thread entirely
                result = db.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"), 
                    {"lock_id": ROTATION_LOCK_ID}
                ).scalar()
                
                if result:
                    logger.debug("Rotation worker: Acquired leader lock.")
                    try:
                        await _process_pending_operations(db, shutdown_event)
                    finally:
                        # Ensure we always release the lock if we acquired it
                        db.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"), 
                            {"lock_id": ROTATION_LOCK_ID}
                        )
                else:
                    logger.debug("Rotation worker: Leader lock held by another instance.")
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error in rotation worker loop: {e}", exc_info=True)
            
        # Wait before the next poll cycle
        await asyncio.sleep(int(os.getenv("ROTATION_POLL_INTERVAL_SEC", "30")))

async def _process_pending_operations(db: Session, shutdown_event: asyncio.Event):
    """Checks for and processes running rotation operations."""
    # Find the oldest running operation
    op = db.query(RotationOperation).filter(
        RotationOperation.status == "running"
    ).order_by(RotationOperation.started_at).first()
    
    if not op:
        return

    logger.info(f"Resuming/Starting rotation operation: {op.id} (target: {op.target_kek_id})")
    
    # Initialize domain services
    # We pass the same DB session to ensure consistency within the worker cycle
    kek_provider = get_kek_provider()
    secret_store = PostgresSecretStore(db, kek_provider)
    rotation_service = RotationService(secret_store, kek_provider)
    
    # Audit: Job Started
    _emit_audit(db, "rotation_started", "secret_batch", op.id, {
        "target_kek_id": op.target_kek_id,
        "resume_cursor": op.cursor
    })
    
    batch_size = int(os.getenv("ROTATION_BATCH_SIZE", "100"))
    qps = float(os.getenv("ROTATION_QPS", "5.0")) # Conservative default
    sleep_interval = 1.0 / qps if qps > 0 else 1.0
    
    try:
        while not shutdown_event.is_set():
            start_time = datetime.now(timezone.utc)
            rotated, last_cursor, scanned, failed = rotation_service.rotate_batch(batch_size, op.cursor)
            duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            # Update operation progress
            op.cursor = last_cursor
            # SQLAlchemy needs notification of JSON mutation if not using MutableDict
            stats = dict(op.stats or {})
            stats["scanned"] = stats.get("scanned", 0) + scanned
            stats["rotated"] = stats.get("rotated", 0) + rotated
            stats["failed"] = stats.get("failed", 0) + failed
            op.stats = stats
            
            # Metrics (Log-based placeholder for Prometheus)
            logger.info(
                f"METRIC: secrets_rotated={rotated} secrets_failed={failed} "
                f"scanned={scanned} duration_ms={duration_ms:.2f} op_id={op.id}"
            )
            
            if scanned < batch_size:
                # No more secrets to process
                op.status = "completed"
                op.completed_at = datetime.now(timezone.utc)
                db.commit()
                
                # Audit: Job Completed
                _emit_audit(db, "rotation_completed", "secret_batch", op.id, stats)
                
                logger.info(f"Completed rotation operation: {op.id}. Rotated {stats['rotated']} secrets.")
                break
            
            # Save progress and throttle
            db.commit()
            await asyncio.sleep(sleep_interval)
            
    except Exception as e:
        logger.error(f"Rotation operation {op.id} failed: {e}")
        op.status = "failed"
        op.last_error = str(e)
        db.commit()
        
        # Audit: Job Failed
        _emit_audit(db, "rotation_failed", "secret_batch", op.id, {"error": str(e)})

def _emit_audit(db: Session, action: str, resource_type: str, resource_id: str, details: dict):
    from app.adapters.postgres.models import AuditEvent
    from uuid6 import uuid7
    event = AuditEvent(
        event_id=str(uuid7()),
        timestamp=datetime.now(timezone.utc),
        principal_id="system/rotation_worker",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        status="success" if "failed" not in action else "error"
    )
    db.add(event)
    db.commit()
