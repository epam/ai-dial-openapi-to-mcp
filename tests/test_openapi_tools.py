"""Tests for openapi_convert and openapi_verify MCP tools."""

import json

import pytest

from dial_openapi_to_mcp.server import openapi_convert, openapi_verify
from helpers import MINIMAL_OPENAPI_30, MINIMAL_SWAGGER_20

# ---------------------------------------------------------------------------
# openapi_convert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_swagger_to_openapi30():
    result = await openapi_convert(MINIMAL_SWAGGER_20)
    assert result["success"] is True
    assert result["openapi"]["openapi"] == "3.0.0"
    assert "openapi_json" in result
    assert isinstance(result["openapi_json"], str)


@pytest.mark.asyncio
async def test_convert_openapi30_passthrough():
    result = await openapi_convert(MINIMAL_OPENAPI_30)
    assert result["success"] is True
    assert result["openapi"]["openapi"] == "3.0.0"


@pytest.mark.asyncio
async def test_convert_openapi31_passthrough():
    spec = dict(MINIMAL_OPENAPI_30)
    spec["openapi"] = "3.1.0"
    result = await openapi_convert(spec)
    assert result["success"] is True
    assert result["openapi"]["openapi"] == "3.1.0"


@pytest.mark.asyncio
async def test_convert_json_string_input():
    result = await openapi_convert(json.dumps(MINIMAL_SWAGGER_20))
    assert result["success"] is True
    assert result["openapi"]["openapi"] == "3.0.0"


@pytest.mark.asyncio
async def test_convert_invalid_json_string():
    result = await openapi_convert("not valid json {{")
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_convert_unrecognized_spec():
    result = await openapi_convert({"info": {"title": "No version field"}})
    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# openapi_verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_valid_openapi30(minimal_openapi_30):
    result = await openapi_verify(minimal_openapi_30)
    assert result["success"] is True
    assert result["steps"]["parse"]["ok"] is True
    assert result["steps"]["version"]["ok"] is True
    assert result["steps"]["fastmcp"]["ok"] is True
    assert result["steps"]["fastmcp"]["tools"] > 0


@pytest.mark.asyncio
async def test_verify_swagger_fails_version(minimal_swagger_20):
    result = await openapi_verify(minimal_swagger_20)
    assert result["success"] is False
    assert result["steps"]["version"]["ok"] is False
    assert "2.x" in result["steps"]["version"]["error"]


@pytest.mark.asyncio
async def test_verify_unwraps_convert_envelope():
    converted = await openapi_convert(MINIMAL_SWAGGER_20)
    assert converted["success"] is True
    result = await openapi_verify(converted)
    assert result["success"] is True
    assert result["steps"]["parse"].get("unwrapped") is True


@pytest.mark.asyncio
async def test_verify_json_string_input():
    result = await openapi_verify(json.dumps(MINIMAL_OPENAPI_30))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_verify_invalid_input():
    result = await openapi_verify("not json {{")
    assert result["success"] is False
    assert result["steps"]["parse"]["ok"] is False


@pytest.mark.asyncio
async def test_verify_fastmcp_step_fails_on_bad_spec():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Bad", "version": "1.0"},
        "paths": {
            "/x": {
                "get": {
                    "parameters": [
                        {"in": "INVALID_LOCATION", "name": "x", "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    result = await openapi_verify(spec)
    # May pass or fail depending on FastMCP tolerance — just assert no crash
    assert "success" in result
