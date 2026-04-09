"""Helpers for task push-notification delivery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable
from urllib.parse import urlparse

import aiohttp


logger = logging.getLogger(__name__)


def schedule_push_notifications(configs: Iterable[dict[str, Any]], payload: dict[str, Any]) -> None:
    """Fire-and-forget push delivery for configured task webhooks."""
    items = [dict(config) for config in configs if isinstance(config, dict)]
    if not items:
        return
    asyncio.create_task(deliver_push_notifications(items, payload))


async def deliver_push_notifications(
    configs: list[dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    async with aiohttp.ClientSession() as session:
        for config in configs:
            try:
                await _deliver_one(session, config, payload)
            except Exception as exc:
                logger.warning("push notification delivery failed: %s", exc)


async def _deliver_one(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    url = str(config.get("url") or "")
    _validate_push_url(url)

    headers = {"Content-Type": "application/json"}
    auth = config.get("authentication")
    if isinstance(auth, dict):
        scheme = auth.get("scheme")
        credentials = auth.get("credentials")
        if scheme and credentials:
            headers["Authorization"] = f"{scheme} {credentials}"

    async with session.post(url, json=payload, headers=headers, timeout=5.0) as response:
        if response.status >= 400:
            raise RuntimeError(f"webhook returned HTTP {response.status}")


def _validate_push_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("push notification url must use http or https")
