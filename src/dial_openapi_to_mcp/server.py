#!/usr/bin/env python3
"""
OpenAPI to MCP server.
Dynamically routes MCP protocol requests based on OpenAPI specs in _meta.openapi
"""

import hashlib
import json
import logging
import os
from contextvars import ContextVar
from importlib import resources
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.lifespan import lifespan
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools import Tool, ToolResult
from starlette.requests import Request
from starlette.responses import JSONResponse

from .cache import CacheEntry, MCPCache
from .multipart import apply_file_uploads
from .path_params import apply_path_param_encoding
from .responses import apply_response_handling
from .spec_fetch import (
    fetch_openapi_spec,
    parse_spec_text,
    resolve_base_url,
    spec_text_to_json,
)
from .swagger2 import convert_swagger2_to_openapi30 as _convert_swagger2_to_openapi30
from .telemetry import setup_telemetry

_REQUEST_CREDENTIAL: ContextVar[tuple[str, str] | None] = ContextVar(
    "request_credential", default=None
)
_REQUEST_HEADERS: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "request_headers", default=()
)
_MCP_CONTROL_HEADERS = (
    "x-meta",
    "x-openapi-url",
    "x-base-url",
    "x-extra-headers",
    "x-dial-application-id",
    "api-key",
)
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "api-key",
    "x-api-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
}
_BLOCKED_FORWARDED_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
}

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

_mcp_cache = MCPCache(
    max_size=int(os.getenv('MCP_CACHE_MAX_SIZE', '100')),
    ttl_seconds=int(os.getenv('MCP_CACHE_TTL', '3600')),
    cleanup_interval=int(os.getenv('MCP_CACHE_CLEANUP_INTERVAL', '300')),
)

APPLICATION_SCHEMA: Dict[str, Any] = json.loads(
    resources.files("dial_openapi_to_mcp")
    .joinpath("application_schema.json")
    .read_text(encoding="utf-8")
)


# (base URL, raw spec text) digest -> api_id, so repeat requests with the same spec text
# skip parsing. Holds only short strings; the MCP entries themselves live in _mcp_cache.
_API_ID_MEMO: Dict[str, str] = {}
_API_ID_MEMO_MAX = 1024


def get_api_id(spec: Dict[str, Any], base_url: Optional[str]) -> str:
    """Generate an opaque key from inputs that affect the generated MCP definition."""
    base = json.dumps(spec, sort_keys=True)
    suffix = base_url or ""
    fingerprint = hashlib.sha256(f"{base}|{suffix}".encode()).hexdigest()
    return fingerprint[:16]


def _safe_destination(url: httpx.URL) -> str:
    """Return a non-sensitive destination description for logs."""
    return f"{url.scheme}://{url.host}{url.path}"


def _resolve_blocked_forwarded_headers() -> set[str]:
    """Operator-overridable block set; unset OUTBOUND_HEADER_BLOCKLIST keeps the default."""
    raw = os.environ.get("OUTBOUND_HEADER_BLOCKLIST")
    if raw is None:
        return _BLOCKED_FORWARDED_HEADERS
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def _validate_extra_headers(raw_headers: Any) -> list[dict[str, str]]:
    """Reject blocked forwarded headers; if OUTBOUND_HEADER_ALLOWLIST is explicitly set,
    also require forwarded headers to be named in it."""
    if not isinstance(raw_headers, list):
        raise ValueError("extra_headers must be a JSON array")

    blocked = _resolve_blocked_forwarded_headers()
    allowlist_raw = os.environ.get("OUTBOUND_HEADER_ALLOWLIST")
    allowlist = (
        {name.strip().lower() for name in allowlist_raw.split(",") if name.strip()}
        if allowlist_raw is not None
        else None
    )

    validated = []
    for entry in raw_headers:
        if not isinstance(entry, dict):
            raise ValueError("extra_headers entries must be objects")
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("extra_headers entries require string name and value")
        normalized_name = name.lower()
        if normalized_name in blocked or (
            allowlist is not None and normalized_name not in allowlist
        ):
            raise ValueError(f"Forwarding header {name!r} is not permitted")
        validated.append({"name": name, "value": value})
    return validated


def _normalize_spec_to_json(spec: Any) -> str:
    """Convert an OpenAPI spec (dict, JSON string, or YAML string) to a JSON string."""
    if isinstance(spec, dict):
        return json.dumps(spec)
    if isinstance(spec, str):
        stripped = spec.strip()
        if stripped.startswith('{'):
            return spec
        try:
            return spec_text_to_json(stripped)
        except Exception as e:
            raise ValueError(f"Cannot parse spec as JSON or YAML: {e}") from e
    raise ValueError(f"Unsupported spec type: {type(spec)}")


class DialCredentialsError(Exception):
    """Raised when ai_dial_config.external_service is set but credentials can't be resolved."""

    def __init__(
        self,
        message: str,
        external_service: str,
        status_code: int = 500,
        www_authenticate: Optional[str] = None,
        scope: Optional[str] = None,
    ):
        super().__init__(message)
        self.external_service = external_service
        self.status_code = status_code
        self.www_authenticate = www_authenticate
        self.scope = scope


