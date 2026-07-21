# Configuration Reference

OpenAPI to MCP reads configuration from environment variables. Store local values in `.env` copied from [`.env.template`](.env.template); do not commit that file.

> [!IMPORTANT]
> The bridge sends outbound requests from client-provided OpenAPI metadata. It relies on external network egress controls (a firewall or egress proxy) to restrict which destinations are reachable — deploy it in an environment where that control exists before accepting client traffic.

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

## Outbound header forwarding

| Variable | Default | Required | Description |
|---|---:|---|---|
| `OUTBOUND_HEADER_ALLOWLIST` | — | No | Comma-separated request-header names that clients may forward to the selected API. |

The bridge blocks routing, proxy, cookie, and authorization headers from generic client forwarding, and never follows redirects. DIAL-resolved credentials use a separate request-scoped mechanism. It does not restrict outbound destinations itself — that is expected to be enforced by external network egress controls (see [Security Model](docs/security.md)).

## DIAL credentials

| Variable | Default | Required | Description |
|---|---:|---|---|
| `DIAL_URL` | — | Conditional | DIAL Core URL when a request uses `ai_dial_config.external_service`. |
| `DIAL_CORE_URL` | — | Conditional | Backward-compatible alternative to `DIAL_URL`. |

When `external_service` is selected, requests must include `x-dial-application-id` and `Api-Key`. Missing configuration, validation failures, and credential-service errors fail the tool call; the bridge does not make an unauthenticated fallback call.

