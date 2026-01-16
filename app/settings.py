"""Settings and configuration."""
import os
from typing import Optional
from pydantic_settings import BaseSettings

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class Settings(BaseSettings):
    # Database
    database_url: str = os.getenv("DATABASE_URL", "postgresql://talos:talos@localhost:5432/talos")
    
    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Security
    master_key: str = os.getenv("MASTER_KEY", "dev-master-key-change-in-prod")
    
    # Rate Limiting
    default_rpm: int = 60
    default_tpm: int = 100000
    
    # MCP
    mcp_schema_cache_ttl_seconds: int = 600
    mcp_tool_list_cache_ttl_seconds: int = 60

    # A2A
    # A2A
    a2a_agent_card_visibility: str = "auth_required"
    dev_mode: bool = False
    
    # A2A Task Retention
    a2a_task_retention_days: int = 30
    
    # A2A SSE Limits
    a2a_sse_max_duration_seconds: int = 900 # 15 Minutes
    a2a_sse_idle_timeout_seconds: int = 60 # 1 Minute
    
    # TGA
    supervisor_public_key: Optional[str] = os.getenv("TGA_SUPERVISOR_PUBLIC_KEY")
    
    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }

settings = Settings()
