"""Talos AI Gateway - Main Application."""
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.public_ai import router as ai_router
from app.api.public_mcp import router as mcp_router
from app.api.admin import router as admin_router
from app.dashboard import router as dashboard_router
from app.api.talos_protocol import router as protocol_router
from app.api.a2a import routes as a2a_routes
from app.api.a2a import agent_card

import asyncio
from app.jobs.retention import retention_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    shutdown_event = asyncio.Event()
    worker_task = asyncio.create_task(retention_worker(shutdown_event))
    yield
    # Shutdown
    shutdown_event.set()
    try:
        await asyncio.wait_for(worker_task, timeout=5.0)
    except asyncio.TimeoutError:
        pass # Force kill if stuck

app = FastAPI(
    title="Talos AI Gateway",
    description="Unified LLM Inference + MCP Tool Gateway",
    version="0.1.0",
    lifespan=lifespan
)

from fastapi.responses import JSONResponse
from fastapi import Request, HTTPException

@app.exception_handler(HTTPException)
async def a2a_http_exception_handler(request: Request, exc: HTTPException):
    # Specialized error handling for A2A routes to ensure top-level 'error' key
    if request.url.path.startswith("/a2a/"):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                headers=exc.headers
            )
    
    # Default FastAPI handler for everything else
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers
    )

# Health check
@app.get("/health")
def health():
    return {"status": "ok"}

# Mount routers
app.include_router(dashboard_router.router, prefix="", tags=["Dashboard"])
app.include_router(protocol_router.router, prefix="", tags=["Protocol"])
app.include_router(ai_router.router, prefix="/v1", tags=["LLM"])
app.include_router(mcp_router.router, prefix="/v1/mcp", tags=["MCP"])
app.include_router(admin_router.router, prefix="/admin/v1", tags=["Admin"])
app.include_router(a2a_routes.router, prefix="/a2a/v1", tags=["A2A"])
app.include_router(agent_card.router, prefix="", tags=["Discovery"])

