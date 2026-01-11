import json
from typing import Any

def _normalize_values(obj: Any) -> Any:
    """Recursively normalize floats that are integers to actual ints."""
    if isinstance(obj, dict):
        return {k: _normalize_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_values(i) for i in obj]
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    return obj

def canonical_json_bytes(obj: Any) -> bytes:
    """
    Produce a canonical JSON byte string for cryptographic signing.
    Matches the Talos Protocol specification for A2A attestation.
    
    Implementation Rules:
    1. Keys sorted lexicographically.
    2. No whitespace (separators: (',', ':')).
    3. No indentation.
    4. Integers must be represented without fractional parts (e.g., 1.0 -> 1).
    5. UTF-8 encoded.
    """
    normalized = _normalize_values(obj)
    
    canonical_str = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )
    return canonical_str.encode("utf-8")
