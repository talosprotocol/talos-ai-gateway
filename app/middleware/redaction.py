"""Log Redaction Middleware."""
import re
from typing import Any, Dict

# Patterns to redact
REDACT_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9_-]{20,}'), '[REDACTED_KEY]'),
    (re.compile(r'"api_key"\s*:\s*"[^"]*"'), '"api_key": "[REDACTED]"'),
    (re.compile(r'"password"\s*:\s*"[^"]*"'), '"password": "[REDACTED]"'),
    (re.compile(r'"secret"\s*:\s*"[^"]*"'), '"secret": "[REDACTED]"'),
    (re.compile(r'"content"\s*:\s*"[^"]{100,}"'), '"content": "[REDACTED_LONG_CONTENT]"'),
]


def redact_string(text: str) -> str:
    """Redact sensitive patterns from a string."""
    result = text
    for pattern, replacement in REDACT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_dict(data: Dict[str, Any], max_content_length: int = 100) -> Dict[str, Any]:
    """Redact sensitive fields from a dictionary."""
    if not isinstance(data, dict):
        return data
    
    result = {}
    sensitive_keys = {'api_key', 'password', 'secret', 'token', 'credentials', 'key_hash'}
    
    for key, value in data.items():
        if key.lower() in sensitive_keys:
            result[key] = '[REDACTED]'
        elif isinstance(value, str) and len(value) > max_content_length:
            result[key] = f'[TRUNCATED:{len(value)} chars]'
        elif isinstance(value, dict):
            result[key] = redact_dict(value, max_content_length)
        elif isinstance(value, list):
            result[key] = [redact_dict(v, max_content_length) if isinstance(v, dict) else v for v in value]
        else:
            result[key] = value
    
    return result
