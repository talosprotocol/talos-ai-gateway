"""Configuration loader for Talos AI Gateway."""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

# Default config file path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "gateway.json"


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from JSON file.
    
    Args:
        config_path: Path to config file. If None, uses default or TALOS_CONFIG env var.
    
    Returns:
        Configuration dictionary.
    """
    # Priority: explicit path > env var > default
    if config_path is None:
        config_path = os.getenv("TALOS_CONFIG", str(DEFAULT_CONFIG_PATH))
    
    path = Path(config_path)
    
    if not path.exists():
        print(f"Warning: Config file not found at {path}, using empty config")
        return {"upstreams": {}, "model_groups": {}, "routing_policies": {}}
    
    with open(path, 'r') as f:
        config = json.load(f)
    
    return config


def save_config(config: Dict[str, Any], config_path: Optional[str] = None) -> None:
    """Save configuration to JSON file.
    
    Args:
        config: Configuration dictionary.
        config_path: Path to config file. If None, uses default or TALOS_CONFIG env var.
    """
    if config_path is None:
        config_path = os.getenv("TALOS_CONFIG", str(DEFAULT_CONFIG_PATH))
    
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, 'w') as f:
        json.dump(config, f, indent=2)


# Global config instance (loaded on import)
_config: Optional[Dict[str, Any]] = None


def get_config() -> Dict[str, Any]:
    """Get the global configuration, loading it if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Reload configuration from file."""
    global _config
    _config = load_config(config_path)
    return _config


def get_upstreams() -> Dict[str, dict]:
    """Get upstreams from config."""
    return get_config().get("upstreams", {})


def get_model_groups() -> Dict[str, dict]:
    """Get model groups from config."""
    return get_config().get("model_groups", {})


def get_routing_policies() -> Dict[str, dict]:
    """Get routing policies from config."""
    return get_config().get("routing_policies", {})
