"""
Free-form JSON request bodies.

When an operation's request body is an object schema without declared ``properties`` (for
example ``type: object, additionalProperties: true``), fastmcp exposes it as a single tool
argument named ``body`` and then sends ``{"body": <value>}`` on the wire. The upstream API
expects ``<value>`` itself, so this module unwraps that synthetic argument.
"""

import logging
from typing import Any

from fastmcp.utilities.openapi import HTTPRoute
from fastmcp.utilities.openapi.director import RequestDirector

logger = logging.getLogger(__name__)

_BODY_ARG = "body"


def has_free_form_body(route: HTTPRoute) -> bool:
    """True if the route's body is an object with no properties, exposed as one ``body`` arg."""
    request_body = route.request_body
    content_schema = getattr(request_body, "content_schema", None)
    if not content_schema:
        return False
    schema = content_schema[next(iter(content_schema))]
    if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("properties"):
        return False
    body_args = {
        name
        for name, mapping in (route.parameter_map or {}).items()
        if mapping.get("location") == "body"
    }
    return body_args == {_BODY_ARG}


class FreeFormBodyRequestDirector(RequestDirector):
    """RequestDirector that sends the ``body`` argument as the whole request body."""

    def __init__(self, director: RequestDirector):
        super().__init__(director._spec)
        self._wrapped = director

    def _build_url(self, path_template: str, path_params: dict[str, Any], base_url: str) -> str:
        # Keep the wrapped director's URL building (e.g. path parameter encoding).
        return self._wrapped._build_url(path_template, path_params, base_url)

    def _unflatten_arguments(self, route: HTTPRoute, flat_args: dict[str, Any]) -> Any:
        path, query, header, cookie, body = super()._unflatten_arguments(route, flat_args)
        if (
            isinstance(body, dict)
            and set(body) == {_BODY_ARG}
            and isinstance(body[_BODY_ARG], dict)
        ):
            body = body[_BODY_ARG]
        return path, query, header, cookie, body


def apply_free_form_body(route: HTTPRoute, tool: Any) -> bool:
    """Install the unwrapping director on a tool with a free-form body. True if changed."""
    if not has_free_form_body(route):
        return False
    director = getattr(tool, "_director", None)
    if not isinstance(director, RequestDirector):
        logger.warning(
            "Cannot unwrap free-form body for %s %s: tool has no request director",
            route.method,
            route.path,
        )
        return False
    tool._director = FreeFormBodyRequestDirector(director)
    return True
