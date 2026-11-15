"""Schema delivered by URL: JSON/YAML, Swagger 2.0, auth, caching and error handling."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, MagicMock

import pytest

from dial_openapi_to_mcp.server import (
    _extract_spec_from_request,
    _mcp_cache,
    get_or_create_mcp,
)
from dial_openapi_to_mcp.spec_fetch import (
    SpecFetchError,
)
from dial_openapi_to_mcp.spec_fetch import _cache as _spec_cache
from dial_openapi_to_mcp.spec_fetch import (
    clear_spec_cache,
    fetch_openapi_spec,
    parse_spec_text,
)
from helpers import MINIMAL_OPENAPI_30, MINIMAL_SWAGGER_20

OPENAPI_30_YAML = """\
openapi: 3.0.3
info:
  title: Yaml API
  version: "1.0"
  x-released: 2024-01-31
paths:
  /items:
    get:
      operationId: list_items
      responses:
        200:
          description: ok
"""

OPENAPI_31_YAML = """\
openapi: 3.1.0
info:
  title: Yaml 31 API
  version: "1.0"
paths:
  /items:
    get:
      operationId: list_items
      responses:
        "200":
          description: ok
"""

SWAGGER_20_YAML = """\
swagger: "2.0"
info:
  title: Old Yaml API
  version: "1.0"
  x-released: 2024-01-31
host: legacy.example.com
basePath: /v1
schemes: [https]
paths:
  /items:
    get:
      operationId: list_items
      responses:
        200:
          description: ok