async def _fetch_dial_credentials(
    dial_url: str,
    application: str,
    external_service: str,
    per_request_key: str,
) -> Dict[str, Any]:
    """
    1. GET /v1/{application} — verify external_service is configured (application = "applications/{bucket}/{id}").
    2. POST /v1/ops/external-service/credentials — retrieve the live credential header.
    Returns {"header_name": str, "header_value": str, "expires_at": int|None}.

    Raises DialCredentialsError with status_code=404 when external_service isn't configured
    on the application, and status_code=401 (with a www_authenticate challenge) when it's
    configured but DIAL core has no stored credential for it yet (the user needs to log in).
    """
    base = dial_url.rstrip("/")
    auth = {"Api-Key": per_request_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        app_resp = await client.get(f"{base}/v1/{application}", headers=auth)
        app_resp.raise_for_status()
        external_services = app_resp.json().get("external_services") or {}
        if external_service not in external_services:
            msg = (
                f"External service {external_service!r} not found in application "
                f"{application!r} external_services registry"
            )
            logger.warning(msg)
            raise DialCredentialsError(msg, external_service, status_code=404)

        scope = f"{application}/external_services/{external_service}"
        try:
            cred_resp = await client.post(
                f"{base}/v1/ops/external-service/credentials",
                headers=auth,
                json={"url": scope},
            )
            cred_resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                www_authenticate = (
                    f'DIAL-External-Service url="{scope}", method="external-service/signin"'
                )
                msg = f"Sign-in required for external service {external_service!r}."
                logger.warning(msg)
                raise DialCredentialsError(
                    msg,
                    external_service,
                    status_code=401,
                    www_authenticate=www_authenticate,
                    scope=scope,
                ) from e
            raise

        data = cred_resp.json()
        return {
            "header_name": data["header_name"],
            "header_value": data["header_value"],
            "expires_at": data.get("expires_at"),
        }


async def _resolve_dial_credentials(request: Request) -> Optional[Dict[str, Any]]:
    """
    If ai_dial_config contains external_service and the x-dial-application-id header is
    present, fetch live credentials from DIAL core. Credentials are always fetched fresh
    because the result is bound to the per-request API key. Returns None only when DIAL
    credential mode is not active (external_service not requested). Raises
    DialCredentialsError when external_service is requested but cannot be resolved.
    """
    try:
        body = await request.json()
    except Exception:
        return None
    meta = (body.get("params") or {}).get("_meta") or {}
    ai_dial = meta.get("ai_dial_config") or {}

    external_service = ai_dial.get("external_service")
    if not external_service:
        return None

    application = request.headers.get("x-dial-application-id")  # "applications/{bucket}/{id}"
    if not application:
        msg = "ai_dial_config.external_service present but x-dial-application-id header is missing"
        logger.error(msg)
        raise DialCredentialsError(msg, external_service)

    dial_url = os.getenv("DIAL_URL") or os.getenv("DIAL_CORE_URL")
    if not dial_url:
        msg = "ai_dial_config.external_service present but DIAL_URL env var is not set"
        logger.error(msg)
        raise DialCredentialsError(msg, external_service)

    per_request_key = request.headers.get("api-key") or request.headers.get("Api-Key")
    if not per_request_key:
        msg = "ai_dial_config.external_service present but no Api-Key header found"
        logger.error(msg)
        raise DialCredentialsError(msg, external_service)

    try:
        creds = await _fetch_dial_credentials(
            dial_url, application, external_service, per_request_key
        )
    except DialCredentialsError:
        raise
    except httpx.HTTPStatusError as e:
        msg = f"Failed to fetch DIAL credentials for {external_service!r}: DIAL core returned {e.response.status_code} {e.response.reason_phrase}"
        logger.error(msg)
        raise DialCredentialsError(msg, external_service) from e
    except Exception as e:
        msg = f"Failed to fetch DIAL credentials for {external_service!r}: {e}"
        logger.error(msg)
        raise DialCredentialsError(msg, external_service) from e

    return creds


def _fetch_sends_auth() -> bool:
    """OPENAPI_FETCH_SEND_AUTH=true sends extra_headers and the DIAL credential on the schema GET."""
    return os.environ.get("OPENAPI_FETCH_SEND_AUTH", "").strip().lower() in {"1", "true", "yes"}


async def _extract_openapi_url(request: Request) -> Optional[str]:
    """Find a schema URL: X-OPENAPI-URL header > ai_dial_config.openapi_url > _meta.openapi_url."""
    header_url = request.headers.get("x-openapi-url")
    if header_url and header_url.strip():
        return header_url.strip()
    try:
        request_body = await request.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    params = request_body.get("params") if isinstance(request_body, dict) else None
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict):
        return None
    ai_dial_config = meta.get("ai_dial_config")
    candidates = [
        ai_dial_config.get("openapi_url") if isinstance(ai_dial_config, dict) else None,
        meta.get("openapi_url"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def _load_spec_from_url(
    request: Request, spec_url: str, base_url: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Fetch the schema (JSON or YAML) and return it as JSON text with its base URL.

    By default the fetch is anonymous. With OPENAPI_FETCH_SEND_AUTH=true it carries the same
    forwarded headers and DIAL external-service credential as the API calls, so protected
    schema endpoints work; the schema host then receives those credentials.
    """
    fetch_headers: list[tuple[str, str]] = []
    if _fetch_sends_auth():
        fetch_headers = [
            (entry["name"], entry["value"]) for entry in await _extract_extra_headers(request)
        ]
        try:
            credential = await _resolve_dial_credentials(request)
        except DialCredentialsError as error:
            logger.warning("Fetching OpenAPI spec without DIAL credential: %s", error)
            credential = None
        if credential and isinstance(credential.get("header_name"), str):
            fetch_headers.append((credential["header_name"], str(credential.get("header_value"))))

    logger.debug(
        "Fetching OpenAPI spec from %s (auth %s)",
        _safe_destination(httpx.URL(spec_url)),
        "sent" if fetch_headers else "not sent",
    )
    spec_json = await fetch_openapi_spec(spec_url, fetch_headers)
    spec_dict = json.loads(spec_json)
    resolved_base = resolve_base_url(
        spec_dict, spec_url, base_url or _base_url_from_spec(spec_dict)
    )
    return spec_json, resolved_base


async def _extract_spec_from_request(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the OpenAPI spec for a request.
    Priority: inline spec (X-META header, ai_dial_config.openapi, _meta.openapi) >
    schema URL (X-OPENAPI-URL header, ai_dial_config.openapi_url, _meta.openapi_url).
    Returns: (OpenAPI spec as JSON string or None, base_url or None)
    Raises ValueError (SpecFetchError) when a schema URL is given but cannot be loaded.
    """
    spec, base_url = await _extract_inline_spec(request)
    if not _is_empty_spec_json(spec):
        return spec, base_url
    spec_url = await _extract_openapi_url(request)
    if not spec_url:
        return spec, base_url
    return await _load_spec_from_url(request, spec_url, base_url)


async def _extract_inline_spec(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """Extract an inline OpenAPI spec from the X-META header or MCP metadata."""
    base_url = None
    spec = None

    for header_name, header_value in request.headers.items():
        name_lower = header_name.lower()
        if name_lower == "x-meta":
            logger.debug("Found OpenAPI spec in X-META header")
            spec = header_value
        if name_lower == "x-base-url":
            logger.debug("Found base URL in X-BASE-URL header")
            base_url = header_value

    if spec and base_url:
        return spec, base_url

    try:
        request_body = await request.json()
        params = request_body.get("params") or {}
        if "_meta" in params and isinstance(params["_meta"], dict):
            meta = params["_meta"]
            if not base_url:
                ai_dial_config = meta.get("ai_dial_config")
                if isinstance(ai_dial_config, dict):
                    base_url = (
                        ai_dial_config.get("base_url")
                        or ai_dial_config.get("baseurl")
                        or ai_dial_config.get("baseURL")
                    )
                    if base_url:
                        logger.debug("Found base URL in DIAL metadata")
            if not base_url:
                base_url = meta.get("base_url") or meta.get("baseurl") or meta.get("baseURL")
                if base_url:
                    logger.debug("Found base URL in request metadata")

            if not spec:
                ai_dial_config = meta.get("ai_dial_config")
                if isinstance(ai_dial_config, dict) and "openapi" in ai_dial_config:
                    raw = ai_dial_config["openapi"]
                    logger.debug("Found OpenAPI spec in DIAL metadata type=%s", type(raw).__name__)
                    return _normalize_spec_to_json(raw), base_url
                elif "openapi" in meta:
                    raw = meta["openapi"]
                    logger.debug(
                        "Found OpenAPI spec in request metadata type=%s", type(raw).__name__
                    )
                    return _normalize_spec_to_json(raw), base_url
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Could not extract OpenAPI metadata from request body")

    return spec, base_url


def _is_empty_spec_json(spec_json: Optional[str]) -> bool:
    """True when no spec was provided, or it parses to an empty JSON object ({})."""
    if not spec_json:
        return True
    try:
        parsed = json.loads(spec_json)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and not parsed


def _base_url_from_spec(openapi_spec: Dict[str, Any]) -> Optional[str]:
    servers = openapi_spec.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        if isinstance(first, dict) and first.get("url"):
            return first["url"]
        if isinstance(first, str):
            return first

    if openapi_spec.get("swagger") == "2.0" or "swagger" in openapi_spec:
        host = openapi_spec.get("host")
        if host:
            schemes = openapi_spec.get("schemes") or []
            scheme = schemes[0] if schemes else "https"
            base_path = openapi_spec.get("basePath", "")
            if base_path and not base_path.startswith("/"):
                base_path = f"/{base_path}"
            return f"{scheme}://{host}{base_path}"

    if openapi_spec.get("x-base-url"):
        return openapi_spec["x-base-url"]

    return None


def _normalize_base_url(base_url: Optional[str]) -> Optional[str]:
    """Normalize a caller-supplied base URL. Destination restriction is expected
    to be enforced by external network egress controls, not by this service."""
    if not base_url:
        return None
    normalized = base_url.strip()
    if not normalized:
        return None
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized


def _collect_x_mcp_overrides(
    openapi_spec: Dict[str, Any],
) -> Tuple[Optional[Dict[str, str]], Optional[Callable[[Any, Any], None]]]:
    """
    Collect optional x-mcp overrides from the OpenAPI spec.

    Supports:
      - x-mcp.name: override component name (via mcp_names) when operationId exists
      - x-mcp.description: override component description
      - x-mcp.parameters: { paramName: { description } } (tool input schema only)
    """
    mcp_names: Dict[str, str] = {}
    found_overrides = False

    paths = openapi_spec.get("paths")
    if isinstance(paths, dict):
        for _, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(method, str) or method.lower() not in {
                    "get",
                    "put",
                    "post",
                    "delete",
                    "options",
                    "head",
                    "patch",
                    "trace",
                }:
                    continue
                if not isinstance(operation, dict):
                    continue
                x_mcp = operation.get("x-mcp")
                if not isinstance(x_mcp, dict):
                    continue
                found_overrides = True
                override_name = x_mcp.get("name")
                operation_id = operation.get("operationId")
                if override_name and operation_id:
                    mcp_names[operation_id] = override_name

    if not found_overrides:
        return None, None

    def _apply_x_mcp_overrides(route: Any, component: Any) -> None:
        x_mcp = None
        extensions = getattr(route, "extensions", None)
        if isinstance(extensions, dict):
            x_mcp = extensions.get("x-mcp")
        if not isinstance(x_mcp, dict):
            return

        override_description = x_mcp.get("description")
        if isinstance(override_description, str) and override_description.strip():
            component.description = override_description

        param_overrides = x_mcp.get("parameters")
        if not isinstance(param_overrides, dict):
            return

        parameters_schema = getattr(component, "parameters", None)
        if not isinstance(parameters_schema, dict):
            return

        properties = parameters_schema.get("properties")
        if not isinstance(properties, dict):
            return

        for param_name, override in param_overrides.items():
            if not isinstance(override, dict):
                continue
            override_param_desc = override.get("description")
            if (
                isinstance(override_param_desc, str)
                and param_name in properties
                and isinstance(properties[param_name], dict)
            ):
                properties[param_name]["description"] = override_param_desc

    return (mcp_names or None), _apply_x_mcp_overrides


def _build_component_fn(
    openapi_spec: Dict[str, Any],
    x_mcp_component_fn: Optional[Callable[[Any, Any], None]],
) -> Callable[[Any, Any], None]:
    """
    Chain x-mcp overrides with tool fixes that apply to every spec (path encoding, uploads,
    response handling).
    """
    allow_reserved_all = _path_params_allow_reserved()
    validate_output = _validate_output()

    def _customize_component(route: Any, component: Any) -> None:
        if x_mcp_component_fn is not None:
            x_mcp_component_fn(route, component)
        if not isinstance(component, OpenAPITool):
            return
        # Order matters: the upload director wraps the path director and reuses its URL building.
        fixes: tuple[tuple[str, Callable[[], Any]], ...] = (
            (
                "path parameter encoding",
                lambda: apply_path_param_encoding(
                    route, component, openapi_spec, allow_reserved_all
                ),
            ),
            ("file uploads", lambda: apply_file_uploads(route, component, openapi_spec)),
            (
                "response handling",
                lambda: apply_response_handling(route, component, openapi_spec, validate_output),
            ),
        )
        for fix_name, apply_fix in fixes:
            try:
                apply_fix()
            except Exception:
                # fastmcp would swallow this and keep the tool without the fix.
                logger.warning(
                    "Failed to apply %s for %s %s",
                    fix_name,
                    getattr(route, "method", "?"),
                    getattr(route, "path", "?"),
                    exc_info=True,
                )

    return _customize_component


def _path_params_allow_reserved() -> bool:
    """PATH_PARAMS_ALLOW_RESERVED=true keeps '/' in every path parameter of every spec."""
    return os.environ.get("PATH_PARAMS_ALLOW_RESERVED", "").strip().lower() in {"1", "true", "yes"}


def _validate_output() -> bool:
    """MCP_VALIDATE_OUTPUT=false stops validating tool results against the spec's schemas."""
    return os.environ.get("MCP_VALIDATE_OUTPUT", "").strip().lower() not in {"0", "false", "no"}


def _dereference_schemas() -> bool:
    """MCP_DEREFERENCE_SCHEMAS=false keeps $ref/$defs in tool schemas instead of inlining them.

    Inlining (the default) is what FastMCP does out of the box, but deeply recursive specs
    can expand to 100+ MB of schemas and take about a minute per build.
    """
    return os.environ.get("MCP_DEREFERENCE_SCHEMAS", "").strip().lower() not in {"0", "false", "no"}


async def _build_extended_openapi_spec(
    openapi_spec: Dict[str, Any],
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Generate an OpenAPI spec with x-mcp fields based on FastMCP defaults.

    The generated x-mcp fields include:
      - name: default tool name
      - description: default tool description
      - parameters.*.description: inferred parameter descriptions
    """
    spec_copy = json.loads(json.dumps(openapi_spec))

    def _apply_x_mcp_from_operation(
        operation: Dict[str, Any],
        name: str,
        description: str,
        input_schema: Optional[Dict[str, Any]],
        output_schema: Optional[Dict[str, Any]],
        tags: Optional[Sequence[str]],
        param_descriptions: Dict[str, str],
    ) -> None:
        x_mcp = operation.get("x-mcp")
        if not isinstance(x_mcp, dict):
            x_mcp = {}
            operation["x-mcp"] = x_mcp
        if overwrite or "name" not in x_mcp:
            x_mcp["name"] = name
        if overwrite or "description" not in x_mcp:
            x_mcp["description"] = description
        if (overwrite or "inputSchema" not in x_mcp) and isinstance(input_schema, dict):
            x_mcp["inputSchema"] = _inline_refs(input_schema)
        if (overwrite or "outputSchema" not in x_mcp) and isinstance(output_schema, dict):
            x_mcp["outputSchema"] = _inline_refs(output_schema)
        if tags and (overwrite or "tags" not in x_mcp):
            x_mcp["tags"] = list(tags)
        if param_descriptions:
            param_overrides = x_mcp.get("parameters")
            if not isinstance(param_overrides, dict):
                param_overrides = {}
                x_mcp["parameters"] = param_overrides
            for param_name, param_desc in param_descriptions.items():
                if param_name not in param_overrides:
                    param_overrides[param_name] = {}
                if not isinstance(param_overrides[param_name], dict):
                    continue
                if overwrite or "description" not in param_overrides[param_name]:
                    param_overrides[param_name]["description"] = param_desc

    def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
        def _resolve_ref(node: Any) -> Any:
            if isinstance(node, dict):
                if "$ref" in node and isinstance(node["$ref"], str):
                    ref = node["$ref"]
                    if ref.startswith("#/components/schemas/"):
                        ref_name = ref.split("/")[-1]
                        component = spec_copy.get("components", {}).get("schemas", {}).get(ref_name)
                        if isinstance(component, dict):
                            return _resolve_ref(component)
                return {k: _resolve_ref(v) for k, v in node.items() if k != "$ref"}
            if isinstance(node, list):
                return [_resolve_ref(item) for item in node]
            return node

        return _resolve_ref(schema)

    base_url = _base_url_from_spec(spec_copy) or "http://localhost"
    client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
    try:
        mcp_instance = FastMCP.from_openapi(
            openapi_spec=spec_copy,
            client=client,
            # Always inline: _inline_refs below only understands #/components/schemas refs, so
            # kept #/$defs refs would be silently dropped from the generated x-mcp schemas.
            dereference_schemas=True,
        )

        tools_list = await mcp_instance.list_tools()
        tools_dict = {t.name: t for t in tools_list}

        for tool in tools_dict.values():
            route = getattr(tool, "_route", None)
            if route is None:
                continue
            path = getattr(route, "path", None)
            method = getattr(route, "method", None)
            if not path or not method:
                continue
            method_key = method.lower()

            paths = spec_copy.get("paths")
            if not isinstance(paths, dict):
                continue
            path_item = paths.get(path)
            if not isinstance(path_item, dict):
                continue
            operation = path_item.get(method_key)
            if not isinstance(operation, dict):
                continue

            param_descriptions: Dict[str, str] = {}
            parameters_schema = getattr(tool, "parameters", None)
            properties = (
                parameters_schema.get("properties") if isinstance(parameters_schema, dict) else None
            )
            if isinstance(properties, dict):
                for param_name, schema in properties.items():
                    if not isinstance(schema, dict):
                        continue
                    param_desc = schema.get("description")
                    if isinstance(param_desc, str) and param_desc.strip():
                        param_descriptions[param_name] = param_desc

            tool_tags = getattr(tool, "tags", None)
            output_schema = getattr(tool, "output_schema", None)
            _apply_x_mcp_from_operation(
                operation=operation,
                name=tool.name,
                description=tool.description or "",
                input_schema=parameters_schema if isinstance(parameters_schema, dict) else None,
                output_schema=output_schema if isinstance(output_schema, dict) else None,
                tags=list(tool_tags) if isinstance(tool_tags, (set, list, tuple)) else None,
                param_descriptions=param_descriptions,
            )
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    return spec_copy


async def _extract_extra_headers(request: Request) -> list[dict[str, str]]:
    """Extract and validate request-scoped headers without retaining their values."""
    meta: dict[str, Any] = {}
    try:
        request_body = await request.json()
        if isinstance(request_body, dict):
            params = request_body.get("params", {})
            if isinstance(params, dict):
                candidate = params.get("_meta") or params.get("meta") or {}
                if isinstance(candidate, dict):
                    meta = candidate
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.debug("Request metadata was not available")

    raw_extra_headers: Any = request.headers.get("x-extra-headers")
    if raw_extra_headers is None:
        ai_dial = meta.get("ai_dial_config") or {}
        if isinstance(ai_dial, dict):
            raw_extra_headers = ai_dial.get("extra_headers") or meta.get("extra_headers")

    if isinstance(raw_extra_headers, str):
        try:
            raw_extra_headers = json.loads(raw_extra_headers)
        except json.JSONDecodeError as error:
            raise ValueError("extra_headers must contain valid JSON") from error
    return _validate_extra_headers(raw_extra_headers) if raw_extra_headers else []


async def get_or_create_mcp(
    spec_json: str,
    request: Request,
    base_url: Optional[str] = None,
    extra_headers: Optional[list[dict[str, str]]] = None,
) -> Optional[CacheEntry]:
    """Get or create MCP instance for an OpenAPI spec (supports JSON and YAML)"""
    # Validate the request's forwarded headers first, so the cache fast path below can never
    # skip the block/allow list checks.
    try:
        if extra_headers is None:
            extra_headers = await _extract_extra_headers(request)
    except Exception as e:
        logger.error(f"Error creating MCP: {e}", exc_info=True)
        return None

    # Fast path: the same spec text and base URL were seen before and the entry is still
    # cached, so skip parsing/hashing (YAML parsing alone costs ~1 s for a large spec).
    memo_key = hashlib.sha256(f"{base_url or ''}\0{spec_json}".encode()).hexdigest()
    remembered_id = _API_ID_MEMO.get(memo_key)
    if remembered_id is not None:
        cached_entry = await _mcp_cache.get(remembered_id)
        if cached_entry is not None:
            return cached_entry

    try:
        openapi_spec = parse_spec_text(spec_json)
    except ValueError as parse_error:
        logger.error(f"Failed to parse OpenAPI spec: {parse_error}")
        return None

    swagger_version = openapi_spec.get("swagger")
    openapi_version = openapi_spec.get("openapi", "")
    if swagger_version:
        if not str(swagger_version).startswith("2."):
            logger.error(f"Unsupported Swagger version: {swagger_version!r}")
            return None
        if not base_url:
            # Swagger host/basePath/schemes are dropped by conversion when host is absent.
            base_url = _base_url_from_spec(openapi_spec)
        try:
            openapi_spec = _convert_swagger2_to_openapi30(openapi_spec)
        except Exception as convert_error:
            logger.error(f"Failed to convert Swagger {swagger_version} spec: {convert_error}")
            return None
        logger.debug("Converted Swagger %s spec to OpenAPI 3.0", swagger_version)
        openapi_version = openapi_spec["openapi"]
    if not isinstance(openapi_version, str):
        openapi_version = ""
    if openapi_version and not openapi_version.startswith("3."):
        logger.error(
            f"Unsupported OpenAPI version: {openapi_version!r}; only OpenAPI 3.x is supported"
        )
        return None

    try:
        if extra_headers is None:
            extra_headers = await _extract_extra_headers(request)

        if not base_url:
            base_url = _base_url_from_spec(openapi_spec)
        base_url = _normalize_base_url(base_url)

        api_id = get_api_id(openapi_spec, base_url)
        _API_ID_MEMO[memo_key] = api_id
        while len(_API_ID_MEMO) > _API_ID_MEMO_MAX:
            _API_ID_MEMO.pop(next(iter(_API_ID_MEMO)))

        if base_url:
            if "servers" not in openapi_spec:
                openapi_spec["servers"] = []
            if openapi_spec["servers"]:
                if isinstance(openapi_spec["servers"][0], dict):
                    openapi_spec["servers"][0]["url"] = base_url
                else:
                    openapi_spec["servers"][0] = {"url": base_url}
            else:
                openapi_spec["servers"].append({"url": base_url})

        async def prepare_request(outbound_request: httpx.Request) -> None:
            for header_name in _MCP_CONTROL_HEADERS:
                outbound_request.headers.pop(header_name, None)
            for header_name, header_value in _REQUEST_HEADERS.get():
                outbound_request.headers[header_name] = header_value
            credential = _REQUEST_CREDENTIAL.get()
            if credential:
                outbound_request.headers[credential[0]] = credential[1]
            logger.debug(
                "Outgoing API call: %s %s header_count=%d",
                outbound_request.method,
                _safe_destination(outbound_request.url),
                len(outbound_request.headers),
            )

        api_name = openapi_spec.get("info", {}).get("title", "Unknown API")
        mcp_names, x_mcp_component_fn = _collect_x_mcp_overrides(openapi_spec)
        from_openapi_kwargs: Dict[str, Any] = {
            "mcp_component_fn": _build_component_fn(openapi_spec, x_mcp_component_fn)
        }
        if mcp_names:
            from_openapi_kwargs["mcp_names"] = mcp_names

        paths = openapi_spec.get("paths", {})

        async def build_entry() -> CacheEntry:
            client_kwargs: dict[str, Any] = {
                "timeout": 30.0,
                "follow_redirects": False,
                "event_hooks": {"request": [prepare_request]},
            }
            if base_url:
                client_kwargs["base_url"] = base_url
            else:
                logger.info("No base URL provided; relying on OpenAPI servers or absolute URLs")
            client = httpx.AsyncClient(**client_kwargs)
            try:
                logger.debug(
                    "Creating MCP: name=%r api_id=%s base_url=%s path_count=%d has_name_overrides=%s",
                    api_name,
                    api_id,
                    base_url,
                    len(paths) if isinstance(paths, dict) else 0,
                    mcp_names is not None,
                )
                mcp_instance = FastMCP.from_openapi(
                    openapi_spec=openapi_spec,
                    client=client,
                    name=api_name,
                    dereference_schemas=_dereference_schemas(),
                    **from_openapi_kwargs,
                )
                tools_list = await mcp_instance.list_tools()
                tools_dict = {tool.name: tool for tool in tools_list}
                logger.debug("FastMCP.from_openapi output: tools=%s", list(tools_dict))
                return CacheEntry(
                    mcp=mcp_instance,
                    name=api_name,
                    client=client,
                    api_id=api_id,
                    spec=openapi_spec,
                    tools=tools_dict,
                )
            except BaseException:
                await client.aclose()
                raise

        entry = await _mcp_cache.get_or_create(api_id, build_entry)
        logger.info(
            "Created or reused MCP for: %s (ID: %s, %d tools)", api_name, api_id, len(entry.tools)
        )
        return entry

    except Exception as e:
        logger.error(f"Error creating MCP: {e}", exc_info=True)
        return None


class OpenAPI2MCPBridge(Middleware):
    """Middleware to intercept and handle all MCP protocol requests."""

    async def on_list_resources(self, context: MiddlewareContext, call_next):
        """Intercept list_resources"""
        logger.debug("Intercepting list_resources")
        try:
            request = get_http_request()
            spec_json, base_url = await _extract_spec_from_request(request)
            if not spec_json:
                return []

            cache_entry = await get_or_create_mcp(spec_json, request, base_url)
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            resources = getattr(cache_entry.mcp, '_resources', [])
            logger.info(f"Returning {len(resources)} resources")
            return resources
        except Exception as e:
            logger.error(f"Error in list_resources: {e}", exc_info=True)
            raise

    # The dict-returning fallback branch below (used when FastMCP has no
    # `_resource_manager`) doesn't match the declared `ResourceResult` return
    # type. Tracked as follow-up FastMCP-compatibility work rather than fixed
    # here to avoid guessing at the real ResourceResult contract.
    async def on_read_resource(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: MiddlewareContext, call_next
    ):
        """Intercept read_resource"""
        logger.debug("Intercepting read_resource")
        try:
            request = get_http_request()
            request_body = await request.json()
            params = request_body.get("params", {})
            resource_uri = params.get("uri")
            logger.info(f"Read resource: {resource_uri}")

            spec_json, base_url = await _extract_spec_from_request(request)
            if not spec_json:
                raise ValueError("No OpenAPI spec provided in request")

            cache_entry = await get_or_create_mcp(spec_json, request, base_url)
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            if hasattr(cache_entry.mcp, '_resource_manager'):
                return await cache_entry.mcp._resource_manager.read_resource(resource_uri)

            return {
                "contents": [
                    {"uri": resource_uri, "mimeType": "application/json", "text": spec_json}
                ]
            }

        except Exception as e:
            logger.error(f"Error in read_resource: {e}", exc_info=True)
            raise

    async def on_list_prompts(self, context: MiddlewareContext, call_next):
        """Intercept list_prompts"""
        logger.debug("Intercepting list_prompts")
        try:
            request = get_http_request()
            spec_json, base_url = await _extract_spec_from_request(request)
            if not spec_json:
                return []

            cache_entry = await get_or_create_mcp(spec_json, request, base_url)
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            prompts = getattr(cache_entry.mcp, '_prompts', [])
            logger.info(f"Returning {len(prompts)} prompts")
            return prompts
        except Exception as e:
            logger.error(f"Error in list_prompts: {e}", exc_info=True)
            raise

    # Same pre-existing dict-fallback vs. `PromptResult` mismatch as
    # `on_read_resource` above.
    async def on_get_prompt(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: MiddlewareContext, call_next
    ):
        """Intercept get_prompt"""
        logger.debug("Intercepting get_prompt")
        try:
            request = get_http_request()
            request_body = await request.json()
            params = request_body.get("params", {})
            prompt_name = params.get("name")
            prompt_arguments = params.get("arguments", {})
            logger.info(f"Get prompt: {prompt_name}")

            spec_json, base_url = await _extract_spec_from_request(request)
            if not spec_json:
                raise ValueError("No OpenAPI spec provided in request")

            cache_entry = await get_or_create_mcp(spec_json, request, base_url)
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            if hasattr(cache_entry.mcp, '_prompt_manager'):
                return await cache_entry.mcp._prompt_manager.get_prompt(
                    prompt_name, prompt_arguments
                )

            api_title = cache_entry.spec.get('info', {}).get('title', 'this API')
            return {
                "description": f"Prompt for {api_title}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"No prompts available for {api_title}",
                        },
                    }
                ],
            }
        except Exception as e:
            logger.error(f"Error in get_prompt: {e}", exc_info=True)
            raise

    async def on_complete(self, context: MiddlewareContext, call_next):
        """Intercept completion requests"""
        logger.debug("Intercepting complete")
        try:
            request = get_http_request()
            request_body = await request.json()
            params = request_body.get("params", {}) if isinstance(request_body, dict) else {}
            ref = params.get("ref", {}) if isinstance(params, dict) else {}
            argument = params.get("argument", {}) if isinstance(params, dict) else {}
            if not isinstance(ref, dict) or not isinstance(argument, dict):
                raise ValueError("Completion ref and argument must be objects")
            logger.info("Completion request received")

            spec_json, base_url = await _extract_spec_from_request(request)
            if not spec_json:
                return {"completion": {"values": []}}

            cache_entry = await get_or_create_mcp(spec_json, request, base_url)
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            if ref.get("type") == "ref/tool" or argument.get("name") == "tool":
                prefix = argument.get("value", "")
                completions = [
                    {"value": name, "description": f"Call {name}", "label": name}
                    for name in cache_entry.tools.keys()
                    if name.lower().startswith(prefix.lower())
                ]
                return {"completion": {"values": completions[:20]}}

            if hasattr(cache_entry.mcp, '_completion_handler'):
                return await cache_entry.mcp._completion_handler(ref, argument)

            return {"completion": {"values": []}}
        except Exception as e:
            logger.error(f"Error in complete: {e}", exc_info=True)
            raise

    async def on_list_tools(self, context: MiddlewareContext, call_next) -> Sequence[Tool]:
        """Intercept list_tools to return tools from the OpenAPI-based MCP"""
        logger.debug("Intercepting list_tools")

        try:
            request = get_http_request()
            spec_json, base_url = await _extract_spec_from_request(request)
            if _is_empty_spec_json(spec_json):
                logger.warning(
                    "Missing or empty OpenAPI spec in list_tools request, returning default tools"
                )
                return await call_next(context)
            assert spec_json is not None

            cache_entry = await get_or_create_mcp(spec_json, request, base_url)
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            tool_list = list(cache_entry.tools.values())
            logger.info(f"Returning {len(tool_list)} tools from {cache_entry.name}")
            return tool_list

        except Exception as e:
            logger.error(f"Error in list_tools: {e}", exc_info=True)
            raise

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Intercept call_tool and route to the OpenAPI-based MCP"""
        logger.debug("Intercepting call_tool")

        try:
            request = get_http_request()
            request_body = await request.json()
            if not isinstance(request_body, dict):
                raise ValueError("Tool call request body must be an object")
            params = request_body.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("Tool call params must be an object")
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                raise ValueError("Tool call requires a string name and object arguments")

            logger.info("Tool call: %s", tool_name)
            logger.debug(
                "Tool call metadata: tool=%s argument_count=%d header_count=%d",
                tool_name,
                len(arguments) if isinstance(arguments, dict) else 0,
                len(request.headers),
            )

            spec_json, base_url = await _extract_spec_from_request(request)
            if _is_empty_spec_json(spec_json):
                return await call_next(context)
            assert spec_json is not None

            extra_headers = await _extract_extra_headers(request)
            cache_entry = await get_or_create_mcp(
                spec_json, request, base_url, extra_headers=extra_headers
            )
            if not cache_entry:
                raise ValueError(
                    "Failed to create MCP from OpenAPI spec — check server logs for details"
                )

            if tool_name not in cache_entry.tools:
                available = list(cache_entry.tools.keys())
                return ToolResult(
                    content=f"Tool '{tool_name}' not found. Available: {', '.join(available[:5])}",
                    is_error=True,
                    structured_content={
                        "success": False,
                        "error": "Tool not found",
                        "available_tools": available[:10],
                    },
                )

            logger.info(f"Executing '{tool_name}' from {cache_entry.name}")

            # ToolResult(meta=..., is_error=True) skips structured_content entirely, so
            # to_mcp_result() returns a CallToolResult directly and the MCP SDK never
            # runs jsonschema validation against the real tool's outputSchema.
            try:
                dial_creds = await _resolve_dial_credentials(request)
            except DialCredentialsError as e:
                meta: dict[str, Any] = {
                    "dial.epam.com/error": {
                        "status_code": e.status_code,
                        "external_service": e.external_service,
                    }
                }
                if e.status_code == 401 and e.scope:
                    meta["dial.epam.com/auth-challenge"] = [
                        {"method": "external-service/signin", "scope": e.scope}
                    ]
                return ToolResult(content=str(e), is_error=True, meta=meta)

            credential_token = None
            if dial_creds:
                header_name = dial_creds.get("header_name")
                header_value = dial_creds.get("header_value")
                if not isinstance(header_name, str) or not isinstance(header_value, str):
                    return ToolResult(
                        content="DIAL core returned an invalid credential",
                        is_error=True,
                        meta={
                            "dial.epam.com/error": {
                                "status_code": 500,
                                "external_service": "unknown",
                            }
                        },
                    )
                credential_token = _REQUEST_CREDENTIAL.set((header_name, header_value))

            headers_token = _REQUEST_HEADERS.set(
                tuple((entry["name"], entry["value"]) for entry in extra_headers)
            )
            try:
                result = await cache_entry.mcp.call_tool(tool_name, arguments)
            finally:
                _REQUEST_HEADERS.reset(headers_token)
                if credential_token is not None:
                    _REQUEST_CREDENTIAL.reset(credential_token)

            if isinstance(result, ToolResult):
                return result

            return ToolResult(
                content=json.dumps(result) if isinstance(result, (dict, list)) else str(result),
                structured_content=result if isinstance(result, dict) else {"result": result},
            )

        except Exception:
            logger.exception("Tool call failed")
            raise


_ready = False


@lifespan
async def _cache_lifespan(server):
    """Run cache maintenance and release cached HTTP clients with the server."""
    global _ready
    setup_telemetry()
    _mcp_cache.start_cleanup_task()
    _ready = True
    try:
        yield {}
    finally:
        _ready = False
        await _mcp_cache.stop_cleanup_task()
        await _mcp_cache.clear()


mcp = FastMCP(
    name="OpenAPI to MCP",
    lifespan=_cache_lifespan,
    dereference_schemas=_dereference_schemas(),
)
mcp.add_middleware(OpenAPI2MCPBridge())


@mcp.custom_route("/v1/configuration-support/application-schema", methods=["GET"])
async def get_application_schema(request: Request) -> JSONResponse:
    """Serve the DIAL application-type configuration schema."""
    return JSONResponse(APPLICATION_SCHEMA)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness probe: the process is up. No external dependencies to check."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/ready", methods=["GET"])
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: the cache-maintenance lifespan has completed startup."""
    if _ready:
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not ready"}, status_code=503)


async def openapi_extend(
    spec: Dict[str, Any] | str,
    overwrite: bool = False,
    as_json: bool = True,
) -> Dict[str, Any]:
    """Generate an inline OpenAPI spec with x-mcp fields using FastMCP defaults.

    Not registered as a public MCP tool; used internally/for generating example
    fixtures.
    """
    try:
        spec_dict = json.loads(_normalize_spec_to_json(spec))
    except ValueError as error:
        return {"success": False, "error": str(error)}

    extended_spec = await _build_extended_openapi_spec(spec_dict, overwrite=overwrite)

    if as_json:
        return {
            "success": True,
            "openapi_json": json.dumps(extended_spec, indent=2),
        }
    return {
        "success": True,
        "openapi": extended_spec,
    }


def _format_exception(e: BaseException, _depth: int = 0) -> Dict[str, Any]:
    """Recursively format an exception chain into a plain dict for diagnostics."""
    if _depth > 5:
        return {"type": "...", "message": "truncated"}
    result: Dict[str, Any] = {"type": type(e).__name__, "message": str(e)}
    errors_method = getattr(e, "errors", None)
    if callable(errors_method):
        try:
            result["pydantic_errors"] = errors_method()
        except Exception:
            pass
    if e.__cause__ is not None:
        result["cause"] = _format_exception(e.__cause__, _depth + 1)
    elif e.__context__ is not None and not e.__suppress_context__:
        result["context"] = _format_exception(e.__context__, _depth + 1)
    return result


@mcp.tool()
async def openapi_verify(spec: Dict[str, Any] | str) -> Dict[str, Any]:
    """Verify an OpenAPI spec: checks JSON parsability, version support, and FastMCP compatibility."""
    steps = {}

    # Step 1: parse
    if isinstance(spec, dict):
        spec_dict = spec
        steps["parse"] = {"ok": True, "format": "dict"}
    else:
        try:
            spec_dict = json.loads(spec)
            steps["parse"] = {"ok": True, "format": "json"}
        except json.JSONDecodeError as json_err:
            try:
                import yaml

                spec_dict = yaml.safe_load(spec)
                if not isinstance(spec_dict, dict):
                    raise ValueError(f"YAML parsed to {type(spec_dict).__name__}, expected dict")
                steps["parse"] = {"ok": True, "format": "yaml"}
            except Exception as yaml_err:
                return {
                    "success": False,
                    "steps": {
                        "parse": {
                            "ok": False,
                            "json_error": str(json_err),
                            "yaml_error": str(yaml_err),
                        }
                    },
                }

    if not isinstance(spec_dict, dict):
        return {
            "success": False,
            "steps": {"parse": {"ok": False, "error": "Spec must be an object"}},
        }

    # unwrap openapi_convert / openapi_extend response envelope if passed directly
    if (
        "success" in spec_dict
        and "openapi" in spec_dict
        and isinstance(spec_dict.get("openapi"), dict)
    ):
        spec_dict = spec_dict["openapi"]
        steps["parse"]["unwrapped"] = True

    # Step 2: version
    swagger_version = spec_dict.get("swagger")
    openapi_version = spec_dict.get("openapi", "")
    if not isinstance(openapi_version, str):
        openapi_version = ""
    converted_from: Optional[str] = None
    if swagger_version:
        if not str(swagger_version).startswith("2."):
            steps["version"] = {
                "ok": False,
                "error": f"Swagger version {swagger_version!r} not supported — only 2.x is supported",
            }
            return {"success": False, "steps": steps}
        try:
            spec_dict = _convert_swagger2_to_openapi30(spec_dict)
        except Exception as e:
            steps["version"] = {
                "ok": False,
                "error": f"Swagger {swagger_version} conversion failed ({type(e).__name__}): {e}",
            }
            return {"success": False, "steps": steps}
        converted_from = f"swagger {swagger_version}"
        openapi_version = spec_dict["openapi"]
    if openapi_version and not openapi_version.startswith("3."):
        steps["version"] = {
            "ok": False,
            "error": f"OpenAPI version {openapi_version!r} not supported — only 3.x is supported",
        }
        return {"success": False, "steps": steps}
    steps["version"] = {"ok": True, "version": openapi_version or "unknown"}
    if converted_from:
        steps["version"]["converted_from"] = converted_from

    # Step 3: structure
    issues = []
    if not isinstance(spec_dict.get("info"), dict):
        issues.append("missing or invalid 'info' object")
    else:
        if not spec_dict["info"].get("title"):
            issues.append("'info.title' is missing or empty")
        if not spec_dict["info"].get("version"):
            issues.append("'info.version' is missing or empty")
    paths = spec_dict.get("paths")
    if not isinstance(paths, dict):
        issues.append("'paths' is missing or not an object")
    elif not paths:
        issues.append("'paths' is empty — no operations defined")
    else:
        for path, item in paths.items():
            if not isinstance(item, dict):
                issues.append(f"path '{path}' value is not an object")
    if issues:
        steps["structure"] = {"ok": False, "issues": issues}
        return {"success": False, "steps": steps}
    steps["structure"] = {"ok": True, "path_count": len(paths or {})}

    # Step 4: FastMCP compatibility
    client: httpx.AsyncClient | None = None
    try:
        client = httpx.AsyncClient()
        mcp_instance = FastMCP.from_openapi(
            openapi_spec=spec_dict,
            client=client,
            name="_verify",
            dereference_schemas=_dereference_schemas(),
        )
        tools = await mcp_instance.list_tools()
        steps["fastmcp"] = {"ok": True, "tools": len(tools)}
    except Exception as e:
        steps["fastmcp"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "details": _format_exception(e),
        }
        # Per-path diagnosis: identify which paths cause the failure
        all_paths = spec_dict.get("paths", {})
        if all_paths and len(all_paths) <= 50:
            path_results: Dict[str, Any] = {}
            for path, item in all_paths.items():
                mini_client = httpx.AsyncClient()
                try:
                    mini_spec = {**spec_dict, "paths": {path: item}}
                    FastMCP.from_openapi(
                        openapi_spec=mini_spec, client=mini_client, name="_verify_path"
                    )
                    path_results[path] = "ok"
                except Exception as path_err:
                    path_results[path] = f"{type(path_err).__name__}: {path_err}"
                finally:
                    try:
                        await mini_client.aclose()
                    except Exception:
                        pass
            steps["fastmcp"]["path_diagnosis"] = path_results
        return {"success": False, "steps": steps}
    finally:
        if client is not None:
            await client.aclose()

    return {"success": True, "steps": steps}


@mcp.tool()
async def openapi_convert(spec: Dict[str, Any] | str) -> Dict[str, Any]:
    """Convert any Swagger/OpenAPI spec to FastMCP-compatible OpenAPI JSON. Swagger 2.x is converted to OpenAPI 3.0; 3.x is returned as-is."""
    if isinstance(spec, str):
        try:
            spec_dict = json.loads(spec)
        except json.JSONDecodeError:
            try:
                import yaml

                spec_dict = yaml.safe_load(spec)
                if not isinstance(spec_dict, dict):
                    raise ValueError(f"YAML parsed to {type(spec_dict).__name__}, expected dict")
            except Exception as e:
                return {"success": False, "error": f"Failed to parse spec: {e}"}
    elif isinstance(spec, dict):
        spec_dict = spec
    else:
        return {"success": False, "error": "spec must be a JSON string or dict"}

    swagger_version = spec_dict.get("swagger")
    openapi_version = spec_dict.get("openapi", "")

    if swagger_version:
        try:
            converted = _convert_swagger2_to_openapi30(spec_dict)
            return {"success": True, "openapi": converted, "openapi_json": json.dumps(converted)}
        except Exception as e:
            return {"success": False, "error": f"Conversion failed ({type(e).__name__}): {e}"}

    if openapi_version.startswith("3."):
        return {"success": True, "openapi": spec_dict, "openapi_json": json.dumps(spec_dict)}

    return {
        "success": False,
        "error": "Unrecognized spec: no 'swagger' or 'openapi' version field found",
    }


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "8080")),
    )
