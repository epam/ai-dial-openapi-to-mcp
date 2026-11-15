# Configuration Reference

OpenAPI to MCP reads configuration from environment variables. Copy [`.env.template`](.env.template) to `.env` for local values; do not commit that file. The CLI loads `.env` at startup, while values already present in the process environment take precedence.

> [!WARNING]
> Configuration, OpenAPI documents, request headers, destinations, and tool arguments can contain sensitive data. Keep real values out of source control, logs, issue reports, and examples.

The bridge intentionally accepts client-selected API destinations. Operators with stricter destination policies can apply network controls outside the application.

## Server

| Variable | Default | Required | Description |
|---|---:|---|---|
| `MCP_PORT` | `8080` | No | Streamable HTTP server port. |
| `LOG_LEVEL` | `INFO` | No | Python logging level. Logs contain operational metadata only. |

## Cache

| Variable | Default | Required | Description |
|---|---:|---|---|
| `MCP_CACHE_MAX_SIZE` | `100` | No | Maximum cached generated MCP definitions; `0` disables the size limit. |
| `MCP_CACHE_TTL` | `3600` | No | Idle expiry in seconds; `0` disables TTL expiry. |
| `MCP_CACHE_CLEANUP_INTERVAL` | `300` | No | Expired-entry cleanup period in seconds; `0` disables periodic cleanup. |

