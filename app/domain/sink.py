from typing import Protocol, Any, Dict
import json
import logging
import aiohttp
import os
from abc import abstractmethod

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

import asyncio

class HttpSink:
    def __init__(self, service_url: str, api_key: str = None, max_queue_size: int = 1000):
        self.url = f"{service_url.rstrip('/')}/events"
        self.api_key = api_key
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task = None
        self._loop = None
        
    def _start_worker(self):
        if self._worker_task is None:
            self._loop = asyncio.get_event_loop()
            self._worker_task = self._loop.create_task(self._worker())

    async def _worker(self):
        async with aiohttp.ClientSession() as session:
            while True:
                event = await self.queue.get()
                try:
                    headers = {"Content-Type": "application/json"}
                    if self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    
                    async with session.post(self.url, json=event, headers=headers, timeout=5.0) as resp:
                        if resp.status >= 400:
                            logger.error(f"Audit Service Error: {resp.status}")
                except Exception as e:
                    logger.error(f"Audit Sink Transmission Error: {e}")
                finally:
                    self.queue.task_done()

    async def emit(self, event: Dict[str, Any]) -> None:
        self._start_worker()
        
        if self.queue.full():
            # Locked Rule 10: Drop newest on overflow
            logger.warning("audit_queue_dropped_total: queue full, dropping newest event")
            # Clear degraded health signal (log is proxy here)
            return
            
        await self.queue.put(event)

class CompositeSink:
    def __init__(self, sinks: list[AuditSink]):
        self.sinks = sinks
    
    async def emit(self, event: Dict[str, Any]) -> None:
        for sink in self.sinks:
            try:
                await sink.emit(event)
            except Exception:
                pass 
