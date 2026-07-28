# Security Model

OpenAPI to MCP is not a general-purpose open proxy, but it also does not restrict outbound destinations itself. Deploy it only in an environment where an external network egress control (firewall, egress proxy, or network policy) already restricts which hosts it can reach — including blocking internal services and cloud metadata endpoints.

## Outbound requests

The bridge does not validate or restrict the destination host of an outbound request; that is delegated entirely to network-level egress controls. It always disables HTTP redirect following.

## Credentials and headers

DIAL external-service credentials are fetched per request and injected through request-local context. A failure to resolve credentials returns an MCP error result carrying a `_meta["dial.epam.com/error"].status_code`: `404` when the service isn't configured on the application, `401` (with a `_meta["dial.epam.com/auth-challenge"]` signin challenge) when it's configured but the user hasn't signed in yet — see [Configuration Reference](../CONFIGURATION.md#dial-credentials). The bridge does not retain DIAL credentials in cache entries.

Client-forwarded headers are gated by a block list (authorization, cookies, proxy/routing, and forwarding headers by default) and, only when `OUTBOUND_HEADER_ALLOWLIST` is explicitly set, an additional allowlist restricting forwarding to the listed names. Both the block list and the allowlist are operator-overridable via env vars (`OUTBOUND_HEADER_BLOCKLIST`, `OUTBOUND_HEADER_ALLOWLIST`) — see [Configuration Reference](../CONFIGURATION.md#outbound-header-forwarding). Loosening the block list also loosens which header name a DIAL-resolved credential is allowed to use.

## Logging

Logs are metadata-only. Do not add body content, OpenAPI documents, argument values, token values, header values, or URL query strings to log records.

## Reporting vulnerabilities

See [SECURITY.md](../SECURITY.md).
