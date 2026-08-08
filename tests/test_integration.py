"""Integration tests for get_or_create_mcp and caching behaviour."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from dial_openapi_to_mcp.server import _mcp_cache, get_or_create_mcp
from helpers import MINIMAL_OPENAPI_30, MINIMAL_SWAGGER_20


def _make_request(body: dict, headers: dict = None):
    mock = MagicMock()
    mock.headers = {k.lower(): v for k, v in (headers or {}).items()}
    mock.json = AsyncMock(return_value=body)
    return mock


@pytest.fixture(autouse=True)
async def clear_cache():
    await _mcp_cache.clear()
    yield
    await _mcp_cache.clear()


@pytest.mark.asyncio
async def test_get_or_create_mcp_returns_entry():
    request = _make_request({"params": {}})
    entry = await get_or_create_mcp(json.dumps(MINIMAL_OPENAPI_30), request)
    assert entry is not None
    assert len(entry.tools) > 0


@pytest.mark.asyncio
async def test_get_or_create_mcp_cache_hit():
    request = _make_request({"params": {}})
    spec_json = json.dumps(MINIMAL_OPENAPI_30)
    entry1 = await get_or_create_mcp(spec_json, request)
    entry2 = await get_or_create_mcp(spec_json, request)
    assert entry1 is entry2


@pytest.mark.asyncio
async def test_get_or_create_mcp_rejects_header_not_in_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("OUTBOUND_HEADER_ALLOWLIST", "x-other-header")
    spec_json = json.dumps(MINIMAL_OPENAPI_30)
    request = _make_request(
        {"params": {"_meta": {"extra_headers": [{"name": "X-Key", "value": "secret"}]}}}
    )

    assert await get_or_create_mcp(spec_json, request) is None


@pytest.mark.asyncio
async def test_get_or_create_mcp_allows_configured_header(monkeypatch):
    monkeypatch.setenv("OUTBOUND_HEADER_ALLOWLIST", "x-request-id")
    spec_json = json.dumps(MINIMAL_OPENAPI_30)
    request = _make_request(
        {"params": {"_meta": {"extra_headers": [{"name": "X-Request-Id", "value": "request-1"}]}}}
    )

    entry = await get_or_create_mcp(spec_json, request)
    assert entry is not None


@pytest.mark.asyncio
async def test_get_or_create_mcp_allows_extra_header_when_allowlist_unset(monkeypatch):
    monkeypatch.delenv("OUTBOUND_HEADER_ALLOWLIST", raising=False)
    spec_json = json.dumps(MINIMAL_OPENAPI_30)
    request = _make_request(
        {"params": {"_meta": {"extra_headers": [{"name": "X-Key", "value": "secret"}]}}}
    )

    entry = await get_or_create_mcp(spec_json, request)
    assert entry is not None


@pytest.mark.asyncio
async def test_get_or_create_mcp_blocklist_override_replaces_default(monkeypatch):
    monkeypatch.delenv("OUTBOUND_HEADER_ALLOWLIST", raising=False)
    monkeypatch.setenv("OUTBOUND_HEADER_BLOCKLIST", "x-custom-blocked")
    spec_json = json.dumps(MINIMAL_OPENAPI_30)

    # "Connection" is in the default block set but not in the overridden one.
    allowed_request = _make_request(
        {"params": {"_meta": {"extra_headers": [{"name": "Connection", "value": "keep-alive"}]}}}
    )
    entry = await get_or_create_mcp(spec_json, allowed_request)
    assert entry is not None

    blocked_request = _make_request(
        {"params": {"_meta": {"extra_headers": [{"name": "X-Custom-Blocked", "value": "v"}]}}}
    )
    assert await get_or_create_mcp(spec_json, blocked_request) is None


@pytest.mark.asyncio
async def test_get_or_create_mcp_rejects_non_object_spec():
    request = _make_request({"params": {}})

    assert await get_or_create_mcp(json.dumps(["not", "an", "object"]), request) is None


@pytest.mark.asyncio
async def test_get_or_create_mcp_rejects_swagger():
    request = _make_request({"params": {}})
    entry = await get_or_create_mcp(json.dumps(MINIMAL_SWAGGER_20), request)
    assert entry is None


@pytest.mark.asyncio
async def test_get_or_create_mcp_rejects_unsupported_version():
    spec = {"openapi": "2.9.0", "info": {"title": "T", "version": "1"}, "paths": {}}
    request = _make_request({"params": {}})
    entry = await get_or_create_mcp(json.dumps(spec), request)
    assert entry is None


@pytest.mark.asyncio
async def test_get_or_create_mcp_tools_match_spec_paths():
    request = _make_request({"params": {}})
    entry = await get_or_create_mcp(json.dumps(MINIMAL_OPENAPI_30), request)
    assert entry is not None
    assert any("list_items" in name or "items" in name for name in entry.tools)
