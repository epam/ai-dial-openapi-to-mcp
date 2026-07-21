"""Tests for process-local MCP cache resource ownership and concurrency."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from dial_openapi_to_mcp.cache import CacheEntry, MCPCache


def _entry(api_id: str, client=None) -> CacheEntry:
    return CacheEntry(mcp=None, name="Test API", client=client, api_id=api_id, spec={}, tools={})


@pytest.mark.asyncio
async def test_cache_basic_operations():
    cache = MCPCache(max_size=10, ttl_seconds=60)
    await cache.set("test123", _entry("test123"))

    retrieved = await cache.get("test123")

    assert retrieved is not None
    assert retrieved.name == "Test API"
    assert retrieved.api_id == "test123"


@pytest.mark.asyncio
async def test_cache_eviction_closes_client():
    cache = MCPCache(max_size=1, ttl_seconds=60)
    first_client = AsyncMock()

    await cache.set("id1", _entry("id1", first_client))
    await cache.set("id2", _entry("id2"))

    assert await cache.get("id1") is None
    assert await cache.get("id2") is not None
    first_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_replacement_closes_displaced_client():
    cache = MCPCache()
    previous_client = AsyncMock()

    await cache.set("id", _entry("id", previous_client))
    await cache.set("id", _entry("id"))

    previous_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_get_or_create_is_single_flight():
    cache = MCPCache()
    calls = 0
    release_factory = asyncio.Event()

    async def factory():
        nonlocal calls
        calls += 1
        await release_factory.wait()
        return _entry("shared")

    first = asyncio.create_task(cache.get_or_create("shared", factory))
    second = asyncio.create_task(cache.get_or_create("shared", factory))
    await asyncio.sleep(0)
    release_factory.set()

    first_entry, second_entry = await asyncio.gather(first, second)

    assert calls == 1
    assert first_entry is second_entry


@pytest.mark.asyncio
async def test_zero_cleanup_interval_does_not_create_task():
    cache = MCPCache(cleanup_interval=0)

    cache.start_cleanup_task()

    assert cache._cleanup_task is None


@pytest.mark.asyncio
async def test_cache_stats():
    cache = MCPCache(max_size=10, ttl_seconds=60)
    await cache.set("test", _entry("test"))
    await cache.get("test")
    await cache.get("nonexistent")

    stats = cache.stats()

    assert stats["size"] == 1
    assert stats["hits"] >= 1
    assert stats["misses"] >= 1
