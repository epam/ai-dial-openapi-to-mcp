"""
Response handling for OpenAPI tools.

fastmcp's ``OpenAPITool.run`` returns only the response body: non-JSON bodies are decoded as text
(binary content is corrupted), response headers such as ``ETag`` are dropped, and a non-JSON body
on a tool with an output schema fails MCP output validation. This module keeps fastmcp's request
building, captures the raw ``httpx.Response`` and rebuilds the tool result from it.
"""

import base64
import json
import logging
from contextvars import ContextVar
from typing import Any

import httpx
import jsonschema  # type: ignore[import-untyped]  # installed with mcp, which validates with it
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools import ToolResult
from fastmcp.utilities.openapi import HTTPRoute
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
)

logger = logging.getLogger(__name__)

RESPONSE_META_KEY = "dial.epam.com/response"
_WRAP_RESULT_FLAG = "x-fastmcp-wrap-result"

# Always returned when present, in addition to the headers the spec declares for the response.
_DEFAULT_EXPOSED_HEADERS = frozenset(
    {"etag", "last-modified", "location", "content-type", "content-length", "content-disposition"}
)
# Never returned, even when the spec declares them.
_HIDDEN_HEADERS = frozenset(
    {"set-cookie", "authorization", "proxy-authorization", "proxy-authenticate", "cookie"}
)
_TEXT_MEDIA_TYPES = frozenset(
    {
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-ndjson",
        "application/sql",
        "application/graphql",
    }
)

# Media types that say nothing about the content; such bodies are sniffed for text.
_UNTYPED_MEDIA_TYPES = frozenset({"", "application/octet-stream"})

_CAPTURED_RESPONSE: ContextVar[httpx.Response | None] = ContextVar(
    "captured_response", default=None
)


class _ResponseCapturingClient:
    """Proxy for the tool's httpx client that records the response of the request it sends."""

    def __init__(self, client: httpx.AsyncClient):
        self._wrapped_client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped_client, name)

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await self._wrapped_client.send(request, **kwargs)
        _CAPTURED_RESPONSE.set(response)
        return response


def _media_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def _is_json_media_type(media_type: str) -> bool:
    return media_type == "application/json" or media_type.endswith("+json")


def _is_text_media_type(media_type: str) -> bool:
    return (
        media_type.startswith("text/")
        or media_type in _TEXT_MEDIA_TYPES
        or media_type.endswith("+xml")
        or media_type.endswith("+yaml")
    )


def _decode_text(response: httpx.Response) -> str | None:
    try:
        return response.content.decode(response.encoding or "utf-8")
    except (UnicodeDecodeError, LookupError):
        return None


def _decode_untyped_text(response: httpx.Response) -> str | None:
    """
    Text for a body without a meaningful type (no Content-Type or application/octet-stream).

    Servers often send files that way (e.g. a stored ``SKILL.md``). Strict UTF-8 without NUL
    bytes is returned as text, which is lossless; anything else stays binary.
    """
    try:
        text = response.content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def _parse_json(response: httpx.Response) -> tuple[bool, Any]:
    try:
        return True, json.loads(response.content)
    except ValueError:
        return False, None


