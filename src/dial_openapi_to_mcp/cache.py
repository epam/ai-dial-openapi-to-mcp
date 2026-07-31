"""Process-local async cache for generated MCP instances."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached MCP instance and the HTTP client it owns."""

    mcp: Any
    name: str
    client: Any
    api_id: str
    spec: dict[str, Any]
    tools: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0

    def touch(self) -> None:
        self.last_accessed = time.time()
        self.access_count += 1

    def is_expired(self, ttl: int) -> bool:
        return ttl > 0 and time.time() - self.last_accessed > ttl

    def age_seconds(self) -> float:
        return time.time() - self.created_at


class MCPCache:
    """Async LRU cache that closes HTTP clients outside its internal lock."""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600, cleanup_interval: int = 300):
        if max_size < 0 or ttl_seconds < 0 or cleanup_interval < 0:
            raise ValueError("Cache settings must be non-negative")
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cleanup_interval = cleanup_interval
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[CacheEntry]] = {}
        self._stats = {"hits": 0, "misses": 0, "evictions": 0, "expirations": 0, "cleanups": 0}

    async def _close(self, entry: CacheEntry | None) -> None:
        if entry and entry.client:
            try:
                await entry.client.aclose()
            except Exception:
                logger.warning("Unable to close cached HTTP client", exc_info=True)

    async def get(self, api_id: str) -> CacheEntry | None:
        expired: CacheEntry | None = None
        async with self._lock:
            entry = self._cache.get(api_id)
            if entry is None:
                self._stats["misses"] += 1
                return None
            if entry.is_expired(self._ttl_seconds):
                expired = self._cache.pop(api_id)
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
            else:
                entry.touch()
                self._cache.move_to_end(api_id)
                self._stats["hits"] += 1
                return entry
        await self._close(expired)
        return None

    async def set(self, api_id: str, entry: CacheEntry) -> None:
        displaced: list[CacheEntry] = []
        async with self._lock:
            previous = self._cache.pop(api_id, None)
            if previous is not None and previous is not entry:
                displaced.append(previous)
            while self._max_size > 0 and len(self._cache) >= self._max_size:
                _, evicted = self._cache.popitem(last=False)
                displaced.append(evicted)
                self._stats["evictions"] += 1
            self._cache[api_id] = entry
        for old_entry in displaced:
            await self._close(old_entry)

    async def get_or_create(self, api_id: str, factory) -> CacheEntry:
        """Create one entry per key; followers await the same creation result."""
        expired: CacheEntry | None = None
        creator = False
        async with self._lock:
            cached = self._cache.get(api_id)
            if cached is not None and not cached.is_expired(self._ttl_seconds):
                cached.touch()
                self._cache.move_to_end(api_id)
                self._stats["hits"] += 1
                return cached
            if cached is not None:
                expired = self._cache.pop(api_id)
                self._stats["expirations"] += 1
            self._stats["misses"] += 1

            pending = self._pending.get(api_id)
            if pending is None:
                pending = asyncio.get_running_loop().create_future()
                self._pending[api_id] = pending
                creator = True

        await self._close(expired)
        if not creator:
            return await asyncio.shield(pending)

        try:
            entry = await factory()
            await self.set(api_id, entry)
            if not pending.done():
                pending.set_result(entry)
            return entry
        except BaseException as error:
            if not pending.done():
                pending.set_exception(error)
                # Mark the exception retrieved if every follower is cancelled.
                pending.exception()
            raise
        finally:
            async with self._lock:
                self._pending.pop(api_id, None)

    async def remove(self, api_id: str) -> bool:
        async with self._lock:
            entry = self._cache.pop(api_id, None)
        await self._close(entry)
        return entry is not None

    async def clear(self) -> None:
        async with self._lock:
            entries = list(self._cache.values())
            self._cache.clear()
        for entry in entries:
            await self._close(entry)

    async def cleanup_expired(self) -> int:
        if self._ttl_seconds <= 0:
            return 0
        async with self._lock:
            expired_ids = [
                key for key, entry in self._cache.items() if entry.is_expired(self._ttl_seconds)
            ]
            entries = [self._cache.pop(key) for key in expired_ids]
            self._stats["expirations"] += len(entries)
            self._stats["cleanups"] += 1
        for entry in entries:
            await self._close(entry)
        return len(entries)

    def start_cleanup_task(self) -> None:
        if self._cleanup_interval == 0 or (self._cleanup_task and not self._cleanup_task.done()):
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._cleanup_interval)
                await self.cleanup_expired()
        except asyncio.CancelledError:
            pass

    def size(self) -> int:
        return len(self._cache)

    def stats(self) -> dict[str, Any]:
        total = self._stats["hits"] + self._stats["misses"]
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "ttl_seconds": self._ttl_seconds,
            **self._stats,
            "hit_rate": f"{self._stats['hits'] / total * 100:.2f}%" if total else "0.00%",
        }
