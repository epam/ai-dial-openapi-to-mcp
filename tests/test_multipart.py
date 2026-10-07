"""Tests for multipart/form-data file uploads (format: binary properties)."""

import base64
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP

from dial_openapi_to_mcp.multipart import (
    BinaryProperty,
    binary_input_schema,
    find_binary_properties,
    to_file_parts,
)
from dial_openapi_to_mcp.server import _build_component_fn

BASE_URL = "https://api.example.com"


def _spec(body_schema: dict[str, Any], encoding: dict[str, Any] | None = None) -> dict[str, Any]:
    media: dict[str, Any] = {"schema": body_schema}
    if encoding:
        media["encoding"] = encoding
    return {
        "openapi": "3.0.0",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "servers": [{"url": BASE_URL}],
        "paths": {
            "/upload/{bucket}": {
                "put": {
                    "operationId": "upload",
                    "parameters": [
                        {
                            "name": "bucket",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": media},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }


SINGLE_FILE_BODY = {
    "type": "object",
    "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary"}},
}


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


async def _call(spec: dict[str, Any], tool: str, arguments: dict[str, Any]) -> httpx.Request:
    recorder = _Recorder()
    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(recorder))
    mcp = FastMCP.from_openapi(
        openapi_spec=spec, client=client, mcp_component_fn=_build_component_fn(spec, None)
    )
    await mcp.call_tool(tool, arguments)
    await client.aclose()
    assert len(recorder.requests) == 1
    return recorder.requests[0]


def _parts(request: httpx.Request) -> list[dict[str, Any]]:
    """Parse a multipart request body into [{name, filename, content_type, data}]."""
    raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=HTTP).parsebytes(raw + request.content)
    parts = []
    for part in message.iter_parts():
        parts.append(
            {
                "name": part.get_param("name", header="content-disposition"),
                "filename": part.get_filename(),
                "content_type": part.get("content-type"),
                "data": part.get_payload(decode=True),
            }
        )
    return parts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_find_binary_properties_single_array_and_encoding():
    body = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "format": "binary"},
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
            "note": {"type": "string"},
        },
    }
    found = find_binary_properties(body, {"file": {"contentType": "application/zip, text/plain"}})

    assert found == {
        "file": BinaryProperty("file", False, "application/zip"),
        "files": BinaryProperty("files", True, None),
    }


def test_to_file_parts_text_string_uses_field_name_and_guessed_type():
    prop = BinaryProperty("notes.txt", False)
    assert to_file_parts(prop, "hello") == [("notes.txt", b"hello", "text/plain")]


def test_to_file_parts_data_uri_is_decoded():
    payload = base64.b64encode(b"\x89PNG").decode()
    parts = to_file_parts(BinaryProperty("file", False), f"data:image/png;base64,{payload}")
    assert parts == [("file", b"\x89PNG", "image/png")]


def test_to_file_parts_object_with_base64_and_explicit_type():
    value = {
        "filename": "archive.zip",
        "content": base64.b64encode(b"PK\x03\x04").decode(),
        "encoding": "base64",
        "contentType": "application/zip",
    }
    parts = to_file_parts(BinaryProperty("file", False), value)
    assert parts == [("archive.zip", b"PK\x03\x04", "application/zip")]


def test_to_file_parts_plain_string_is_never_guessed_as_base64():
    # "SKILL" is valid base64 text; it must still be sent verbatim.
    assert to_file_parts(BinaryProperty("SKILL.md", False), "SKILL")[0][1] == b"SKILL"


def test_to_file_parts_json_string_file_object_is_parsed():
    value = '{"filename": "SKILL.md", "content": "# Skill\\n"}'
    assert to_file_parts(BinaryProperty("file", False), value) == [
        ("SKILL.md", b"# Skill\n", "text/markdown")
    ]


@pytest.mark.parametrize(
    "value",
    [
        '{"name": "x", "content": "y"}',
        '{"content": 1}',
        '["SKILL.md"]',
        "{not json",
        '{"filename": "a.txt"}',
    ],
)
def test_to_file_parts_other_json_strings_are_sent_verbatim(value):
    assert to_file_parts(BinaryProperty("data.json", False), value)[0][:2] == (
        "data.json",
        value.encode(),
    )


