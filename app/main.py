"""Talos AI Gateway - Main Application."""
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.public_ai import router as ai_router
from app.api.public_mcp import router as mcp_router
from app.api.admin import router as admin_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown

app = FastAPI(
    title="Talos AI Gateway",
    description="Unified LLM Inference + MCP Tool Gateway",
    version="0.1.0",
    lifespan=lifespan
)

# Health check
@app.get("/health")
def health():
    return {"status": "ok"}

# Mount routers
app.include_router(ai_router.router, prefix="/v1", tags=["LLM"])
app.include_router(mcp_router.router, prefix="/mcp/v1", tags=["MCP"])
app.include_router(admin_router.router, prefix="/admin/v1", tags=["Admin"])
