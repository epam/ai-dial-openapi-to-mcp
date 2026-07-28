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
| `OUTBOUND_HEADER_ALLOWLIST` | — | No | Comma-separated request-header names that clients may forward to the selected API. When unset, no allowlist restriction applies — any header not on the block list is forwarded. Setting this (to any value, including empty) switches to strict allowlist enforcement: only listed names are forwarded. |
| `OUTBOUND_HEADER_BLOCKLIST` | *(see below)* | No | Comma-separated request-header names that can never be forwarded, replacing the built-in default entirely. Unset keeps the default: `authorization`, `cookie`, `set-cookie`, `proxy-authorization`, `host`, `connection`, `keep-alive`, `proxy-authenticate`, `te`, `trailer`, `transfer-encoding`, `upgrade`, `forwarded`, `x-forwarded-for`, `x-forwarded-host`, `x-forwarded-proto`. |

By default, the bridge blocks routing, proxy, cookie, and authorization headers from generic client forwarding, and never follows redirects. `OUTBOUND_HEADER_BLOCKLIST` gives operators full control over this set, including the ability to unblock `authorization`/`cookie` if explicitly chosen — do this only if you understand the consequences. This same resolved block set also gates which header name a DIAL-resolved credential is allowed to use, so loosening it affects that check too. DIAL-resolved credentials otherwise use a separate request-scoped mechanism. The bridge does not restrict outbound destinations itself — that is expected to be enforced by external network egress controls (see [Security Model](docs/security.md)).

## DIAL credentials

| Variable | Default | Required | Description |
|---|---:|---|---|
| `DIAL_URL` | — | Conditional | DIAL Core URL when a request uses `ai_dial_config.external_service`. |
| `DIAL_CORE_URL` | — | Conditional | Backward-compatible alternative to `DIAL_URL`. |

When `external_service` is selected, requests must include `x-dial-application-id` and `Api-Key`. Missing configuration, validation failures, and credential-service errors fail the tool call as an MCP error result (`isError: true`); the bridge does not make an unauthenticated fallback call.

The result's `_meta` carries machine-readable detail distinguishing why credential resolution failed:

- `_meta["dial.epam.com/error"]` — always present on failure: `{"status_code": <int>, "external_service": <str>}`. `status_code` is `404` when `external_service` isn't configured on the DIAL application (not present in its `external_services` registry), `401` when it's configured but DIAL core has no stored credential yet (the end user needs to log in), or `500` for other misconfiguration/credential-service errors.
- `_meta["dial.epam.com/auth-challenge"]` — present only when `status_code` is `401`: a list of `{"method": "external-service/signin", "scope": <str>}` objects (a list so a future response can name more than one pending signin). `scope` is the DIAL external-service path — `<application>/external_services/<external_service>` — to send as `params.url` on an `external-service/signin` JSON-RPC request. A client reacts by sending that signin request, then retrying the original tool call once signin completes.

