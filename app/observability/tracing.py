from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter
import re
from typing import Optional

class TalosSpanProcessor(SpanProcessor):
    """
    SpanProcessor that redacts sensitive information from spans before they are exported.
    """
    def __init__(self, processor: SpanProcessor):
        self._processor = processor
        self._sensitive_keys = {
            "authorization", "cookie", "set-cookie",
            "header_b64u", "ciphertext_b64u", "ciphertext_hash"
        }
        self._sensitive_patterns = [
            
            re.compile(r"http\.request\.header\..*", re.IGNORECASE),
            re.compile(r"http\.response\.header\..*", re.IGNORECASE),
            re.compile(r".*(signature|token|secret|nonce).*", re.IGNORECASE)
        ]

    def on_start(self, span: "Span", parent_context: Optional["Context"] = None) -> None:
        self._processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if not span.attributes:
            self._processor.on_end(span)
            return
            
        new_attributes = {}
        # Iterate and scrub
        # span.attributes is a Mapping[str, AttributeValue]
        for key, value in span.attributes.items():
            if self._should_redact(key):
                new_attributes[key] = "[REDACTED]"
            else:
                new_attributes[key] = value
        
        # Apply changes to the underlying span storage
        # Note: This relies on internal implementation details of OTel Python SDK's ReadableSpan
        # which stores attributes in `.attributes` property backed by `_attributes` usually.
        # Direct modification is necessary because ReadableSpan is ostensibly read-only.
        if hasattr(span, "_attributes"):
             # We replace the entire attributes dict/Attributes object with our scrubbed dict
             # BoundedAttributes (default impl) can accept a dict update, but replacing it is safer 
             # to ensure we don't keep old sensitive keys if we were modifying in place.
             span._attributes = new_attributes
        
        # Delegate to the wrapped processor (e.g. BatchSpanProcessor -> Exporter)
        self._processor.on_end(span)

    def shutdown(self, delay_millis: int = 30000) -> None:
        self._processor.shutdown(delay_millis)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._processor.force_flush(timeout_millis)
        
    def _should_redact(self, key: str) -> bool:
        key_lower = key.lower()
        if key_lower in self._sensitive_keys:
            return True
        for pattern in self._sensitive_patterns:
            if pattern.match(key_lower):
                return True
        return False
