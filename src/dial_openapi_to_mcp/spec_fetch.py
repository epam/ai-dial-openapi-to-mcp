"""Fetching and parsing of OpenAPI documents delivered by URL (JSON or YAML)."""

import asyncio
import datetime
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

logger = logging.getLogger(__name__)

_MAX_NODES = 2_000_000
_NESTING_ERROR = "Spec is nested too deeply or contains a circular YAML alias"


class SpecFetchError(ValueError):
    """Raised when the OpenAPI document cannot be fetched or parsed."""


def _fetch_timeout() -> float:
    return float(os.getenv("OPENAPI_FETCH_TIMEOUT", "10"))


def _fetch_max_bytes() -> int:
    return int(os.getenv("OPENAPI_FETCH_MAX_BYTES", str(10 * 1024 * 1024)))


def _cache_ttl() -> float:
    return float(os.getenv("OPENAPI_URL_CACHE_TTL", "3600"))


def _cache_stale_ttl() -> float:
    return float(os.getenv("OPENAPI_URL_CACHE_STALE_TTL", "86400"))


def _cache_max_entries() -> int:
    return int(os.getenv("OPENAPI_URL_CACHE_MAX_ENTRIES", "32"))


def _to_jsonable(obj: Any, counter: list[int]) -> Any:
    """Make a YAML-loaded structure JSON-native: str keys, ISO dates, bounded size.

    The node counter stops alias expansion ("billion laughs") from exploding.
    """
    counter[0] += 1
    if counter[0] > _MAX_NODES:
        raise ValueError("Spec is too large after expanding YAML aliases")
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v, counter) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v, counter) for v in obj]
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def parse_spec_text(text: str) -> Dict[str, Any]:
    """Parse an OpenAPI/Swagger document given as JSON or YAML text into a dict.

    The format is detected from the content, never from a URL or content type.
    """
    stripped = text.lstrip("﻿").strip()
    if not stripped:
        raise ValueError("Spec is empty")
    parsed: Any
    from_json = False
    try:
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                from_json = True
            except json.JSONDecodeError:
                parsed = _load_yaml(stripped)
        else:
            parsed = _load_yaml(stripped)
        if not isinstance(parsed, dict):
            raise ValueError(f"Spec must be an object, got {type(parsed).__name__}")
        # JSON is already JSON-native and cannot expand through aliases; only YAML needs
        # key/date normalization and the alias-bomb node cap.
        return parsed if from_json else _to_jsonable(parsed, [0])
    except RecursionError as error:
        raise ValueError(_NESTING_ERROR) from error


def _load_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"Cannot parse spec as JSON or YAML: {error}") from error


def spec_text_to_json(text: str) -> str:
    parsed = parse_spec_text(text)
    try:
        return json.dumps(parsed)
    except RecursionError as error:
        raise ValueError(_NESTING_ERROR) from error


def validate_spec_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SpecFetchError("openapi_url must be an absolute http(s) URL")
    return url.strip()


def resolve_base_url(spec: Dict[str, Any], spec_url: str, declared: Optional[str]) -> Optional[str]:
    """Base URL for a spec fetched from spec_url: declared server, else the schema origin.

    A relative server URL (or a Swagger 2 basePath without host) resolves against the
    schema URL.
    """
    if declared and urlsplit(declared).scheme:
        return declared
    if declared:
        return urljoin(spec_url, declared)
    if "swagger" in spec:
        base_path = spec.get("basePath") or "/"
        return urljoin(spec_url, base_path if base_path.startswith("/") else f"/{base_path}")
    parts = urlsplit(spec_url)
    return f"{parts.scheme}://{parts.netloc}"


async def _download(url: str, headers: Sequence[Tuple[str, str]]) -> str:
    """Download the document under a total deadline (httpx timeouts only bound each read)."""
    deadline = _fetch_timeout()
    try:
        async with asyncio.timeout(deadline):
            return await _download_body(url, headers)
    except TimeoutError as error:
        raise SpecFetchError(f"Fetching OpenAPI spec timed out after {deadline:g}s") from error


