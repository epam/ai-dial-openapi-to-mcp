"""
Multipart file uploads for OpenAPI tools.

fastmcp sends every multipart/form-data value as a plain form field (no filename), and tool
arguments are JSON, so ``format: binary`` properties can never reach an API as real file parts.
This module turns such arguments into file parts, driven only by the OpenAPI request body schema.
"""

import base64
import binascii
import logging
import mimetypes
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote_to_bytes

import httpx
from fastmcp.utilities.openapi import HTTPRoute
from fastmcp.utilities.openapi.director import RequestDirector, _query_scalar_to_str

logger = logging.getLogger(__name__)

MULTIPART_FORM_DATA = "multipart/form-data"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_DATA_URI_PREFIX = "data:"

_FileTuple = tuple[str, bytes, str]


@dataclass(frozen=True)
class BinaryProperty:
    """A multipart body property that carries file content."""

    name: str
    multiple: bool
    encoding_content_type: str | None = None


def _is_binary_schema(schema: Any) -> bool:
    return (
        isinstance(schema, dict)
        and schema.get("type") == "string"
        and schema.get("format") == "binary"
    )


def multipart_body_schema(route: HTTPRoute) -> dict[str, Any] | None:
    """Return the multipart/form-data body schema of a route, if it declares one."""
    if not route.request_body:
        return None
    for media_type, schema in route.request_body.content_schema.items():
        if media_type.split(";")[0].strip().lower() == MULTIPART_FORM_DATA:
            return schema if isinstance(schema, dict) else None
    return None


def find_binary_properties(
    body_schema: dict[str, Any], encoding: dict[str, Any] | None = None
) -> dict[str, BinaryProperty]:
    """Find ``format: binary`` properties (single or array) in a multipart body schema."""
    properties = body_schema.get("properties")
    if not isinstance(properties, dict):
        return {}

    encoding = encoding if isinstance(encoding, dict) else {}
    found: dict[str, BinaryProperty] = {}
    for name, schema in properties.items():
        if _is_binary_schema(schema):
            multiple = False
        elif (
            isinstance(schema, dict)
            and schema.get("type") == "array"
            and _is_binary_schema(schema.get("items"))
        ):
            multiple = True
        else:
            continue

        prop_encoding = encoding.get(name)
        content_type = prop_encoding.get("contentType") if isinstance(prop_encoding, dict) else None
        # encoding.contentType may list several types ("image/png, image/jpeg"); use the first.
        if isinstance(content_type, str) and content_type.strip():
            content_type = content_type.split(",")[0].strip()
        else:
            content_type = None
        found[name] = BinaryProperty(name, multiple, content_type)
    return found


def _decode_data_uri(value: str) -> tuple[bytes, str | None]:
    header, sep, payload = value[len(_DATA_URI_PREFIX) :].partition(",")
    if not sep:
        raise ValueError("Malformed data URI: missing ','")
    params = header.split(";")
    mime_type = params[0].strip() or None
    if "base64" in (p.strip().lower() for p in params[1:]):
        return _decode_base64(payload), mime_type
    return unquote_to_bytes(payload), mime_type


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"Invalid base64 content: {e}") from e


def _decode_content(content: str, encoding: str | None) -> tuple[bytes, str | None]:
    if content.startswith(_DATA_URI_PREFIX):
        return _decode_data_uri(content)
    if encoding == "base64":
        return _decode_base64(content), None
    if encoding not in (None, "text"):
        raise ValueError(f"Unsupported content encoding {encoding!r}; use 'base64' or 'text'")
    return content.encode("utf-8"), None


def to_file_part(prop: BinaryProperty, value: Any) -> _FileTuple:
    """Convert one tool argument value into an httpx file tuple (filename, bytes, content type)."""
    filename: str | None = None
    content_type: str | None = None
    if isinstance(value, str):
        data, uri_type = _decode_content(value, None)
    elif isinstance(value, dict):
        content = value.get("content")
        if not isinstance(content, str):
            raise ValueError(f"File '{prop.name}': 'content' must be a string")
        encoding = value.get("encoding")
        data, uri_type = _decode_content(content, encoding if isinstance(encoding, str) else None)
        filename = value.get("filename") if isinstance(value.get("filename"), str) else None
        content_type = (
            value.get("contentType") if isinstance(value.get("contentType"), str) else None
        )
    else:
        raise ValueError(
            f"File '{prop.name}' must be a string or an object with 'content', "
            f"got {type(value).__name__}"
        )

    filename = filename or prop.name
    content_type = (
        content_type
        or uri_type
        or prop.encoding_content_type
        or mimetypes.guess_type(filename)[0]
        or _DEFAULT_CONTENT_TYPE
    )
    return filename, data, content_type


def to_file_parts(prop: BinaryProperty, value: Any) -> list[_FileTuple]:
    """Convert a tool argument into one file part, or one per item for array properties."""
    if prop.multiple:
        items = value if isinstance(value, list) else [value]
        return [to_file_part(prop, item) for item in items]
    return [to_file_part(prop, value)]


