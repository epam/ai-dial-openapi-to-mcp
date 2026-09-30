"""
Path parameter encoding for OpenAPI tools.

fastmcp percent-encodes every path parameter with ``quote(value, safe="")`` and also encodes
``.`` (``SKILL.md`` -> ``SKILL%2Emd``, ``a/b`` -> ``a%2Fb``). That blocks path traversal, but it
also breaks APIs whose path parameters are hierarchical paths. This module keeps ``.`` inside
values, and keeps ``/`` for parameters that opt in, while still rejecting traversal segments.
"""

import logging
from typing import Any
from urllib.parse import quote, urljoin

from fastmcp.utilities.openapi import HTTPRoute
from fastmcp.utilities.openapi.director import RequestDirector

logger = logging.getLogger(__name__)

ALLOW_RESERVED_EXTENSION = "x-mcp-allow-reserved"
_TRAVERSAL_SEGMENTS = frozenset({".", ".."})
_SLASH_NOTE = "May contain '/' to address a nested path; '.' and '..' segments are not allowed."


def encode_path_value(name: str, value: Any, allow_slash: bool) -> str:
    """
    Percent-encode one path parameter value.

    ``.`` is kept (quote treats it as unreserved), so file names survive; a value that is, or
    with ``allow_slash`` contains, a ``.``/``..`` segment is rejected because it would change
    which resource the URL addresses. An empty value is allowed (APIs use it for "root", e.g.
    listing a folder), but empty segments inside a slash-separated value are not: a leading
    ``//`` would make the URL a network-path reference to another host.
    """
    text = str(value)
    if text == "":
        return ""
    segments = text.split("/") if allow_slash else [text]
    for segment in segments:
        if segment in _TRAVERSAL_SEGMENTS or segment == "":
            raise ValueError(
                f"Invalid value for path parameter '{name}': empty, '.' and '..' segments "
                "are not allowed"
            )
    return "/".join(quote(segment, safe="") for segment in segments)


class PathParamRequestDirector(RequestDirector):
    """RequestDirector that keeps ``.`` in path values and ``/`` for opted-in parameters."""

    def __init__(self, director: RequestDirector, allow_reserved: frozenset[str] | bool):
        super().__init__(director._spec)
        self._allow_reserved = allow_reserved

    def _allows_slash(self, name: str) -> bool:
        if isinstance(self._allow_reserved, bool):
            return self._allow_reserved
        return name in self._allow_reserved

    def _build_url(self, path_template: str, path_params: dict[str, Any], base_url: str) -> str:
        url_path = path_template
        for name, value in path_params.items():
            placeholder = f"{{{name}}}"
            if placeholder in url_path:
                url_path = url_path.replace(
                    placeholder, encode_path_value(name, value, self._allows_slash(name))
                )
        return urljoin(base_url.rstrip("/") + "/", url_path.lstrip("/"))


def _resolve_ref(openapi_spec: dict[str, Any], node: Any) -> Any:
    """Resolve a local ``#/...`` $ref (parameters are often shared via components)."""
    ref = node.get("$ref") if isinstance(node, dict) else None
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return node
    target: Any = openapi_spec
    for key in ref[2:].split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        target = target.get(key) if isinstance(target, dict) else None
    return target


def allow_reserved_path_params(openapi_spec: dict[str, Any] | None, route: HTTPRoute) -> set[str]:
    """
    Names of the route's path parameters marked with ``x-mcp-allow-reserved: true`` or
    ``allowReserved: true`` in the raw spec (fastmcp does not keep either).
    """
    if not isinstance(openapi_spec, dict):
        return set()
    path_item = (openapi_spec.get("paths") or {}).get(route.path)
    if not isinstance(path_item, dict):
        return set()
    operation = path_item.get(route.method.lower())
    declared = list(path_item.get("parameters") or [])
    if isinstance(operation, dict):
        declared += list(operation.get("parameters") or [])

    allowed: set[str] = set()
    for raw in declared:
        param = _resolve_ref(openapi_spec, raw)
        if not isinstance(param, dict) or param.get("in") != "path":
            continue
        if param.get(ALLOW_RESERVED_EXTENSION) is True or param.get("allowReserved") is True:
            allowed.add(param.get("name", ""))
    allowed.discard("")
    return allowed


def apply_path_param_encoding(
    route: HTTPRoute,
    tool: Any,
    openapi_spec: dict[str, Any] | None = None,
    allow_reserved_all: bool = False,
) -> None:
    """Install the path-parameter director on a tool and document slash-capable parameters."""
    director = getattr(tool, "_director", None)
    if not isinstance(director, RequestDirector):
        logger.warning(
            "Cannot apply path parameter encoding for %s %s: tool has no request director",
            route.method,
            route.path,
        )
        return

    path_param_names = {p.name for p in route.parameters if p.location == "path"}
    allowed: frozenset[str] | bool = (
        True
        if allow_reserved_all
        else frozenset(allow_reserved_path_params(openapi_spec, route) & path_param_names)
    )
    tool._director = PathParamRequestDirector(director, allowed)

    slash_params = path_param_names if allowed is True else set(allowed or ())
    properties = tool.parameters.get("properties") if isinstance(tool.parameters, dict) else None
    if not slash_params or not isinstance(properties, dict):
        return
    for arg_name, mapping in (route.parameter_map or {}).items():
        if mapping.get("location") != "path" or mapping.get("openapi_name") not in slash_params:
            continue
        schema = properties.get(arg_name)
        if isinstance(schema, dict):
            description = schema.get("description")
            schema["description"] = (
                f"{description.strip()} {_SLASH_NOTE}"
                if isinstance(description, str) and description.strip()
                else _SLASH_NOTE
            )