"""


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    requests: list = []

    def do_GET(self):  # noqa: N802
        type(self).requests.append((self.path, dict(self.headers)))
        status, content_type, body, extra = self.routes.get(
            self.path, (404, "text/plain", b"missing", {})
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def schema_server():
    handler = type("Handler", (_Handler,), {"routes": {}, "requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield handler
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
async def clean_caches():
    clear_spec_cache()
    await _mcp_cache.clear()
    yield
    clear_spec_cache()
    await _mcp_cache.clear()


def _request(body=None, headers=None):
    mock = MagicMock()
    mock.headers = {k.lower(): v for k, v in (headers or {}).items()}
    mock.json = AsyncMock(return_value=body if body is not None else {"params": {}})
    return mock


async def _tools_from_url(url, **kwargs):
    request = _request(headers={"X-OPENAPI-URL": url}, **kwargs)
    spec_json, base_url = await _extract_spec_from_request(request)
    entry = await get_or_create_mcp(spec_json, request, base_url)
    return entry, base_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,content_type,body,title",
    [
        ("/openapi.json", "application/json", json.dumps(MINIMAL_OPENAPI_30), "Test API"),
        ("/openapi.yaml", "application/yaml", OPENAPI_30_YAML, "Yaml API"),
        ("/openapi.yml", "text/yaml", OPENAPI_31_YAML, "Yaml 31 API"),
        ("/spec", "text/plain", OPENAPI_30_YAML, "Yaml API"),
        ("/swagger.json", "application/json", json.dumps(MINIMAL_SWAGGER_20), "Test API"),
        ("/swagger.yaml", "application/x-yaml", SWAGGER_20_YAML, "Old Yaml API"),
    ],
)
async def test_formats_served_over_http(schema_server, path, content_type, body, title):
    schema_server.routes[path] = (200, content_type, body, {})

    entry, _ = await _tools_from_url(schema_server.base + path)

    assert entry is not None
    assert entry.name == title
    assert any("items" in name for name in entry.tools)


@pytest.mark.asyncio
async def test_yaml_with_bom_and_int_codes_and_dates(schema_server):
    schema_server.routes["/o.yaml"] = (
        200,
        "application/octet-stream",
        b"\xef\xbb\xbf" + OPENAPI_30_YAML.encode(),
        {},
    )
    request = _request(headers={"x-openapi-url": schema_server.base + "/o.yaml"})

    spec_json, _ = await _extract_spec_from_request(request)

    spec = json.loads(spec_json)
    assert spec["info"]["x-released"] == "2024-01-31"
    assert "200" in spec["paths"]["/items"]["get"]["responses"]


@pytest.mark.asyncio
async def test_swagger_base_url_comes_from_host(schema_server):
    schema_server.routes["/s.yaml"] = (200, "text/yaml", SWAGGER_20_YAML, {})

    _, base_url = await _tools_from_url(schema_server.base + "/s.yaml")

    assert base_url == "https://legacy.example.com/v1"


@pytest.mark.asyncio
async def test_base_url_defaults_to_schema_origin(schema_server):
    schema_server.routes["/openapi.json"] = (
        200,
        "application/json",
        json.dumps(MINIMAL_OPENAPI_30),
        {},
    )

    _, base_url = await _tools_from_url(schema_server.base + "/openapi.json")

    assert base_url == schema_server.base


@pytest.mark.asyncio
async def test_relative_server_resolved_against_schema_url(schema_server):
    spec = {**MINIMAL_OPENAPI_30, "servers": [{"url": "/api/v2"}]}
    schema_server.routes["/docs/openapi.json"] = (200, "application/json", json.dumps(spec), {})

    _, base_url = await _tools_from_url(schema_server.base + "/docs/openapi.json")

    assert base_url == schema_server.base + "/api/v2"


@pytest.mark.asyncio
async def test_explicit_base_url_wins(schema_server):
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    request = _request(
        headers={"x-openapi-url": schema_server.base + "/o.json", "x-base-url": "https://x.test"}
    )

    _, base_url = await _extract_spec_from_request(request)

    assert base_url == "https://x.test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta",
    [
        {"ai_dial_config": {"openapi_url": "{url}"}},
        {"openapi_url": "{url}"},
    ],
)
async def test_url_from_meta(schema_server, meta):
    schema_server.routes["/o.yaml"] = (200, "text/yaml", OPENAPI_30_YAML, {})
    url = schema_server.base + "/o.yaml"
    body = {"params": {"_meta": json.loads(json.dumps(meta).replace("{url}", url))}}

    spec_json, _ = await _extract_spec_from_request(_request(body))

    assert json.loads(spec_json)["info"]["title"] == "Yaml API"


@pytest.mark.asyncio
async def test_inline_spec_beats_url(schema_server):
    schema_server.routes["/o.yaml"] = (200, "text/yaml", OPENAPI_30_YAML, {})
    request = _request(
        {"params": {"_meta": {"openapi": MINIMAL_OPENAPI_30}}},
        headers={"x-openapi-url": schema_server.base + "/o.yaml"},
    )

    spec_json, _ = await _extract_spec_from_request(request)

    assert json.loads(spec_json)["info"]["title"] == "Test API"
    assert schema_server.requests == []


@pytest.mark.asyncio
async def test_no_spec_and_no_url_returns_none():
    spec_json, base_url = await _extract_spec_from_request(_request())

    assert spec_json is None
    assert base_url is None


@pytest.mark.asyncio
async def test_extra_headers_forwarded_to_schema_fetch(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_FETCH_SEND_AUTH", "true")
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    body = {"params": {"_meta": {"extra_headers": [{"name": "X-Token", "value": "abc"}]}}}
    request = _request(body, headers={"x-openapi-url": schema_server.base + "/o.json"})

    await _extract_spec_from_request(request)

    assert schema_server.requests[0][1]["X-Token"] == "abc"
    assert "X-Openapi-Url" not in schema_server.requests[0][1]


@pytest.mark.asyncio
async def test_schema_fetch_is_anonymous_by_default(schema_server, monkeypatch):
    monkeypatch.delenv("OPENAPI_FETCH_SEND_AUTH", raising=False)
    resolve = AsyncMock(return_value={"header_name": "Authorization", "header_value": "Bearer s"})
    monkeypatch.setattr("dial_openapi_to_mcp.server._resolve_dial_credentials", resolve)
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    body = {"params": {"_meta": {"extra_headers": [{"name": "X-Token", "value": "abc"}]}}}
    request = _request(body, headers={"x-openapi-url": schema_server.base + "/o.json"})

    await _extract_spec_from_request(request)

    sent = schema_server.requests[0][1]
    assert "X-Token" not in sent and "Authorization" not in sent
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_dial_credential_sent_when_enabled(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_FETCH_SEND_AUTH", "true")
    resolve = AsyncMock(return_value={"header_name": "Authorization", "header_value": "Bearer s"})
    monkeypatch.setattr("dial_openapi_to_mcp.server._resolve_dial_credentials", resolve)
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    request = _request(headers={"x-openapi-url": schema_server.base + "/o.json"})

    await _extract_spec_from_request(request)

    assert schema_server.requests[0][1]["Authorization"] == "Bearer s"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("false", False),
        ("0", False),
        ("true", True),
        ("1", True),
        ("YES", True),
    ],
)
def test_send_auth_flag_parsing(monkeypatch, value, expected):
    from dial_openapi_to_mcp.server import _fetch_sends_auth

    if value is None:
        monkeypatch.delenv("OPENAPI_FETCH_SEND_AUTH", raising=False)
    else:
        monkeypatch.setenv("OPENAPI_FETCH_SEND_AUTH", value)
    assert _fetch_sends_auth() is expected


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_fetch(schema_server):
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    url = schema_server.base + "/o.json"

    await _extract_spec_from_request(_request(headers={"x-openapi-url": url}))
    await _extract_spec_from_request(_request(headers={"x-openapi-url": url}))

    assert len(schema_server.requests) == 1


@pytest.mark.asyncio
async def test_cache_is_isolated_per_credential(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_FETCH_SEND_AUTH", "true")
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    url = schema_server.base + "/o.json"

    for token in ("one", "two"):
        body = {"params": {"_meta": {"extra_headers": [{"name": "X-Token", "value": token}]}}}
        await _extract_spec_from_request(_request(body, headers={"x-openapi-url": url}))

    assert [r[1]["X-Token"] for r in schema_server.requests] == ["one", "two"]


@pytest.mark.asyncio
async def test_cache_expires(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_TTL", "0")
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    url = schema_server.base + "/o.json"

    await _extract_spec_from_request(_request(headers={"x-openapi-url": url}))
    await _extract_spec_from_request(_request(headers={"x-openapi-url": url}))

    assert len(schema_server.requests) == 2


@pytest.mark.asyncio
async def test_changed_schema_rebuilds_tools(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_TTL", "0")
    url = schema_server.base + "/o.json"
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    first, _ = await _tools_from_url(url)

    changed = json.loads(json.dumps(MINIMAL_OPENAPI_30))
    changed["paths"]["/other"] = {
        "get": {"operationId": "other_op", "responses": {"200": {"description": "ok"}}}
    }
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(changed), {})
    second, _ = await _tools_from_url(url)

    assert first is not second
    assert "other_op" in second.tools and "other_op" not in first.tools


@pytest.mark.asyncio
async def test_non_200_fails(schema_server):
    with pytest.raises(ValueError, match="HTTP 404"):
        await _extract_spec_from_request(
            _request(headers={"x-openapi-url": schema_server.base + "/nope"})
        )


@pytest.mark.asyncio
async def test_redirect_is_not_followed(schema_server):
    schema_server.routes["/r"] = (302, "text/plain", "", {"Location": "/o.json"})
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})

    with pytest.raises(ValueError, match="HTTP 302"):
        await _extract_spec_from_request(
            _request(headers={"x-openapi-url": schema_server.base + "/r"})
        )


@pytest.mark.asyncio
async def test_oversize_fails(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_FETCH_MAX_BYTES", "50")
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})

    with pytest.raises(ValueError, match="limit"):
        await _extract_spec_from_request(
            _request(headers={"x-openapi-url": schema_server.base + "/o.json"})
        )


@pytest.mark.asyncio
async def test_invalid_document_fails(schema_server):
    schema_server.routes["/bad"] = (200, "text/plain", "- just\n- a list\n", {})

    with pytest.raises(ValueError, match="not valid JSON or YAML"):
        await _extract_spec_from_request(
            _request(headers={"x-openapi-url": schema_server.base + "/bad"})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "/relative/openapi.json"])
async def test_non_http_scheme_rejected(url):
    with pytest.raises(ValueError, match="http"):
        await _extract_spec_from_request(_request(headers={"x-openapi-url": url}))


@pytest.mark.asyncio
async def test_unreachable_host_fails():
    with pytest.raises(ValueError, match="Fetching OpenAPI spec failed"):
        await _extract_spec_from_request(
            _request(headers={"x-openapi-url": "http://127.0.0.1:1/openapi.json"})
        )


class _SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            for _ in range(40):
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.25)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.mark.asyncio
async def test_slow_drip_hits_total_deadline(monkeypatch):
    monkeypatch.setenv("OPENAPI_FETCH_TIMEOUT", "1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        with pytest.raises(SpecFetchError, match="timed out"):
            await fetch_openapi_spec(f"http://127.0.0.1:{server.server_address[1]}/o.json")
        assert time.monotonic() - started < 3
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_cache_is_capped_oldest_first(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_MAX_ENTRIES", "2")
    for name in ("a", "b", "c"):
        schema_server.routes[f"/{name}.json"] = (
            200,
            "application/json",
            json.dumps(MINIMAL_OPENAPI_30),
            {},
        )
        await fetch_openapi_spec(f"{schema_server.base}/{name}.json")
    assert [key[0].rsplit("/", 1)[1] for key in _spec_cache] == ["b.json", "c.json"]


@pytest.mark.asyncio
async def test_cache_disabled_with_zero_entries(schema_server, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_MAX_ENTRIES", "0")
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})
    for _ in range(2):
        await fetch_openapi_spec(schema_server.base + "/o.json")
    assert len(schema_server.requests) == 2 and not _spec_cache


@pytest.mark.parametrize("kind", ["yaml_alias_cycle", "deep_json_array", "deep_json_object"])
def test_pathological_nesting_is_a_value_error(kind):
    text = {
        "yaml_alias_cycle": "a: &a [*a]",
        "deep_json_array": "[" * 50000,
        "deep_json_object": '{"a":' * 50000,
    }[kind]
    with pytest.raises(ValueError, match="nested too deeply"):
        parse_spec_text(text)


@pytest.mark.asyncio
async def test_pathological_document_over_http_is_clean_error(schema_server):
    schema_server.routes["/o.yaml"] = (200, "text/yaml", "a: &a [*a]", {})
    with pytest.raises(SpecFetchError, match="not valid JSON or YAML"):
        await fetch_openapi_spec(schema_server.base + "/o.yaml")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("dial_openapi_to_mcp.spec_fetch.time", fake)
    monkeypatch.setenv("OPENAPI_URL_CACHE_TTL", "300")
    monkeypatch.setenv("OPENAPI_URL_CACHE_STALE_TTL", "3600")
    return fake


def _good(schema_server, path="/o.json"):
    schema_server.routes[path] = (200, "application/json", json.dumps(MINIMAL_OPENAPI_30), {})


def _down(schema_server, path="/o.json"):
    schema_server.routes[path] = (500, "text/plain", "boom", {})


@pytest.mark.asyncio
async def test_stale_copy_served_when_refetch_fails(schema_server, clock, caplog):
    _good(schema_server)
    url = schema_server.base + "/o.json"
    fresh = await fetch_openapi_spec(url)
    _down(schema_server)
    clock.now += 301

    with caplog.at_level("WARNING", logger="dial_openapi_to_mcp.spec_fetch"):
        assert await fetch_openapi_spec(url) == fresh

    assert "serving the copy fetched" in caplog.text
    assert "?" not in caplog.text.split("Refetching")[1].split(" failed")[0]


@pytest.mark.asyncio
async def test_error_raised_after_grace_period(schema_server, clock):
    _good(schema_server)
    url = schema_server.base + "/o.json"
    await fetch_openapi_spec(url)
    _down(schema_server)
    clock.now += 300 + 3600 + 1

    with pytest.raises(SpecFetchError):
        await fetch_openapi_spec(url)


@pytest.mark.asyncio
async def test_retry_backoff_while_serving_stale(schema_server, clock):
    _good(schema_server)
    url = schema_server.base + "/o.json"
    await fetch_openapi_spec(url)
    _down(schema_server)
    clock.now += 301
    await fetch_openapi_spec(url)  # first failed refetch
    before = len(schema_server.requests)

    clock.now += 10
    await fetch_openapi_spec(url)
    assert len(schema_server.requests) == before  # still backing off

    clock.now += 25
    await fetch_openapi_spec(url)
    assert len(schema_server.requests) == before + 1  # retried after 30 s


@pytest.mark.asyncio
async def test_recovered_server_replaces_stale_copy(schema_server, clock):
    _good(schema_server)
    url = schema_server.base + "/o.json"
    await fetch_openapi_spec(url)
    _down(schema_server)
    clock.now += 301
    await fetch_openapi_spec(url)

    updated = dict(MINIMAL_OPENAPI_30, info={"title": "Updated", "version": "2"})
    schema_server.routes["/o.json"] = (200, "application/json", json.dumps(updated), {})
    clock.now += 31

    assert json.loads(await fetch_openapi_spec(url))["info"]["title"] == "Updated"


@pytest.mark.asyncio
async def test_stale_fallback_can_be_disabled(schema_server, clock, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_STALE_TTL", "0")
    _good(schema_server)
    url = schema_server.base + "/o.json"
    await fetch_openapi_spec(url)
    _down(schema_server)
    clock.now += 301

    with pytest.raises(SpecFetchError):
        await fetch_openapi_spec(url)


@pytest.mark.asyncio
async def test_stale_copy_not_shared_across_credentials(schema_server, clock):
    _good(schema_server)
    url = schema_server.base + "/o.json"
    await fetch_openapi_spec(url, [("X-Token", "one")])
    _down(schema_server)
    clock.now += 301

    with pytest.raises(SpecFetchError):
        await fetch_openapi_spec(url, [("X-Token", "two")])


@pytest.mark.asyncio
async def test_first_fetch_failure_still_raises(schema_server, clock):
    _down(schema_server)
    with pytest.raises(SpecFetchError):
        await fetch_openapi_spec(schema_server.base + "/o.json")


@pytest.mark.asyncio
async def test_stale_warning_does_not_log_url_credentials(schema_server, clock, caplog):
    _good(schema_server, "/o.json?token=abc")
    url = schema_server.base.replace("http://", "http://user:s3cret@") + "/o.json?token=abc"
    await fetch_openapi_spec(url)
    _down(schema_server, "/o.json?token=abc")
    clock.now += 301

    with caplog.at_level("WARNING", logger="dial_openapi_to_mcp.spec_fetch"):
        await fetch_openapi_spec(url)

    assert "serving the copy fetched" in caplog.text
    assert "s3cret" not in caplog.text and "user" not in caplog.text and "token" not in caplog.text


@pytest.mark.asyncio
async def test_stale_retry_never_outlives_grace_period(schema_server, clock, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_STALE_TTL", "100")
    _good(schema_server)
    url = schema_server.base + "/o.json"
    await fetch_openapi_spec(url)
    _down(schema_server)
    clock.now += 390  # inside ttl(300) + grace(100): stale copy served, 30 s backoff starts
    await fetch_openapi_spec(url)

    clock.now += 11  # 401 s old: past the grace period although the backoff has not ended
    with pytest.raises(SpecFetchError):
        await fetch_openapi_spec(url)


@pytest.mark.asyncio
async def test_refreshed_entry_is_evicted_last(schema_server, clock, monkeypatch):
    monkeypatch.setenv("OPENAPI_URL_CACHE_MAX_ENTRIES", "2")
    for name in ("a", "b"):
        _good(schema_server, f"/{name}.json")
        await fetch_openapi_spec(f"{schema_server.base}/{name}.json")
    clock.now += 301
    await fetch_openapi_spec(f"{schema_server.base}/a.json")  # refresh a
    _good(schema_server, "/c.json")
    await fetch_openapi_spec(f"{schema_server.base}/c.json")

    assert [key[0].rsplit("/", 1)[1] for key in _spec_cache] == ["a.json", "c.json"]


class _DelayedHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        time.sleep(0.5)
        body = json.dumps(MINIMAL_OPENAPI_30).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_download_and_survive_leader_cancel():
    import asyncio

    handler = type("DelayedHandler", (_DelayedHandler,), {"hits": 0})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/o.json"
    try:
        leader = asyncio.create_task(fetch_openapi_spec(url))
        await asyncio.sleep(0.1)
        follower = asyncio.create_task(fetch_openapi_spec(url))
        await asyncio.sleep(0.1)
        leader.cancel()

        result = await follower

        assert json.loads(result)["info"]["title"] == MINIMAL_OPENAPI_30["info"]["title"]
        assert handler.hits == 1
        with pytest.raises(asyncio.CancelledError):
            await leader
    finally:
        server.shutdown()
        server.server_close()


def test_json_is_not_subject_to_the_yaml_node_cap(monkeypatch):
    monkeypatch.setattr("dial_openapi_to_mcp.spec_fetch._MAX_NODES", 10)
    big = {"info": {"title": "T"}, "items": list(range(100))}

    assert parse_spec_text(json.dumps(big))["items"][99] == 99
    yaml_text = "info: {title: T}\nitems:\n" + "".join(f"  - {i}\n" for i in range(100))
    with pytest.raises(ValueError, match="too large"):
        parse_spec_text(yaml_text)
