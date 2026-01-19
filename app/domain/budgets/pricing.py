"""Pricing Registry for Talos AI Gateway."""
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Optional, Tuple, Any
import logging
from datetime import datetime
import hashlib
import json
import os

logger = logging.getLogger(__name__)

# Default Quantization: 8 decimal places for USD
USD_QUANT = Decimal("0.00000001")

class PricingRegistry:
    """Registry for calculating costs of LLM and MCP operations."""
    
    def __init__(self, config: Dict[str, Any] = None):
        self._pricing_map = config or {}
        self._version = self._compute_version()
        self._loaded_at = datetime.utcnow()
        
    def _compute_version(self) -> str:
        """Simple version hash of the current pricing map."""
        content = json.dumps(self._pricing_map, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:8]

    @property
    def version(self) -> str:
        return self._version

    def get_llm_cost(
        self, 
        model_name: str, 
        provider: str, 
        group_id: Optional[str], 
        input_tokens: int, 
        output_tokens: int
    ) -> Tuple[Decimal, str]:
        """
        Calculate cost for LLM usage.
        
        Fallback Order:
        1. Exact Model Match (model_name)
        2. Model Group Match (group_id)
        3. Provider Default (provider)
        4. Default (global) -> 0 if not found
        
        Returns: (cost_usd, pricing_version)
        """
        prices = self._pricing_map.get("llm", {})
        
        # 1. Exact Model
        entry = prices.get(model_name)
        
        # 2. Model Group
        if not entry and group_id:
            entry = prices.get(f"group:{group_id}")
            
        # 3. Provider Default
        if not entry and provider:
            entry = prices.get(f"provider:{provider}")
            
        # 4. Global Default (optional)
        if not entry:
            entry = prices.get("default")
            
        if not entry:
            # Fallback to 0 if unknown
            return Decimal("0.00"), self._version
            
        try:
            # Parse rates (per 1k tokens usually)
            rate_in = Decimal(str(entry.get("input", "0")))
            rate_out = Decimal(str(entry.get("output", "0")))
            
            # Use Decimal for high precision arithmetic
            cost_in = (Decimal(input_tokens) / Decimal(1000)) * rate_in
            cost_out = (Decimal(output_tokens) / Decimal(1000)) * rate_out
            
            total = (cost_in + cost_out).quantize(USD_QUANT, rounding=ROUND_HALF_EVEN)
            return total, self._version
        except Exception as e:
            logger.error(f"Error calculating pricing for {model_name}: {e}")
            return Decimal("0.00"), self._version

    def get_mcp_cost(
        self,
        server_id: str,
        tool_name: str
    ) -> Tuple[Decimal, str]:
        """
        Calculate cost for MCP usage.
        
        Key: server_id:tool_name (lower case)
        
        Fallback Order:
        1. Exact Tool
        2. Server Default
        3. Global Default
        """
        prices = self._pricing_map.get("mcp", {})
        
        # Normalize key
        key = f"{server_id}:{tool_name}".lower()
        
        # 1. Exact Tool
        entry = prices.get(key)
        
        # 2. Server Default
        if not entry:
            entry = prices.get(f"server:{server_id}")
            
        # 3. Global Default
        if not entry:
            entry = prices.get("default")
            
        if not entry:
            return Decimal("0.00"), self._version
            
        try:
            # Entry format: "0.01" (Fixed cost per call)
            cost = Decimal(str(entry))
            return cost.quantize(USD_QUANT, rounding=ROUND_HALF_EVEN), self._version
        except Exception as e:
            logger.error(f"Error calculating MCP pricing for {key}: {e}")
            return Decimal("0.00"), self._version


# Default configuration for V1
DEFAULT_PRICING = {
    "llm": {
        "gpt-4": {"input": "0.03", "output": "0.06"},
        "gpt-4-turbo": {"input": "0.01", "output": "0.03"},
        "gpt-3.5-turbo": {"input": "0.0005", "output": "0.0015"},
        "provider:openai": {"input": "0.001", "output": "0.002"},
        "provider:anthropic": {"input": "0.0015", "output": "0.0075"}, # Claude 3 Sonnet approx
        "default": {"input": "0", "output": "0"}
    },
    "mcp": {
        "default": "0.00",
        # Example: "weather-server:get_forecast": "0.01"
    }
}

_registry: Optional[PricingRegistry] = None

def get_pricing_registry() -> PricingRegistry:
    global _registry
    if _registry is None:
        # Check for env var override
        path = os.getenv("PRICING_CONFIG_PATH")
        config = DEFAULT_PRICING
        if path and os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    config = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load pricing config from {path}: {e}")
        
        _registry = PricingRegistry(config)
    return _registry

def reload_pricing_registry():
    global _registry
    logger.info("Reloading Pricing Registry...")
    # Just reset to force reload on next get() call, or reload immediately
    _registry = None
    get_pricing_registry()