class ResponseAwareOpenAPITool(OpenAPITool):
    """OpenAPITool that returns non-JSON bodies intact, exposes response headers and validates."""

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        token = _CAPTURED_RESPONSE.set(None)
        try:
            try:
                base_result = await super().run(arguments)
            except Exception:
                # fastmcp parses successful bodies with response.json() and catches only
                # JSONDecodeError, so a binary body raises UnicodeDecodeError. After a 2xx
                # response, any failure comes from that body parsing: rebuild from the response.
                response = _CAPTURED_RESPONSE.get()
                if response is None or not response.is_success:
                    raise
            else:
                response = _CAPTURED_RESPONSE.get()
                if response is None:
                    return base_result
        finally:
            _CAPTURED_RESPONSE.reset(token)
        return self._build_result(response)

    def _build_result(self, response: httpx.Response) -> ToolResult:
        headers = self._exposed_headers(response)
        meta = {RESPONSE_META_KEY: {"status_code": response.status_code, "headers": headers}}
        summary = _summary_block(response.status_code, headers)
        wrap = bool(self.output_schema and self.output_schema.get(_WRAP_RESULT_FLAG))
        media_type = _media_type(response)

        if not response.content:
            return ToolResult(content=[summary], meta=meta)

        is_json, parsed = (
            _parse_json(response)
            if _is_json_media_type(media_type) or not media_type
            else (False, None)
        )
        if is_json:
            structured = self._structure_json(parsed)
            self._validate(structured)
            content: list[ContentBlock] = [
                TextContent(type="text", text=json.dumps(parsed, ensure_ascii=False))
            ]
            return ToolResult(content=content + [summary], structured_content=structured, meta=meta)

        if _is_text_media_type(media_type):
            text = _decode_text(response)
        elif media_type in _UNTYPED_MEDIA_TYPES:
            text = _decode_untyped_text(response)
        else:
            text = None
        if text is not None:
            return ToolResult(
                content=[TextContent(type="text", text=text), summary],
                structured_content={"result": text} if wrap else None,
                meta=meta,
            )

        data = base64.b64encode(response.content).decode("ascii")
        mime_type = media_type or "application/octet-stream"
        return ToolResult(
            content=[_binary_block(response, data, mime_type), summary],
            structured_content={"result": data} if wrap else None,
            meta=meta,
        )

    def _structure_json(self, parsed: Any) -> dict[str, Any]:
        # Same shaping as fastmcp's OpenAPITool.run.
        if self.output_schema is not None and self.output_schema.get(_WRAP_RESULT_FLAG):
            return {"result": parsed}
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    def _validate(self, structured: dict[str, Any]) -> None:
        # Results that carry _meta skip the MCP SDK's output validation, so it is done here.
        if self.output_schema is None or not getattr(self, "_validate_output", True):
            return
        try:
            jsonschema.validate(instance=structured, schema=self.output_schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Output validation error: {e.message}") from e

    def _exposed_headers(self, response: httpx.Response) -> dict[str, str]:
        declared: frozenset[str] = getattr(self, "_declared_response_headers", frozenset())
        return {
            name: value
            for name, value in response.headers.items()
            if name.lower() not in _HIDDEN_HEADERS
            and (name.lower() in _DEFAULT_EXPOSED_HEADERS or name.lower() in declared)
        }


def _summary_block(status_code: int, headers: dict[str, str]) -> TextContent:
    # Many MCP clients show only content blocks, not _meta; keep status and headers visible.
    rendered = ", ".join(f"{name}: {value}" for name, value in headers.items())
    return TextContent(
        type="text", text=f"HTTP {status_code}" + (f"; {rendered}" if rendered else "")
    )


def _binary_block(response: httpx.Response, data: str, mime_type: str) -> ContentBlock:
    if mime_type.startswith("image/"):
        return ImageContent(type="image", data=data, mimeType=mime_type)
    if mime_type.startswith("audio/"):
        return AudioContent(type="audio", data=data, mimeType=mime_type)
    # The query string is dropped: it may carry values that should not be echoed back.
    uri = str(response.request.url.copy_with(query=None, fragment=None))
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(uri=uri, mimeType=mime_type, blob=data),  # type: ignore[arg-type]
    )


def _success_responses(route: HTTPRoute) -> dict[str, Any]:
    return {code: info for code, info in (route.responses or {}).items() if code.startswith("2")}


def _declared_json_success(route: HTTPRoute) -> bool:
    """
    Whether a 2xx response declares a JSON document. A JSON media type whose schema is just
    ``type: string, format: binary`` describes file content, not a JSON document.
    """
    for info in _success_responses(route).values():
        for media_type, schema in (info.content_schema or {}).items():
            normalized = media_type.split(";")[0].strip().lower()
            if _is_json_media_type(normalized) and not _is_binary_string_schema(schema):
                return True
    return False


def _is_binary_string_schema(schema: Any) -> bool:
    return (
        isinstance(schema, dict)
        and schema.get("type") == "string"
        and schema.get("format") == "binary"
    )


def _declared_response_headers(openapi_spec: dict[str, Any] | None, route: HTTPRoute) -> set[str]:
    """Header names declared on the operation's 2xx responses (fastmcp does not keep them)."""
    if not isinstance(openapi_spec, dict):
        return set()
    operation = (openapi_spec.get("paths") or {}).get(route.path, {}).get(route.method.lower())
    if not isinstance(operation, dict):
        return set()
    names: set[str] = set()
    for code, response in (operation.get("responses") or {}).items():
        if str(code).startswith("2") and isinstance(response, dict):
            names.update(name.lower() for name in (response.get("headers") or {}))
    return names


def _x_mcp(route: HTTPRoute) -> dict[str, Any]:
    extensions = getattr(route, "extensions", None)
    x_mcp = extensions.get("x-mcp") if isinstance(extensions, dict) else None
    return x_mcp if isinstance(x_mcp, dict) else {}


def apply_response_handling(
    route: HTTPRoute,
    tool: Any,
    openapi_spec: dict[str, Any] | None = None,
    validate_output: bool = True,
) -> bool:
    """
    Make an OpenAPI tool return full responses (see module docstring).

    Output validation is on by default; ``MCP_VALIDATE_OUTPUT=false`` (passed in as
    ``validate_output``) or ``x-mcp: {validateOutput: false}`` on the operation turns it off and
    removes the tool's output schema. Operations whose success responses declare no JSON body
    get no output schema at all.
    """
    if not isinstance(tool, OpenAPITool):
        return False

    x_mcp_validate = _x_mcp(route).get("validateOutput")
    validate = x_mcp_validate if isinstance(x_mcp_validate, bool) else validate_output
    if not validate or not _declared_json_success(route):
        tool.output_schema = None

    # fastmcp constructs OpenAPITool directly, so the subclass is applied to the built tool; it
    # adds no fields, so the instance layout is unchanged. The extra attributes are plain
    # instance attributes (not pydantic private attributes, which the swap would not populate).
    tool.__class__ = ResponseAwareOpenAPITool
    setattr(tool, "_client", _ResponseCapturingClient(tool._client))
    setattr(tool, "_validate_output", validate)
    setattr(
        tool,
        "_declared_response_headers",
        frozenset(_declared_response_headers(openapi_spec, route)),
    )
    return True
