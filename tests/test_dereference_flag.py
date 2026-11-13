"""MCP_DEREFERENCE_SCHEMAS toggles $ref inlining in cached tool schemas."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from dial_openapi_to_mcp.server import (
    _build_extended_openapi_spec,
    _dereference_schemas,
    _mcp_cache,
    get_or_create_mcp,
)

# Non-recursive $ref: inlining fully removes it, keeping it leaves $ref + $defs.
SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Deref", "version": "1"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/nodes": {
            "post": {
                "operationId": "createNode",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}
                    },
                },
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
    "components": {
        "schemas": {
            "Node": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "child": {"$ref": "#/components/schemas/Leaf"}},
            },
            "Leaf": {"type": "object", "properties": {"value": {"type": "integer"}}},
        }
    },
}


def _request():
    request = MagicMock()
    request.headers = {}
    request.json = AsyncMock(return_value={"params": {}})
    return request


@pytest.fixture
async def clean_cache():
    await _mcp_cache.clear()
    yield
    await _mcp_cache.clear()


@pytest.mark.parametrize("value", ["", "true", "1", "yes", "anything"])
def test_flag_defaults_to_enabled(monkeypatch, value):
    monkeypatch.setenv("MCP_DEREFERENCE_SCHEMAS", value)
    assert _dereference_schemas() is True


def test_flag_unset_is_enabled(monkeypatch):
    monkeypatch.delenv("MCP_DEREFERENCE_SCHEMAS", raising=False)
    assert _dereference_schemas() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
def test_flag_can_disable(monkeypatch, value):
    monkeypatch.setenv("MCP_DEREFERENCE_SCHEMAS", value)
    assert _dereference_schemas() is False


@pytest.mark.asyncio
async def test_disabled_keeps_refs(monkeypatch, clean_cache):
    monkeypatch.setenv("MCP_DEREFERENCE_SCHEMAS", "false")
    entry = await get_or_create_mcp(json.dumps(SPEC), _request())
    assert entry is not None
    params = entry.tools["createNode"].parameters
    assert "$defs" in params
    assert "$ref" in json.dumps(params)


@pytest.mark.asyncio
async def test_enabled_inlines_refs(monkeypatch, clean_cache):
    monkeypatch.delenv("MCP_DEREFERENCE_SCHEMAS", raising=False)
    entry = await get_or_create_mcp(json.dumps(SPEC), _request())
    assert entry is not None
    params = entry.tools["createNode"].parameters
    assert "$defs" not in params
    assert "$ref" not in json.dumps(params)


@pytest.mark.asyncio
async def test_extended_spec_inlines_even_when_flag_off(monkeypatch):
    monkeypatch.setenv("MCP_DEREFERENCE_SCHEMAS", "false")
    extended = await _build_extended_openapi_spec(SPEC)
    x_mcp = extended["paths"]["/nodes"]["post"]["x-mcp"]
    schema = x_mcp["inputSchema"]
    assert "$defs" not in schema
    assert schema["properties"]["child"]["properties"]["value"] == {"type": "integer"}


@pytest.mark.asyncio
async def test_repeat_spec_text_skips_parsing(monkeypatch, clean_cache):
    import dial_openapi_to_mcp.server as server

    text = "\n".join(
        ["openapi: 3.0.0", "info: {title: Memo, version: '1'}", "paths:", "  /a:",
         "    get: {operationId: getA, responses: {'200': {description: ok}}}"]
    )
    calls = []
    real_parse = server.parse_spec_text
    monkeypatch.setattr(server, "parse_spec_text", lambda t: calls.append(1) or real_parse(t))

    first = await get_or_create_mcp(text, _request(), "https://api.example.com")
    second = await get_or_create_mcp(text, _request(), "https://api.example.com")

    assert first is second and len(calls) == 1
    # a different base URL is a different entry and is parsed again
    other = await get_or_create_mcp(text, _request(), "https://other.example.com")
    assert other is not first and len(calls) == 2


@pytest.mark.asyncio
async def test_memo_survives_cache_clear(clean_cache):
    text = json.dumps(SPEC)
    first = await get_or_create_mcp(text, _request())
    await _mcp_cache.clear()
    rebuilt = await get_or_create_mcp(text, _request())
    assert rebuilt is not None and rebuilt is not first and "createNode" in rebuilt.tools