_FILE_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["content"],
    "properties": {
        "filename": {
            "type": "string",
            "description": "File name sent in the multipart part (defaults to the field name).",
        },
        "content": {
            "type": "string",
            "description": "File content: plain text, a data: URI, or base64 when "
            "encoding is 'base64'.",
        },
        "contentType": {
            "type": "string",
            "description": "MIME type of the file (guessed from the file name if omitted).",
        },
        "encoding": {
            "type": "string",
            "enum": ["text", "base64"],
            "description": "How 'content' is encoded (default: text).",
        },
    },
    "additionalProperties": False,
}

_FILE_VALUE_DESCRIPTION = (
    "File upload. Pass the content as a string (plain text, or a data:<mime>;base64,... URI "
    "for binary data), or as an object {filename, content, contentType, encoding}."
)


def binary_input_schema(prop: BinaryProperty, original: Any) -> dict[str, Any]:
    """Tool input schema that accepts the supported file argument shapes."""
    file_value: dict[str, Any] = {"anyOf": [{"type": "string"}, _FILE_OBJECT_SCHEMA]}
    original_description = original.get("description") if isinstance(original, dict) else None
    description = _FILE_VALUE_DESCRIPTION
    if prop.multiple:
        description += " Pass a list to upload several files."
    if isinstance(original_description, str) and original_description.strip():
        description = f"{original_description.strip()}\n\n{description}"

    if prop.multiple:
        return {"type": "array", "items": file_value, "description": description}
    return {**file_value, "description": description}


class FileUploadRequestDirector(RequestDirector):
    """RequestDirector that sends ``format: binary`` multipart properties as real file parts."""

    def __init__(self, director: RequestDirector, binary_properties: dict[str, BinaryProperty]):
        super().__init__(director._spec)
        self._binary_properties = binary_properties

    def build(
        self,
        route: HTTPRoute,
        flat_args: dict[str, Any],
        base_url: str = "http://localhost",
    ) -> httpx.Request:
        request = super().build(route, flat_args, base_url)
        _, _, _, _, body = self._unflatten_arguments(route, flat_args)
        if not isinstance(body, dict):
            return request

        files: list[tuple[str, Any]] = []
        for name, value in body.items():
            prop = self._binary_properties.get(name)
            if prop is not None:
                files.extend((name, part) for part in to_file_parts(prop, value))
            elif isinstance(value, list):
                files.extend((name, (None, _query_scalar_to_str(item))) for item in value)
            else:
                files.append((name, (None, _query_scalar_to_str(value))))

        # Rebuild only the body: keep the URL, query and headers fastmcp produced, but drop the
        # Content-Type/Length it computed for the old body so httpx sets the new boundary.
        headers = httpx.Headers(request.headers)
        for header in ("content-type", "content-length", "transfer-encoding"):
            headers.pop(header, None)
        return httpx.Request(request.method, request.url, headers=headers, files=files)


def apply_file_uploads(
    route: HTTPRoute, tool: Any, openapi_spec: dict[str, Any] | None = None
) -> bool:
    """
    Enable file uploads on an OpenAPI tool whose multipart body has ``format: binary`` properties.

    Swaps the tool's request director and rewrites the matching input schema properties.
    Returns True if the tool was changed.
    """
    body_schema = multipart_body_schema(route)
    if body_schema is None:
        return False

    binary_properties = find_binary_properties(
        body_schema, _multipart_encoding(openapi_spec, route)
    )
    if not binary_properties:
        return False

    director = getattr(tool, "_director", None)
    if not isinstance(director, RequestDirector):
        logger.warning(
            "Cannot enable file uploads for %s %s: tool has no request director",
            route.method,
            route.path,
        )
        return False

    tool._director = FileUploadRequestDirector(director, binary_properties)

    properties = tool.parameters.get("properties") if isinstance(tool.parameters, dict) else None
    if isinstance(properties, dict):
        for arg_name, mapping in (route.parameter_map or {}).items():
            if mapping.get("location") != "body":
                continue
            prop = binary_properties.get(mapping.get("openapi_name", arg_name))
            if prop is not None and arg_name in properties:
                properties[arg_name] = binary_input_schema(prop, properties[arg_name])

    logger.debug(
        "Enabled file uploads for %s %s: fields=%s",
        route.method,
        route.path,
        sorted(binary_properties),
    )
    return True


def _multipart_encoding(
    openapi_spec: dict[str, Any] | None, route: HTTPRoute
) -> dict[str, Any] | None:
    """Read the multipart ``encoding`` object from the raw spec (fastmcp does not keep it)."""
    if not isinstance(openapi_spec, dict):
        return None
    operation = (openapi_spec.get("paths") or {}).get(route.path, {}).get(route.method.lower())
    if not isinstance(operation, dict):
        return None
    content = (operation.get("requestBody") or {}).get("content") or {}
    for media_type, media in content.items():
        if media_type.split(";")[0].strip().lower() == MULTIPART_FORM_DATA:
            encoding = media.get("encoding") if isinstance(media, dict) else None
            return encoding if isinstance(encoding, dict) else None
    return None
