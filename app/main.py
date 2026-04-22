"""Talos AI Gateway - Main Application."""
import asyncio
import logging
import os
import sys
import traceback
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from alembic import command
from alembic.config import Config
from app.adapters.redis.client import close_redis_client
from app.api.a2a import agent_card, routes as a2a_routes
from app.api.a2a_v1 import router as a2a_v1_router
from app.api.admin import router as admin_router
from app.api.public_ai import router as ai_router
from app.api.public_mcp import router as mcp_router
from app.dashboard import router as dashboard_router
from app.dependencies import (
    get_policy_engine_async,
)
from app.domain.mcp.classifier import init_tool_classifier
from app.jobs.budget_cleanup import budget_cleanup_worker
from app.jobs.retention import retention_worker
from app.jobs.revocation import revocation_worker
from app.jobs.rotation_worker import rotation_worker
from app.logging_hardening import setup_logging_redaction
from app.middleware.audit import TalosAuditMiddleware
from app.middleware.observability import RegionHeaderMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.shutdown_gate import ShutdownGateMiddleware
from app.observability.tracing import TalosSpanProcessor
from app.routers import health as health_router
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

# Initialize logging redaction filters early
setup_logging_redaction()

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


def _background_worker_names() -> list[str]:
    names = ["retention"]
    use_json_stores = _env_flag("USE_JSON_STORES")

    if not use_json_stores:
        names.append("rotation")

    rate_limit_backend = os.getenv("RATE_LIMIT_BACKEND", "").lower()
    redis_enabled = False
    if not use_json_stores:
        if rate_limit_backend:
            redis_enabled = rate_limit_backend == "redis"
        else:
            redis_enabled = bool(os.getenv("REDIS_URL"))

    if redis_enabled:
        names.extend(["revocation", "budget_cleanup"])

    return names


