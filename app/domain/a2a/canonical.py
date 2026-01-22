"""Canonical JSON serialization (RFC 8785)."""
import json

def canonical_json_bytes(data: dict) -> bytes:
    """
    Serializes a dictionary to byte string using RFC 8785 rules:
    - Keys sorted alphabetically
    - No whitespace in separators
    - UTF-8 encoding
    - Floats ending in .0 converted to int
    """
    def normalize(obj):
        if isinstance(obj, float):
            if obj.is_integer():
                return int(obj)
            return obj
        if isinstance(obj, dict):
            return {k: normalize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [normalize(v) for v in obj]
        return obj

    clean_data = normalize(data)

    return json.dumps(
        clean_data, 
        sort_keys=True, 
        separators=(',', ':'), 
        ensure_ascii=False
    ).encode('utf-8')
