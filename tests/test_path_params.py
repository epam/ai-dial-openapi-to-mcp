"""Tests for path parameter encoding (dots, opt-in slashes, traversal rejection)."""

from typing import Any

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from dial_openapi_to_mcp.path_params import encode_path_value
from dial_openapi_to_mcp.server import _build_component_fn

BASE_URL = "https://api.example.com/api"


def _spec(path_param_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Paths API", "version": "1.0.0"},
        "servers": [{"url": BASE_URL}],
        "components": {
            "parameters": {
                "Path": {
                    "name": "path",
                    "in": "path",
                    "required": True,
                    "description": "Resource path.",
                    "schema": {"type": "string"},
                    **(path_param_extra or {}),
                }
            }
        },
        "paths": {
            "/files/{bucket}/{path}/files/{filePath}": {
                "parameters": [{"$ref": "#/components/parameters/Path"}],
                "get": {
                    "operationId": "getFile",
                    "parameters": [
                        {
                            "name": "bucket",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "filePath",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "allowReserved": True,
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/upload/{path}": {
                "parameters": [{"$ref": "#/components/parameters/Path"}],
                "put": {
                    "operationId": "upload",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }


class _Recorder:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})


async def _call(spec: dict[str, Any], tool: str, arguments: dict[str, Any]) -> str:
    recorder = _Recorder()
    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(recorder))
    mcp = FastMCP.from_openapi(
        openapi_spec=spec, client=client, mcp_component_fn=_build_component_fn(spec, None)
    )
    try:
        await mcp.call_tool(tool, arguments)
    finally:
        await client.aclose()
    assert len(recorder.urls) == 1
    return recorder.urls[0]


# ---------------------------------------------------------------------------
# encode_path_value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "allow_slash", "expected"),
    [
        ("SKILL.md", False, "SKILL.md"),
        ("a/b", False, "a%2Fb"),
        ("a/b.md", True, "a/b.md"),
        ("my skill/ü?#", True, "my%20skill/%C3%BC%3F%23"),
        ("a%2Fb", True, "a%252Fb"),
        (42, False, "42"),
    ],
)
def test_encode_path_value(value, allow_slash, expected):
    assert encode_path_value("p", value, allow_slash) == expected


@pytest.mark.parametrize(
    ("value", "allow_slash"),
    [
        ("..", False),
        (".", False),
        ("", False),
        ("a/../b", True),
        ("./a", True),
        ("a//b", True),
        ("/a", True),
        ("a/", True),
    ],
)
def test_encode_path_value_rejects_traversal_and_empty_segments(value, allow_slash):
    with pytest.raises(ValueError, match="path parameter 'p'"):
        encode_path_value("p", value, allow_slash)


# ---------------------------------------------------------------------------
# Through FastMCP tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_keeps_dots_but_encodes_slashes():
    url = await _call(
        _spec(), "getFile", {"bucket": "b1", "path": "skills/x", "filePath": "dir/SKILL.md"}
    )
    # filePath opts in with allowReserved; path (shared $ref parameter) does not.
    assert url == f"{BASE_URL}/files/b1/skills%2Fx/files/dir/SKILL.md"


@pytest.mark.asyncio
async def test_x_mcp_allow_reserved_on_referenced_path_level_parameter():
    url = await _call(
        _spec({"x-mcp-allow-reserved": True}),
        "getFile",
        {"bucket": "b1", "path": "skills/x", "filePath": "SKILL.md"},
    )
    assert url == f"{BASE_URL}/files/b1/skills/x/files/SKILL.md"


@pytest.mark.asyncio
async def test_env_var_allows_slashes_for_every_path_parameter(monkeypatch):
    monkeypatch.setenv("PATH_PARAMS_ALLOW_RESERVED", "true")
    url = await _call(
        _spec(), "getFile", {"bucket": "b1", "path": "skills/x", "filePath": "SKILL.md"}
    )
    assert url == f"{BASE_URL}/files/b1/skills/x/files/SKILL.md"


@pytest.mark.asyncio
async def test_traversal_is_rejected_before_any_request():
    with pytest.raises(ToolError):
        await _call(_spec(), "getFile", {"bucket": "b1", "path": "x", "filePath": "../secret"})


@pytest.mark.asyncio
async def test_upload_tools_use_the_same_path_encoding():
    url = await _call(
        _spec({"x-mcp-allow-reserved": True}),
        "upload",
        {"path": "skills/my-skill", "file": {"filename": "SKILL.md", "content": "x"}},
    )
    assert url == f"{BASE_URL}/upload/skills/my-skill"


@pytest.mark.asyncio
async def test_slash_capable_parameters_are_documented_in_tool_schema():
    spec = _spec()
    client = httpx.AsyncClient(base_url=BASE_URL)
    mcp = FastMCP.from_openapi(
        openapi_spec=spec, client=client, mcp_component_fn=_build_component_fn(spec, None)
    )
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    await client.aclose()

    properties = tools["getFile"].parameters["properties"]
    assert "May contain '/'" in properties["filePath"]["description"]
    assert "May contain '/'" not in properties["path"].get("description", "")
