import os
from pydantic_settings import BaseSettings  # type: ignore
from pydantic import PostgresDsn
from typing import Optional

class Settings(BaseSettings):
    # Core
    MODE: str = "dev"  # dev, prod
    REGION_ID: str = "local"
    DEV_MODE: bool = False
    
    # Database (Split for Multi-Region)
    DATABASE_WRITE_URL: PostgresDsn = "postgresql://talos:talos@localhost:5432/talos"  # type: ignore
    DATABASE_READ_URL: Optional[PostgresDsn] = None
    
    # Logic Gates
    RUN_MIGRATIONS: bool = True
    READ_FALLBACK_ENABLED: bool = True
    
    # Circuit Breaker (Phase 12)
    READ_FAILURE_THRESHOLD: int = 3
    CIRCUIT_OPEN_DURATION_SECONDS: int = 30
    
    # Database Timeouts (Phase 12)
    DATABASE_READ_TIMEOUT_MS: int = 1000  # Fast fail for replica reads
    DATABASE_CONNECT_TIMEOUT_MS: int = 3000
    
    # Cache
    REDIS_URL: str = "redis://localhost:6379/0"
    OLLAMA_URL: str = "http://localhost:11434"
    
    # Observability
    TRACING_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    
    # Security
    MASTER_KEY: str = "insecure-default-key-for-dev-only-do-not-use-in-prod"
    TGA_SUPERVISOR_PUBLIC_KEY: Optional[str] = None

    # Multi-Region Safety (Phase 12)
    # List of endpoint IDs (or paths) allowed to use read replicas
    # Default to a safe set of non-critical read endpoints
    REPLICA_READ_ALLOWLIST: list[str] = [
        "/admin/v1/mcp/servers",
        "/admin/v1/mcp/policies",
        "/admin/v1/telemetry/stats",
        "/admin/v1/audit/stats",
        "/health/ready"
    ]

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"

# Logic for Read URL Default
settings = Settings()
if not settings.DATABASE_READ_URL:
    settings.DATABASE_READ_URL = settings.DATABASE_WRITE_URL
