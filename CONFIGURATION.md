# Configuration Reference

OpenAPI to MCP reads configuration from environment variables. Copy [`.env.template`](.env.template) to `.env` for local values; do not commit that file. The CLI loads `.env` at startup, while values already present in the process environment take precedence.

> [!WARNING]
> Configuration, OpenAPI documents, request headers, destinations, and tool arguments can contain sensitive data. Keep real values out of source control, logs, issue reports, and examples.

The bridge intentionally accepts client-selected API destinations. No destination allowlist or external egress control is required. Operators with stricter destination policies can apply network controls outside the application.

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

Cache keys are derived only from the OpenAPI spec and normalized base URL. Cached definitions and HTTP clients contain no forwarded header names, header values, or DIAL credentials; those values exist only in request scope.

## Outbound header forwarding

| Variable | Default | Required | Description |
|---|---:|---|---|
| `OUTBOUND_HEADER_ALLOWLIST` | Unset | No | Comma-separated request-header names clients may forward. **Unset:** all non-blocked headers may be forwarded. **Explicitly empty:** no client headers may be forwarded. **Populated:** only listed, non-blocked names may be forwarded. |
| `OUTBOUND_HEADER_BLOCKLIST` | *(see below)* | No | Comma-separated names that can never be forwarded, replacing the built-in default. Unset keeps the default: `authorization`, `cookie`, `set-cookie`, `proxy-authorization`, `host`, `connection`, `keep-alive`, `proxy-authenticate`, `te`, `trailer`, `transfer-encoding`, `upgrade`, `forwarded`, `x-forwarded-for`, `x-forwarded-host`, `x-forwarded-proto`. |

The block list takes precedence over the allowlist. By default, the bridge blocks routing, proxy, cookie, and authorization headers and never follows redirects. `OUTBOUND_HEADER_BLOCKLIST` gives operators full control over the blocked set, including the ability to unblock `authorization` or `cookie`; do so only after reviewing the credential-exposure and request-smuggling implications. The resolved block set also gates the header name used by a DIAL-resolved credential.

All forwarded header values are read from the current request and injected only into that request's outbound call. They are not written to shared client defaults or cache entries.

## DIAL credentials

| Variable | Default | Required | Description |
|---|---:|---|---|
| `DIAL_URL` | — | Conditional | DIAL Core URL when a request uses `ai_dial_config.external_service`. |
| `DIAL_CORE_URL` | — | Conditional | Backward-compatible alternative to `DIAL_URL`. |

When `external_service` is selected, requests must include `x-dial-application-id` and `Api-Key`. Missing configuration, validation failures, and credential-service errors fail the tool call as an MCP error result (`isError: true`); the bridge does not make an unauthenticated fallback call. Resolved credential values are request-scoped and never retained in cache entries.

The result's `_meta` carries machine-readable detail distinguishing why credential resolution failed:

- `_meta["dial.epam.com/error"]` — always present on failure: `{"status_code": <int>, "external_service": <str>}`. `status_code` is `404` when `external_service` is not configured on the DIAL application, `401` when it is configured but DIAL Core has no stored credential yet, or `500` for other configuration and credential-service errors.
- `_meta["dial.epam.com/auth-challenge"]` — present only for `401`: a list of `{"method": "external-service/signin", "scope": <str>}` objects. `scope` is `<application>/external_services/<external_service>` and can be sent as `params.url` in an `external-service/signin` JSON-RPC request. After sign-in completes, retry the original tool call.
