"""Tests for free-form object request bodies (no declared properties)."""

import json
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP

from dial_openapi_to_mcp.server import _build_component_fn

BASE_URL = "https://api.example.com"


def _spec(body_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.0.1",
        "info": {"title": "Proxy", "version": "1"},
        "servers": [{"url": BASE_URL}],
        "paths": {
            "/v1/messages/{id}": {
                "post": {
                    "operationId": "createMessage",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "schemas": {"ProxyRequest": {"type": "object", "additionalProperties": True}}
        },
    }


async def _send(spec: dict[str, Any], arguments: dict[str, Any]) -> httpx.Request:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    mcp = FastMCP.from_openapi(
        openapi_spec=spec, client=client, mcp_component_fn=_build_component_fn(spec, None)
    )
    await mcp.call_tool("createMessage", arguments)
    await client.aclose()
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/components/schemas/ProxyRequest"},
        {"type": "object", "additionalProperties": True},
    ],
)
async def test_free_form_body_is_sent_unwrapped(schema: dict[str, Any]) -> None:
    payload = {"model": "claude", "max_tokens": 1}
    request = await _send(_spec(schema), {"id": "a.b", "body": payload})
    assert json.loads(request.content) == payload


@pytest.mark.asyncio
async def test_empty_free_form_body_is_empty_object() -> None:
    request = await _send(
        _spec({"$ref": "#/components/schemas/ProxyRequest"}), {"id": "x", "body": {}}
    )
    assert json.loads(request.content) == {}


@pytest.mark.asyncio
async def test_declared_property_named_body_is_not_unwrapped() -> None:
    schema = {"type": "object", "properties": {"body": {"type": "string"}}}
    request = await _send(_spec(schema), {"id": "x", "body": "hi"})
    assert json.loads(request.content) == {"body": "hi"}
