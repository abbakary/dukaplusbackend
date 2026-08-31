"""Thread-safe TTL cache for hot read endpoints (dashboard, analytics).

Uses in-process storage by default. Set REDIS_URL in env for shared cache across workers.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.config import settings

_store: dict[str, tuple[Any, float]] = {}
_lock = asyncio.Lock()
_redis = None


async def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    url = getattr(settings, "redis_url", "") or ""
    if not url:
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        _redis = aioredis.from_url(url, decode_responses=True)
        return _redis
    except Exception:
        return None


async def cache_get(key: str) -> Any | None:
    r = await _get_redis()
    if r is not None:
        try:
            raw = await r.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            pass

    async with _lock:
        entry = _store.get(key)
        if not entry:
            return None
        value, expires = entry
        if time.monotonic() > expires:
            _store.pop(key, None)
            return None
        return value


async def cache_set(key: str, value: Any, ttl_seconds: int | None = None) -> None:
    ttl = ttl_seconds if ttl_seconds is not None else settings.cache_ttl_seconds
    r = await _get_redis()
    if r is not None:
        try:
            await r.setex(key, ttl, json.dumps(value, default=str))
            return
        except Exception:
            pass

    async with _lock:
        _store[key] = (value, time.monotonic() + ttl)


async def cache_delete_prefix(prefix: str) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            keys = [k async for k in r.scan_iter(match=f"{prefix}*")]
            if keys:
                await r.delete(*keys)
            return
        except Exception:
            pass

    async with _lock:
        doomed = [k for k in _store if k.startswith(prefix)]
        for k in doomed:
            _store.pop(k, None)


def tenant_cache_key(tenant_id: str, namespace: str, *parts: str) -> str:
    suffix = ":".join(parts) if parts else "default"
    return f"tenant:{tenant_id}:{namespace}:{suffix}"


async def invalidate_tenant_cache(tenant_id: str) -> None:
    await cache_delete_prefix(f"tenant:{tenant_id}:")
