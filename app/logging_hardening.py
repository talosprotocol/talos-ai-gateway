"""Logging Hardening and Redaction.

This module provides filters to prevent sensitive data (like secret Envelopes)
from appearing in application logs.
"""
import logging
import re

# Regex to match potential hex-encoded secret components (24 or 32 hex chars)
# accompanied by context clues like iv, tag, ciphertext.
SECRET_PATTERNS = [
    (re.compile(r'("iv":\s*")[0-9a-f]{24}(")'), r'\1[REDACTED]\2'),
    (re.compile(r'("tag":\s*")[0-9a-f]{32}(")'), r'\1[REDACTED]\2'),
    (re.compile(r'("ciphertext":\s*")[0-9a-f]+(")'), r'\1[REDACTED]\2'),
    # Also catch keyword-based assignments
    (re.compile(r'(iv=[0-9a-f]{24})'), 'iv=[REDACTED]'),
    (re.compile(r'(tag=[0-9a-f]{32})'), 'tag=[REDACTED]'),
]

class SecretRedactionFilter(logging.Filter):
    """Filter that redacts secret-like patterns from log records."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.msg, str):
            return True
            
        msg = record.msg
        for pattern, replacement in SECRET_PATTERNS:
            msg = pattern.sub(replacement, msg)
        
        record.msg = msg
        
        # Also redact arguments if they are strings
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    for pattern, replacement in SECRET_PATTERNS:
                        arg = pattern.sub(replacement, arg)
                new_args.append(arg)
            record.args = tuple(new_args)
            
        return True

def setup_logging_redaction() -> None:
    """Apply the SecretRedactionFilter to all existing loggers."""
    redact_filter = SecretRedactionFilter()
    
    # Apply to root logger and all descendants
    root_logger = logging.getLogger()
    
    # Remove existing filters if any (to avoid duplicates)
    for f in root_logger.filters[:]:
        if isinstance(f, SecretRedactionFilter):
            root_logger.removeFilter(f)
            
    root_logger.addFilter(redact_filter)
    
    # Specifically ensure it's on common library loggers if they bypass root
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        logger.addFilter(redact_filter)

    logging.info("Logging redaction filters active.")
