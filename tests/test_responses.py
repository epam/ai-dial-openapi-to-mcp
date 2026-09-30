"""Tests for tool response handling: non-JSON bodies, response headers, output validation."""

import base64
from typing import Any, Callable

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import EmbeddedResource, ImageContent, TextContent

from dial_openapi_to_mcp.responses import RESPONSE_META_KEY
from dial_openapi_to_mcp.server import _build_component_fn

BASE_URL = "https://api.example.com"


def _operation(operation_id: str, responses: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"operationId": operation_id, "responses": responses, **extra}


def _json_response(schema: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"description": "OK", "content": {"application/json": {"schema": schema}}, **extra}


ITEM_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}

SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Responses API", "version": "1.0.0"},
    "servers": [{"url": BASE_URL}],
    "paths": {
        "/item": {
            "get": _operation(
                "getItem",
                {
                    "200": _json_response(
                        ITEM_SCHEMA,
                        headers={"X-Revision": {"schema": {"type": "string"}}},
                    )
                },
            )
        },
        "/loose-item": {
            "get": _operation(
                "getLooseItem",
                {"200": _json_response(ITEM_SCHEMA)},
                **{"x-mcp": {"validateOutput": False}},
            )
        },
        # Like DIAL's downloadSkillFile: declared as JSON binary, served as the file's own type.
        "/file": {
            "get": _operation(
                "getFile", {"200": _json_response({"type": "string", "format": "binary"})}
            )
        },
        "/archive": {
            "get": _operation(
                "getArchive",
                {
                    "200": {
                        "description": "ZIP",
                        "content": {
                            "application/zip": {"schema": {"type": "string", "format": "binary"}}
                        },
                    }
                },
            )
        },
        "/upload": {"put": _operation("upload", {"200": {"description": "Uploaded"}})},
    },
}


async def _call(
    handler: Callable[[httpx.Request], httpx.Response],
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    mcp = FastMCP.from_openapi(
        openapi_spec=SPEC, client=client, mcp_component_fn=_build_component_fn(SPEC, None)
    )
    try:
        return await mcp.call_tool(tool, arguments or {})
    finally:
        await client.aclose()


async def _tools() -> dict[str, Any]:
    client = httpx.AsyncClient(base_url=BASE_URL)
    mcp = FastMCP.from_openapi(
        openapi_spec=SPEC, client=client, mcp_component_fn=_build_component_fn(SPEC, None)
    )
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    await client.aclose()
    return tools


def _texts(result: Any) -> list[str]:
    return [block.text for block in result.content if isinstance(block, TextContent)]


@pytest.mark.asyncio
async def test_json_response_keeps_structured_content_and_exposes_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"name": "a"},
            headers={
                "ETag": '"v1"',
                "X-Revision": "7",
                "X-Internal": "hidden",
                "Set-Cookie": "session=secret",
            },
        )

    result = await _call(handler, "getItem")

    assert result.structured_content == {"name": "a"}
    headers = result.meta[RESPONSE_META_KEY]["headers"]
    assert result.meta[RESPONSE_META_KEY]["status_code"] == 200
    assert headers["etag"] == '"v1"'
    assert headers["x-revision"] == "7"  # declared on the response in the spec
    assert "x-internal" not in headers and "set-cookie" not in headers
    assert any(text.startswith("HTTP 200; ") and '"v1"' in text for text in _texts(result))


@pytest.mark.asyncio
async def test_json_response_is_validated_against_output_schema():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ToolError, match="Output validation error"):
        await _call(handler, "getItem")


@pytest.mark.asyncio
async def test_validation_can_be_disabled_globally(monkeypatch):
    monkeypatch.setenv("MCP_VALIDATE_OUTPUT", "false")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    result = await _call(handler, "getItem")
    assert result.structured_content == {"unexpected": True}
    assert (await _tools())["getItem"].output_schema is None


@pytest.mark.asyncio
async def test_validation_can_be_disabled_per_operation():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    result = await _call(handler, "getLooseItem")
    assert result.structured_content == {"unexpected": True}
    assert (await _tools())["getLooseItem"].output_schema is None


@pytest.mark.asyncio
async def test_text_body_on_json_declared_route_is_returned_as_text():
    body = "---\nname: my-skill\n---\n# Skill\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode(), headers={"Content-Type": "text/markdown", "ETag": "e2"}
        )

    result = await _call(handler, "getFile")

    assert _texts(result)[0] == body
    # The declared schema is a wrapped string, so the text also satisfies it.
    assert result.structured_content == {"result": body}
    assert result.meta[RESPONSE_META_KEY]["headers"]["etag"] == "e2"


@pytest.mark.asyncio
async def test_image_body_is_returned_as_image_content():
    png = b"\x89PNG\r\n\x1a\n\x00\x01"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})

    result = await _call(handler, "getFile")

    (image,) = [block for block in result.content if isinstance(block, ImageContent)]
    assert image.mimeType == "image/png"
    assert base64.b64decode(image.data) == png


@pytest.mark.asyncio
async def test_binary_body_is_returned_as_blob_resource_without_query():
    archive = b"PK\x03\x04\xff\xfe"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=archive, headers={"Content-Type": "application/zip"})

    result = await _call(handler, "getArchive")

    (resource,) = [block for block in result.content if isinstance(block, EmbeddedResource)]
    assert resource.resource.mimeType == "application/zip"
    assert base64.b64decode(resource.resource.blob) == archive
    assert str(resource.resource.uri) == f"{BASE_URL}/archive"
    assert (await _tools())["getArchive"].output_schema is None


@pytest.mark.asyncio
async def test_empty_body_returns_status_and_etag():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"ETag": '"new-version"'})

    result = await _call(handler, "upload")

    assert _texts(result) == ['HTTP 200; etag: "new-version"']
    assert result.meta[RESPONSE_META_KEY]["headers"]["etag"] == '"new-version"'


@pytest.mark.asyncio
async def test_http_errors_still_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"message": "Precondition failed"})

    with pytest.raises(ToolError, match="412"):
        await _call(handler, "upload")
