# Security Model

OpenAPI to MCP is not a general-purpose open proxy, but it also does not restrict outbound destinations itself. Deploy it only in an environment where an external network egress control (firewall, egress proxy, or network policy) already restricts which hosts it can reach — including blocking internal services and cloud metadata endpoints.

## Outbound requests

The bridge does not validate or restrict the destination host of an outbound request; that is delegated entirely to network-level egress controls. It always disables HTTP redirect following.

## Credentials and headers

DIAL external-service credentials are fetched per request and injected through request-local context. A failure to resolve credentials fails the call. The bridge does not retain DIAL credentials in cache entries.

Client-forwarded headers require `OUTBOUND_HEADER_ALLOWLIST`. Authorization, cookies, proxy/routing, and forwarding headers are blocked from generic forwarding.

## Logging

Logs are metadata-only. Do not add body content, OpenAPI documents, argument values, token values, header values, or URL query strings to log records.

## Reporting vulnerabilities

See [SECURITY.md](../SECURITY.md).