def test_to_file_parts_uses_spec_encoding_content_type():
    prop = BinaryProperty("file", False, "application/zip")
    assert to_file_parts(prop, {"content": "x"})[0][2] == "application/zip"


def test_to_file_parts_rejects_invalid_values():
    with pytest.raises(ValueError, match="base64"):
        to_file_parts(BinaryProperty("file", False), {"content": "%%%", "encoding": "base64"})
    with pytest.raises(ValueError, match="must be a string or an object"):
        to_file_parts(BinaryProperty("file", False), 42)


def test_binary_input_schema_keeps_description_and_supports_lists():
    schema = binary_input_schema(
        BinaryProperty("files", True), {"type": "array", "description": "Skill files."}
    )
    assert schema["type"] == "array"
    assert schema["description"].startswith("Skill files.")
    assert schema["items"]["anyOf"][0] == {"type": "string"}


# ---------------------------------------------------------------------------
# End-to-end through FastMCP tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_input_schema_accepts_file_objects():
    spec = _spec(SINGLE_FILE_BODY)
    client = httpx.AsyncClient(base_url=BASE_URL)
    mcp = FastMCP.from_openapi(
        openapi_spec=spec, client=client, mcp_component_fn=_build_component_fn(spec, None)
    )
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    await client.aclose()

    file_schema = tools["upload"].parameters["properties"]["file"]
    assert {"type": "string"} in file_schema["anyOf"]
    assert tools["createItem"].parameters["properties"]["name"] == {"type": "string"}


@pytest.mark.asyncio
async def test_upload_sends_real_file_part_with_filename():
    request = await _call(
        _spec(SINGLE_FILE_BODY),
        "upload",
        {"bucket": "b1", "file": {"filename": "SKILL.md", "content": "# Skill\n"}},
    )

    assert request.method == "PUT"
    assert request.url == f"{BASE_URL}/upload/b1"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert _parts(request) == [
        {
            "name": "file",
            "filename": "SKILL.md",
            "content_type": "text/markdown",
            "data": b"# Skill\n",
        }
    ]


@pytest.mark.asyncio
async def test_upload_accepts_file_object_serialized_as_string():
    request = await _call(
        _spec(SINGLE_FILE_BODY),
        "upload",
        {"bucket": "b1", "file": '{"filename": "SKILL.md", "content": "# Skill\\n"}'},
    )

    assert [(p["name"], p["filename"], p["data"]) for p in _parts(request)] == [
        ("file", "SKILL.md", b"# Skill\n")
    ]


@pytest.mark.asyncio
async def test_upload_array_sends_one_part_per_file_and_keeps_form_fields():
    body = {
        "type": "object",
        "properties": {
            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
            "comment": {"type": "string"},
        },
    }
    request = await _call(
        _spec(body),
        "upload",
        {
            "bucket": "b1",
            "comment": "v2",
            "files": [
                {"filename": "SKILL.md", "content": "---\nname: x\n---\n"},
                {"filename": "scripts/run.py", "content": "print(1)\n"},
            ],
        },
    )

    parts = _parts(request)
    assert sorted((p["name"], p["filename"] or "", p["data"]) for p in parts) == [
        ("comment", "", b"v2"),
        ("files", "SKILL.md", b"---\nname: x\n---\n"),
        ("files", "scripts/run.py", b"print(1)\n"),
    ]


@pytest.mark.asyncio
async def test_upload_uses_spec_encoding_content_type():
    request = await _call(
        _spec(SINGLE_FILE_BODY, encoding={"file": {"contentType": "application/zip"}}),
        "upload",
        {"bucket": "b1", "file": "data:;base64," + base64.b64encode(b"PK").decode()},
    )

    (part,) = _parts(request)
    assert part["content_type"] == "application/zip"
    assert part["data"] == b"PK"


@pytest.mark.asyncio
async def test_non_multipart_routes_keep_default_behaviour():
    request = await _call(_spec(SINGLE_FILE_BODY), "createItem", {"name": "a"})

    assert request.headers["content-type"] == "application/json"
    assert request.content == b'{"name":"a"}'
