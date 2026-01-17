import os
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn
from typing import Optional

class Settings(BaseSettings):
    # Core
    MODE: str = "dev"  # dev, prod
    REGION_ID: str = "local"
    
    # Database (Split for Multi-Region)
    DATABASE_WRITE_URL: PostgresDsn = "postgresql://talos:talos@localhost:5432/talos"
    DATABASE_READ_URL: Optional[PostgresDsn] = None
    
    # Logic Gates
    RUN_MIGRATIONS: bool = True
    READ_FALLBACK_ENABLED: bool = True
    
    # Cache
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Observability
    TRACING_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    
    # Security
    MASTER_KEY: str = "insecure-default-key-for-dev-only-do-not-use-in-prod"
    TGA_SUPERVISOR_PUBLIC_KEY: Optional[str] = None

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"

# Logic for Read URL Default
settings = Settings()
if not settings.DATABASE_READ_URL:
    settings.DATABASE_READ_URL = settings.DATABASE_WRITE_URL
