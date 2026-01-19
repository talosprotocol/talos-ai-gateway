from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.dependencies import get_redis_client, get_read_db
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/health/live")
async def liveness():
    """Liveness probe: Service is running."""
    return {"status": "ok", "checks": {"api": "ok"}}

@router.get("/health/ready")
async def readiness(db: Session = Depends(get_read_db)):
    """Readiness probe: Dependencies connected."""
    health = {"status": "ok", "checks": {}}
    
    # 1. Check DB
    try:
        db.execute(text("SELECT 1"))
        health["checks"]["postgres"] = "ok"
    except Exception as e:
        logger.error(f"Health check failed (postgres): {e}")
        health["checks"]["postgres"] = "failed"
        health["status"] = "failed"

    # 2. Check Redis
    try:
        redis = await get_redis_client()
        await redis.ping()
        health["checks"]["redis"] = "ok"
    except Exception as e:
        logger.error(f"Health check failed (redis): {e}")
        health["checks"]["redis"] = "failed"
        health["status"] = "failed"
    
    if health["status"] == "failed":
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=health)
        
    return health

@router.get("/health/ollama")
async def health_ollama():
    """Proxy health check for Ollama downstream."""
    from app.core.config import settings
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                return {"status": "ok", "service": "ollama"}
            else:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail={"status": "failed", "upstream_code": resp.status_code})
    except Exception as e:
        logger.error(f"Ollama health check failed: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail={"status": "failed", "error": str(e)})