MCP cache keys are derived only from the OpenAPI spec and normalized base URL. Cached definitions and HTTP clients contain no forwarded header names, header values, or DIAL credentials; those values exist only in request scope. The separate cache of fetched schemas (see [OpenAPI spec by URL](#openapi-spec-by-url)) is keyed by the URL plus a SHA-256 hash of the forwarded headers it was fetched with; only the hash is stored, never the header values.

## OpenAPI spec by URL

An inline `openapi` value may be an object or JSON/YAML text (the DIAL application schema allows both), in OpenAPI 3.x or Swagger 2.0 form.

A client can supply `openapi_url` (`X-OPENAPI-URL` header, `_meta.ai_dial_config.openapi_url`, or `_meta.openapi_url`) instead of an inline document. The bridge fetches it with a plain `GET`; JSON and YAML are detected from the content, not the URL or `Content-Type`, and Swagger 2.0 is converted to OpenAPI 3.0. An inline document wins when both are present. Without an explicit base URL, the first `servers` entry is used (a relative entry resolves against the schema URL); with none, the schema URL's origin is used.

| Variable | Default | Required | Description |
|---|---:|---|---|
| `OPENAPI_FETCH_TIMEOUT` | `10` | No | Total seconds allowed for the whole schema download (connect, headers and body), so a slow-drip server cannot hold a request open. |
| `OPENAPI_FETCH_MAX_BYTES` | `10485760` | No | Maximum schema size; larger responses are rejected. |
| `OPENAPI_URL_CACHE_TTL` | `3600` | No | Seconds a fetched schema is reused; `0` fetches on every request. |
| `OPENAPI_URL_CACHE_STALE_TTL` | `86400` | No | Extra seconds, on top of `OPENAPI_URL_CACHE_TTL`, that the previous copy of a schema is served when refetching it fails (host down, error status, invalid document), so an outage of the schema host does not break tools. With the defaults a copy is usable for up to 25 h after it was fetched. Retries happen at most every 30 s and each fallback logs a warning. `0` disables the fallback. |
| `OPENAPI_URL_CACHE_MAX_ENTRIES` | `32` | No | Maximum fetched schemas kept (one per URL and set of forwarded headers); the oldest are dropped first. `0` disables the cache. Each entry can be up to `OPENAPI_FETCH_MAX_BYTES`. |
| `OPENAPI_FETCH_SEND_AUTH` | `false` | No | `true` sends the request's validated `extra_headers` and, when `external_service` is configured, the DIAL-resolved credential on the schema request. Off by default: the schema request is anonymous. |

Only `http` and `https` URLs are accepted and redirects are not followed. By default the schema request is anonymous, so a caller-supplied `openapi_url` can never receive credentials. Protected schema endpoints need `OPENAPI_FETCH_SEND_AUTH=true`; with it, the schema host receives the same `extra_headers` and DIAL credential as the API calls, so enable it only where every host a client can name is trusted. Also pin `base_url` (in the application configuration or `X-BASE-URL`) when using `openapi_url`: without it the fetched document's `servers` entry decides where tool calls, which carry the forwarded headers and DIAL credential, are sent, so whoever controls the schema host can redirect them. Fetched schemas are cached per URL and per set of forwarded headers, never shared across credentials, including the stale fallback. As with `base_url`, restricting which hosts the bridge may reach is expected to be enforced by external network egress controls. A schema that cannot be fetched or parsed fails the request with an error instead of falling back to the default tools.

## Outbound header forwarding

| Variable | Default | Required | Description |
|---|---:|---|---|
| `OUTBOUND_HEADER_ALLOWLIST` | Unset | No | Comma-separated request-header names clients may forward. **Unset:** all non-blocked headers may be forwarded. **Explicitly empty:** no client headers may be forwarded. **Populated:** only listed, non-blocked names may be forwarded. |
| `OUTBOUND_HEADER_BLOCKLIST` | *(see below)* | No | Comma-separated names that can never be forwarded, replacing the built-in default. Unset keeps the default: `authorization`, `cookie`, `set-cookie`, `proxy-authorization`, `host`, `connection`, `keep-alive`, `proxy-authenticate`, `te`, `trailer`, `transfer-encoding`, `upgrade`, `forwarded`, `x-forwarded-for`, `x-forwarded-host`, `x-forwarded-proto`. |

The block list takes precedence over the allowlist. By default, the bridge blocks routing, proxy, cookie, and authorization headers and never follows redirects. `OUTBOUND_HEADER_BLOCKLIST` gives operators full control over the blocked set, including the ability to unblock `authorization` or `cookie`; do so only after reviewing the credential-exposure and request-smuggling implications. The resolved block set also gates the header name used by a DIAL-resolved credential.

All forwarded header values are read from the current request and injected only into that request's outbound call. They are not written to shared client defaults or cache entries.

## Path parameters

| Variable | Default | Required | Description |
|---|---:|---|---|
| `PATH_PARAMS_ALLOW_RESERVED` | `false` | No | `true` keeps `/` unencoded in every path parameter of every spec. Otherwise only parameters marked `x-mcp-allow-reserved: true` or `allowReserved: true` keep it. Empty, `.` and `..` segments are always rejected. See [docs/path-parameters.md](docs/path-parameters.md). |

## Tool responses

| Variable | Default | Required | Description |
|---|---:|---|---|
| `MCP_VALIDATE_OUTPUT` | `true` | No | `false` stops validating JSON tool results against the output schemas derived from the spec, and tools publish no output schema. Per operation, `x-mcp: {validateOutput: false}` (or `true`) overrides it. See [docs/responses.md](docs/responses.md). |
| `MCP_DEREFERENCE_SCHEMAS` | `true` | No | `true` inlines `$ref` in the tool input/output schemas (FastMCP default; genuinely circular refs stay as `$ref`/`$defs`). `false` keeps `$ref`/`$defs` everywhere. The `x-mcp` schema generator always inlines. Set `false` for large specs with deeply recursive schemas, where inlining can expand the schemas to 100+ MB, take about a minute per build and use hundreds of MB per cached spec. Clients that cannot resolve `$ref`/`$defs` need `true`. |

## DIAL credentials

| Variable | Default | Required | Description |
|---|---:|---|---|
| `DIAL_URL` | — | Conditional | DIAL Core URL when a request uses `ai_dial_config.external_service`. |
| `DIAL_CORE_URL` | — | Conditional | Backward-compatible alternative to `DIAL_URL`. |

When `external_service` is selected, requests must include `x-dial-application-id` and `Api-Key`. Missing configuration, validation failures, and credential-service errors fail the tool call as an MCP error result (`isError: true`); the bridge does not make an unauthenticated fallback call. Resolved credential values are request-scoped and never retained in cache entries.

The result's `_meta` carries machine-readable detail distinguishing why credential resolution failed:

- `_meta["dial.epam.com/error"]` — always present on failure: `{"status_code": <int>, "external_service": <str>}`. `status_code` is `404` when `external_service` is not configured on the DIAL application, `401` when it is configured but DIAL Core has no stored credential yet, or `500` for other configuration and credential-service errors.
- `_meta["dial.epam.com/auth-challenge"]` — present only for `401`: a list of `{"method": "external-service/signin", "scope": <str>}` objects. `scope` is `<application>/external_services/<external_service>` and can be sent as `params.url` in an `external-service/signin` JSON-RPC request. After sign-in completes, retry the original tool call.

## Health & readiness

| Path | Purpose | Response |
|---|---|---|
| `GET /health` | Liveness — the process is up. | `200 {"status": "ok"}` |
| `GET /ready` | Readiness — the cache-maintenance lifespan has finished startup. | `200 {"status": "ready"}` once ready, `503 {"status": "not ready"}` before. |

Both are unauthenticated HTTP endpoints on `MCP_PORT`, intended for Kubernetes liveness/readiness probes.

## Observability

| Variable | Default | Required | Description |
|---|---:|---|---|
| `OTEL_SERVICE_NAME` | `openapi-to-mcp` | No | Service name attached to traces, metrics, and logs. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Unset | No | OTLP collector endpoint. Required for any `otlp` exporter to actually export. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | No | `grpc` or `http/json`/`http/protobuf`. |
| `OTEL_TRACES_EXPORTER` | Unset | No | Set to `otlp` to enable trace export. Unset disables tracing entirely. |
| `OTEL_METRICS_EXPORTER` | Unset | No | Comma-separated: `otlp` to push metrics to the collector, `prometheus` to serve them locally. Unset disables metrics entirely. |
| `OTEL_LOGS_EXPORTER` | Unset | No | Set to `otlp` to additionally export existing application logs (see [Server](#server) `LOG_LEVEL`) via OTLP. Unset disables log export; console logging is unaffected either way. |
| `OTEL_RESOURCE_ATTRIBUTES` | Unset | No | Comma-separated `key=value` pairs merged into the OTEL resource. |

When `OTEL_METRICS_EXPORTER` includes `prometheus`, metrics are served on `:9464/metrics` (not `MCP_PORT`) for scraping. With no `OTEL_*_EXPORTER` variable set, telemetry setup is a complete no-op — no ports opened, no exporters started.
