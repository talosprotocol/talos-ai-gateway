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
        for key, value in span.attributes.items():
            if self._should_redact(key):
                new_attributes[key] = "[REDACTED]"
            else:
                new_attributes[key] = value
        
        # We can't easily modify the span attributes directly on a ReadableSpan cleanly 
        # without private access or potentially affecting other processors if we were chaining differently.
        # However, OpenTelemetry Python SDK `ReadableSpan` attributes are stored in `_attributes`.
        # Standard practice: we wrap the exporter instead? 
        # Actually, SpanProcessor `on_end` receives a ReadableSpan. If we want to modify it, 
        # we should probably be a `SpanExporter` wrapper or modify it here if possible.
        # But `on_end` is called when the span is finished. 
        # Let's try to modify existing attributes if mutable, or if not, use a different approach.
        # `span._attributes` is the storage. 
        
        # Official recommended way is usually a custom `SpanExporter` that sanitizes before sending,
        # OR a `SpanProcessor` that modifies the span in `on_start` (but attributes might not be there yet)
        # OR `on_end`.
        
        # In OTel Python, `span.set_attribute` works if the span is recording. 
        # But `on_end` happens after `span.end()`, so `is_recording` might be false?
        # Let's check typical usage. Actually, `BatchSpanProcessor` just queues it. 
        # If we wrap the processor, we intercept `on_end`.
        
        # Let's try to modify `span.attributes` directly? It's a `BoundedAttributes` object usually.
        # Or we can just use `span._attributes = ...` if we are bold.
        # Better: use `span.set_attribute(key, "[REDACTED]")` IF it allows it after end?
        # Usually it doesn't.
        
        # WAIT: The requirement says "Implement a span processor... that: Removes or redacts...".
        # If we use `span.set_attribute` during execution (e.g. via instrumentation hooks), that's cleaner.
        # But we want to catch ALL attributes, even those added by auto-instrumentation.
        
        # A common pattern is to wrap the Exporter. 
        # "Implement a span processor (`TalosSpanProcessor`)..."
        # Okay, let's look at `BatchSpanProcessor`. It takes an Exporter.
        # If I implement `SpanProcessor`, I get `on_end(span)`.
        # I can treat `span` as mutable if the SDK implementation allows.
        # `ReadableSpan` is the type hint.
        
        # Let's assume we can modify `_attributes`.
        # Or cleaner: Since `BatchSpanProcessor` reads `span.attributes` to export, 
        # if we modify it in our `on_end` BEFORE delegating to `self._processor.on_end(span)`, 
        # then the delegate (e.g. BatchSpanProcessor) sees the redacted version.
        
        # But `attributes` on ReadableSpan might be a copy in some implementations? 
        # No, it checks `self._attributes`.
        pass
        
        # To affect the underlying span before it goes to the next processor/exporter:
        # We need access to write.
        if hasattr(span, "_attributes"):
             span._attributes = new_attributes
             
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
