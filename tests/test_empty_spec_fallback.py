"""Tests for treating a null/empty-object OpenAPI spec as 'no spec' (default tools)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dial_openapi_to_mcp.server import OpenAPI2MCPBridge, _is_empty_spec_json
from helpers import MINIMAL_OPENAPI_30

# ---------------------------------------------------------------------------
# _is_empty_spec_json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec_json",
    [None, "", "{}", "  {}  "],
)
def test_is_empty_spec_json_true_for_null_or_empty_object(spec_json):
    assert _is_empty_spec_json(spec_json) is True


@pytest.mark.parametrize(
    "spec_json",
    ['{"openapi": "3.0.0"}', "not valid json {{", "[]", '""'],
)
def test_is_empty_spec_json_false_for_non_empty_or_non_object(spec_json):
    assert _is_empty_spec_json(spec_json) is False


# ---------------------------------------------------------------------------
# on_list_tools / on_call_tool fallback behavior
# ---------------------------------------------------------------------------


def _make_request(body: dict, headers: dict = None):
    mock = MagicMock()
    mock.headers = {k.lower(): v for k, v in (headers or {}).items()}
    mock.json = AsyncMock(return_value=body)
    return mock


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_spec", [{}, None])
async def test_on_list_tools_falls_back_to_default_for_null_or_empty_spec(empty_spec):
    body = {"params": {}} if empty_spec is None else {"params": {"_meta": {"openapi": empty_spec}}}
    request = _make_request(body)
    bridge = OpenAPI2MCPBridge()
    default_tools = ["default_tool"]
    call_next = AsyncMock(return_value=default_tools)

    with patch("dial_openapi_to_mcp.server.get_http_request", return_value=request):
        result = await bridge.on_list_tools(MagicMock(), call_next)

    assert result == default_tools
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_list_tools_uses_openapi_spec_when_non_empty():
    request = _make_request({"params": {"_meta": {"openapi": MINIMAL_OPENAPI_30}}})
    bridge = OpenAPI2MCPBridge()
    call_next = AsyncMock(return_value=["default_tool"])

    with patch("dial_openapi_to_mcp.server.get_http_request", return_value=request):
        result = await bridge.on_list_tools(MagicMock(), call_next)

    call_next.assert_not_awaited()
    assert any(getattr(tool, "name", None) == "list_items" for tool in result)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_spec", [{}, None])
async def test_on_call_tool_falls_back_to_default_for_null_or_empty_spec(empty_spec):
    body = {
        "params": {"name": "some_tool", "arguments": {}},
    }
    if empty_spec is not None:
        body["params"]["_meta"] = {"openapi": empty_spec}
    request = _make_request(body)
    bridge = OpenAPI2MCPBridge()
    sentinel_result = object()
    call_next = AsyncMock(return_value=sentinel_result)

    with patch("dial_openapi_to_mcp.server.get_http_request", return_value=request):
        result = await bridge.on_call_tool(MagicMock(), call_next)

    assert result is sentinel_result
    call_next.assert_awaited_once()
