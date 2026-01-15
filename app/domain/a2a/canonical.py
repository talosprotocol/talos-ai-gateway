"""Canonical JSON serialization (RFC 8785)."""
import json

def canonical_json_bytes(data: dict) -> bytes:
    """
    Serializes a dictionary to byte string using RFC 8785 rules:
    - Keys sorted alphabetically
    - No whitespace in separators
    - UTF-8 encoding
    """
    return json.dumps(
        data, 
        sort_keys=True, 
        separators=(',', ':'), 
        ensure_ascii=False
    ).encode('utf-8')