def _start_background_workers(shutdown_event: asyncio.Event) -> list[asyncio.Task]:
    task_factories = {
        "retention": retention_worker,
        "revocation": revocation_worker,
        "rotation": rotation_worker,
        "budget_cleanup": budget_cleanup_worker,
    }
    enabled = _background_worker_names()
    tasks = []
    for name, factory in task_factories.items():
        if name in enabled:
            tasks.append(asyncio.create_task(factory(shutdown_event)))
        else:
            logger.info("Skipping background worker %s for current runtime mode", name)
    return tasks

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    shutdown_event = asyncio.Event()
    worker_tasks = []

    # Phase 12: Migrations
    run_mig = os.getenv("RUN_MIGRATIONS", "false").lower()
    print(f"DEBUG: RUN_MIGRATIONS={run_mig}")
    if run_mig == "true":
        print("DEBUG: Starting Migrations...")
        logger.info("Running DB Migrations...")
        try:
            alembic_cfg = Config("alembic.ini")
            # Override URL with Write URL
            url = os.getenv("DATABASE_WRITE_URL")
            if url:
                alembic_cfg.set_main_option("sqlalchemy.url", str(url))
                os.environ["DATABASE_URL"] = str(url)
            
            # Run in thread executor to avoid blocking loop too long?
            # Or just block since it's startup
            await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
            logger.info("Migrations complete.")
        except Exception as e:
            logger.error(f"Migration Failed: {e}")
            # If migration fails, we should probably crash
            sys.exit(1)
            
    # Start background workers after migrations to avoid DB deadlocks
    worker_tasks = _start_background_workers(shutdown_event)
    
    # Phase 7: policy initialization for dependency-based auth paths
    try:
        logger.info("Initializing authorization policy engine...")
        from app.dependencies import _write_engine, USE_JSON_STORES
        from sqlalchemy.orm import Session
        from app.adapters.postgres.stores import PostgresRbacStore
        from app.adapters.json_store.stores import JsonRbacStore

        with Session(_write_engine) as db:
            if USE_JSON_STORES:
                rbac_store = JsonRbacStore()
            else:
                rbac_store = PostgresRbacStore(db)
            await get_policy_engine_async(rbac_store)
    except Exception as e:
        logger.error(f"Authorization initialization failed: {e}")
        shutdown_event.set()
        sys.exit(1)

    # Phase 9.2: Tool Classifier Initialization
    try:
        registry_path = os.getenv("TOOL_REGISTRY_PATH", "../contracts/artifacts")
        # Ensure absolute path
        if not os.path.isabs(registry_path):
             registry_path = os.path.abspath(registry_path)
             
        logger.info(f"Initializing Tool Classifier from {registry_path}...")
        init_tool_classifier(registry_dir=registry_path, env=os.getenv("MODE", "dev"))
    except Exception as e:
        logger.error(f"Tool Classifier Init Failed: {e}")
        # In PROD this should be fatal, but for now we log
        if os.getenv("MODE") == "prod":
            sys.exit(1)

    try:
        # Phase 11.4 Startup Checks (Normative)
        mode = os.getenv("MODE", "dev").lower()
        if mode == "prod":
            # 1. Rate Limiting Checks
            if os.getenv("RATE_LIMIT_ENABLED", "false").lower() == "true":
                backend = os.getenv("RATE_LIMIT_BACKEND", "memory").lower()
                if backend != "redis":
                    raise RuntimeError("In PROD, RATE_LIMIT_BACKEND must be 'redis'")
                
                redis_url = os.getenv("REDIS_URL")
                if not redis_url:
                    raise RuntimeError("In PROD, REDIS_URL must be present when rate limiting is enabled")
                    
                # Verify connectivity
                from app.adapters.redis.client import get_redis_client
                logger.info("Verifying Redis connectivity for PROD startup...")
                r = await get_redis_client()
                # Cast or ignore for mypy if it's confused about Awaitable[bool] | bool
                from typing import cast, Awaitable
                await cast(Awaitable[bool], r.ping())
                logger.info("Redis connectivity verified.")

            # 2. Tracing Checks
            # Spec says: If TRACING_ENABLED=true
            if os.getenv("TRACING_ENABLED", "false").lower() == "true":
                if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
                    raise RuntimeError("In PROD, OTEL_EXPORTER_OTLP_ENDPOINT must be present when tracing is enabled")
                    
    except RuntimeError as e:
        print(f"CRITICAL STARTUP ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"CRITICAL STARTUP ERROR (Unexpected): {e}")
        sys.exit(1)
        
    yield
    # Shutdown
    shutdown_event.set()
    logger.info("Initiating graceful shutdown...")
    ShutdownGateMiddleware.set_shutting_down(True)
    
    # Close Redis connections if any (via dependency cache or explicit close)
    await close_redis_client()
    
    # Cancel background tasks (Optional: worker handles shutdown_event)
    for task in worker_tasks:
        if not task.done():
            task.cancel()
    logger.info("Shutdown complete.")

app = FastAPI(
    title="Talos AI Gateway",
    description="Unified LLM Inference + MCP Tool Gateway",
    version="0.1.0",
    lifespan=lifespan
)

# OpenTelemetry Setup

# OpenTelemetry Setup
def setup_opentelemetry(app: FastAPI) -> None:
    # Provider
    os.getenv("OTEL_RESOURCE_ATTRIBUTES", "service.name=talos-gateway")
    provider = TracerProvider()
    
    # Exporter
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    else:
        # Fallback to Console (or NoOp if preferred in prod, but Console good for debug logs if verbose)
        # For production without OTLP, we might want simple logging or nothing.
        # Let's use nothing if not configured to avoid log spam, OR Console if Debug.
        if os.getenv("DEV_MODE") == "true":
             processor = BatchSpanProcessor(ConsoleSpanExporter())
        else:
             processor = None

    if processor:
        # Wrap with Redaction Processor
        redacted_processor = TalosSpanProcessor(processor)
        provider.add_span_processor(redacted_processor)
    
    trace.set_tracer_provider(provider)
    
    # Instrument FastAPI (auto-generates spans for requests)
    # Exclude health checks from tracing to reduce noise
    FastAPIInstrumentor.instrument_app(
        app, 
        tracer_provider=provider, 
        excluded_urls="health/*"
    )
    
    # Instrument SQLAlchemy (auto-generates spans for DB queries)
    # Normative: statement capture MUST be disabled
    SQLAlchemyInstrumentor().instrument(
        tracer_provider=provider, 
        enable_commenter=True,
        db_statement_enabled=False
    )

app.add_middleware(RateLimitMiddleware)
app.add_middleware(TalosAuditMiddleware)
app.add_middleware(RegionHeaderMiddleware)

# setup_opentelemetry(app)


@app.exception_handler(HTTPException)
async def a2a_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # Specialized error handling for A2A routes
    if request.url.path.startswith("/a2a/"):
        # For A2A v1 RPC, we should ideally return JSON-RPC error bodies
        if "/v1/rpc" in request.url.path or request.url.path.endswith("/v1/"):
            # Try to extract JSON-RPC ID from request body if possible
            request_id = None
            try:
                body = await request.json()
                if isinstance(body, dict):
                    request_id = body.get("id")
            except Exception:
                pass

            # Map Talos error to JSON-RPC error
            rpc_error = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000, # Reserved for implementation-defined server-errors
                    "message": "Unauthorized" if exc.status_code in (401, 403) else "Internal Error",
                }
            }
            
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                inner = exc.detail["error"]
                rpc_error["error"]["data"] = {
                    "talos_code": inner.get("code"),
                    "details": inner.get("message") or inner.get("details")
                }
                if inner.get("code") == "RBAC_DENIED":
                    rpc_error["error"]["message"] = "Permission denied"
                elif inner.get("code") == "AUTH_INVALID":
                    rpc_error["error"]["message"] = "Unauthorized"
            else:
                rpc_error["error"]["data"] = {"details": str(exc.detail)}

            return JSONResponse(
                status_code=exc.status_code,
                content=rpc_error,
                headers=exc.headers
            )

        # Legacy/Other A2A routes
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

# Mount routers
app.include_router(dashboard_router.router, prefix="", tags=["Dashboard"])
# from app.api.talos_protocol import router as protocol_router
# app.include_router(protocol_router.router, prefix="", tags=["Protocol"])
app.include_router(ai_router.router, prefix="/v1", tags=["LLM"])
app.include_router(mcp_router.router, prefix="/v1/mcp", tags=["MCP"])
app.include_router(admin_router.router, prefix="/admin/v1", tags=["Admin"])
app.include_router(a2a_v1_router.router, prefix="", tags=["A2A"])
app.include_router(a2a_routes.router, prefix="/a2a/v1", tags=["A2A"])
app.include_router(a2a_v1_router.router, prefix="/a2a/v1", tags=["A2A"])
app.include_router(agent_card.router, prefix="", tags=["Discovery"])

app.include_router(health_router.router, tags=["Health"])

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"DEBUG: {str(exc)}", "traceback": traceback.format_exc()})
