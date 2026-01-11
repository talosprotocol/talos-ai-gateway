"""Settings and configuration."""
import os
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
    
    class Config:
        env_file = ".env"

settings = Settings()
