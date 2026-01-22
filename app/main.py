"""Talos AI Gateway - Main Application."""
from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)

from app.api.public_ai import router as ai_router
from app.api.public_mcp import router as mcp_router
from app.api.admin import router as admin_router
from app.dashboard import router as dashboard_router
# from app.api.talos_protocol import router as protocol_router
from app.api.a2a import routes as a2a_routes
from app.api.a2a import agent_card

import asyncio
from app.jobs.retention import retention_worker
from app.jobs.revocation import revocation_worker
from app.jobs.rotation_worker import rotation_worker
from app.jobs.budget_cleanup import budget_cleanup_worker
from app.logging_hardening import setup_logging_redaction

# Initialize logging redaction filters early
setup_logging_redaction()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    shutdown_event = asyncio.Event()
    worker_task = asyncio.create_task(retention_worker(shutdown_event))
    revoc_task = asyncio.create_task(revocation_worker(shutdown_event))
    rotation_task = asyncio.create_task(rotation_worker(shutdown_event))
    budget_cleanup_task = asyncio.create_task(budget_cleanup_worker(shutdown_event))

    # Phase 12: Migrations
    import os
    run_mig = os.getenv("RUN_MIGRATIONS", "false").lower()
    print(f"DEBUG: RUN_MIGRATIONS={run_mig}")
    if run_mig == "true":
        print("DEBUG: Starting Migrations...")
        logger.info("Running DB Migrations...")
        try:
            from alembic.config import Config
            from alembic import command
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
            import sys
            sys.exit(1)
    
    # Surface Completeness Gate
    from app.dependencies import get_surface_registry
    try:
        registry = get_surface_registry()
        # registry.verify_app_routes(app)
        
        # Phase 11.4 Startup Checks (Normative)
        import os
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
                # We need to await this
                r = await get_redis_client()
                await r.ping()
                logger.info("Redis connectivity verified.")

            # 2. Tracing Checks
            # Spec says: If TRACING_ENABLED=true
            if os.getenv("TRACING_ENABLED", "false").lower() == "true":
                if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
                    raise RuntimeError("In PROD, OTEL_EXPORTER_OTLP_ENDPOINT must be present when tracing is enabled")
                    
    except RuntimeError as e:
        import sys
        print(f"CRITICAL STARTUP ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        import sys
        print(f"CRITICAL STARTUP ERROR (Unexpected): {e}")
        sys.exit(1)
        
    yield
    # Shutdown
    shutdown_event.set()
    logger.info("Initiating graceful shutdown...")
    from app.middleware.shutdown_gate import ShutdownGateMiddleware
    ShutdownGateMiddleware.set_shutting_down(True)
    
    # Close Redis connections if any (via dependency cache or explicit close)
    from app.adapters.redis.client import close_redis_client
    await close_redis_client()
    
    # Cancel background tasks
    try:
        await asyncio.gather(
            asyncio.wait_for(worker_task, timeout=5.0),
            asyncio.wait_for(revoc_task, timeout=5.0),
            asyncio.wait_for(rotation_task, timeout=5.0),
            return_exceptions=True
        )
    except asyncio.TimeoutError:
        logger.warning("Background tasks shutdown timed out.")
        
    logger.info("Shutdown complete.")

app = FastAPI(
    title="Talos AI Gateway",
    description="Unified LLM Inference + MCP Tool Gateway",
    version="0.1.0",
    lifespan=lifespan
)

from app.middleware.audit import TalosAuditMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
import os

# OpenTelemetry Setup
def setup_opentelemetry(app: FastAPI):
    # Provider
    resource = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "service.name=talos-gateway")
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
        from app.observability.tracing import TalosSpanProcessor
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

from app.middleware.shutdown_gate import ShutdownGateMiddleware
app.add_middleware(ShutdownGateMiddleware)

from app.middleware.observability import RegionHeaderMiddleware
app.add_middleware(RegionHeaderMiddleware)

setup_opentelemetry(app)

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

# Mount routers
app.include_router(dashboard_router.router, prefix="", tags=["Dashboard"])
# from app.api.talos_protocol import router as protocol_router
# app.include_router(protocol_router.router, prefix="", tags=["Protocol"])
app.include_router(ai_router.router, prefix="/v1", tags=["LLM"])
app.include_router(mcp_router.router, prefix="/v1/mcp", tags=["MCP"])
app.include_router(admin_router.router, prefix="/admin/v1", tags=["Admin"])
app.include_router(a2a_routes.router, prefix="/a2a/v1", tags=["A2A"])
app.include_router(agent_card.router, prefix="", tags=["Discovery"])

from app.routers import health
app.include_router(health.router, tags=["Health"])

from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"DEBUG: {str(exc)}", "traceback": traceback.format_exc()})

