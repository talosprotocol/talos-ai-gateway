import json
import logging
import aiohttp
from typing import Protocol, Any, Dict

logger = logging.getLogger("talos.audit.sink")

class AuditSink(Protocol):
    async def emit(self, event: Dict[str, Any]) -> None:
        """Emit an audit event to the sink."""
        ...

class StdOutSink:
    def __init__(self):
        self._logger = logging.getLogger("talos.audit")
        
    async def emit(self, event: Dict[str, Any]) -> None:
        self._logger.info(json.dumps(event))

class HttpSink:
    def __init__(
        self,
        service_url: str,
        api_key: str = None,
        max_queue_size: int = 1000,
        timeout_seconds: float = 5.0,
    ):
        self.url = f"{service_url.rstrip('/')}/events"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        
    async def emit(self, event: Dict[str, Any]) -> None:
        """Emit an audit event to the sink."""
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.url, json=event, headers=headers) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.error(f"Audit Service Error: {resp.status} - {body}")
        except Exception as e:
            logger.error(f"Audit Sink Transmission Error: {e}")

class CompositeSink:
    def __init__(self, sinks: list[AuditSink]):
        self.sinks = sinks
    
    async def emit(self, event: Dict[str, Any]) -> None:
        for sink in self.sinks:
            try:
                await sink.emit(event)
            except Exception:
                pass 