async def _download_body(url: str, headers: Sequence[Tuple[str, str]]) -> str:
    max_bytes = _fetch_max_bytes()
    try:
        async with httpx.AsyncClient(timeout=_fetch_timeout(), follow_redirects=False) as client:
            async with client.stream(
                "GET",
                url,
                headers={
                    "Accept": "application/json, application/yaml, text/yaml, */*",
                    **dict(headers),
                },
            ) as response:
                if response.status_code != 200:
                    raise SpecFetchError(
                        f"Fetching OpenAPI spec failed: HTTP {response.status_code}"
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise SpecFetchError(f"OpenAPI spec exceeds the {max_bytes} byte limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
    except httpx.HTTPError as error:
        raise SpecFetchError(f"Fetching OpenAPI spec failed: {type(error).__name__}") from error
    return b"".join(chunks).decode(encoding, errors="replace")


# Cache value: (retry_at, fetched_at, spec_json). The entry is served as fresh until
# retry_at; fetched_at bounds how long it may be served stale when a refetch fails.
_cache: Dict[Tuple[str, str], Tuple[float, float, str]] = {}
_STALE_RETRY_SECONDS = 30.0
_inflight: Dict[Tuple[str, str], "asyncio.Task[str]"] = {}


def _cache_key(url: str, headers: Sequence[Tuple[str, str]]) -> Tuple[str, str]:
    digest = hashlib.sha256(
        json.dumps(sorted((k.lower(), v) for k, v in headers)).encode()
    ).hexdigest()
    return url, digest


def _safe_url(url: str) -> str:
    """URL for logs: scheme, host, port and path only (no userinfo or query string)."""
    parsed = httpx.URL(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.host}{port}{parsed.path}"


def _store(key: Tuple[str, str], retry_at: float, fetched_at: float, spec_json: str) -> None:
    """Insert or refresh an entry as the newest one, then enforce expiry and the size cap."""
    _cache.pop(key, None)
    _cache[key] = (retry_at, fetched_at, spec_json)
    _evict_expired(_cache_ttl())
    max_entries = _cache_max_entries()
    while len(_cache) > max_entries:
        _cache.pop(next(iter(_cache)))


async def _refresh(
    key: Tuple[str, str],
    url: str,
    headers: Sequence[Tuple[str, str]],
    cached: Optional[Tuple[float, float, str]],
) -> str:
    """Download and parse the document, falling back to the stale copy on failure."""
    ttl = _cache_ttl()
    try:
        text = await _download(url, headers)
        try:
            spec_json = spec_text_to_json(text)
        except ValueError as error:
            raise SpecFetchError(
                f"OpenAPI spec at URL is not valid JSON or YAML: {error}"
            ) from error
    except SpecFetchError as error:
        now = time.monotonic()
        stale_limit = ttl + _cache_stale_ttl()
        if cached and now - cached[1] < stale_limit:
            logger.warning(
                "Refetching OpenAPI spec %s failed (%s); serving the copy fetched %.0fs ago",
                _safe_url(url),
                error,
                now - cached[1],
            )
            retry_at = min(now + _STALE_RETRY_SECONDS, cached[1] + stale_limit)
            _store(key, retry_at, cached[1], cached[2])
            return cached[2]
        raise
    if _cache_max_entries() > 0 and ttl > 0:
        fetched_at = time.monotonic()
        _store(key, fetched_at + ttl, fetched_at, spec_json)
    return spec_json


async def fetch_openapi_spec(url: str, headers: Sequence[Tuple[str, str]] = ()) -> str:
    """Fetch the document at url and return it as a JSON string.

    Results are cached per (url, auth headers) for OPENAPI_URL_CACHE_TTL seconds, so one
    caller's credentials never serve another's. Concurrent requests for the same key share
    one download, which runs as its own task so a cancelled request cannot fail the others.
    If a refetch fails, the previous copy is served for up to OPENAPI_URL_CACHE_STALE_TTL
    more seconds, retrying at most every 30 s.
    """
    url = validate_spec_url(url)
    key = _cache_key(url, headers)
    cached = _cache.get(key)
    if cached and time.monotonic() < cached[0]:
        return cached[2]

    task = _inflight.get(key)
    if task is None:
        task = asyncio.get_running_loop().create_task(_refresh(key, url, headers, cached))
        _inflight[key] = task

        def _done(finished: "asyncio.Task[str]") -> None:
            if _inflight.get(key) is finished:
                del _inflight[key]
            if not finished.cancelled():
                finished.exception()  # mark retrieved even if every waiter was cancelled

        task.add_done_callback(_done)
    return await asyncio.shield(task)


def _evict_expired(ttl: float) -> None:
    """Drop entries too old to serve even as a stale fallback."""
    now = time.monotonic()
    limit = ttl + _cache_stale_ttl()
    for key in [k for k, (_, fetched_at, _) in _cache.items() if now - fetched_at >= limit]:
        _cache.pop(key, None)


def clear_spec_cache() -> None:
    _cache.clear()
