# Security Model

OpenAPI to MCP is an outbound bridge for trusted MCP clients. It intentionally accepts client-selected API destinations and has no application destination allowlist. External egress controls are not required; deployments with stricter destination policies can add a firewall, egress proxy, or network policy.

## Outbound requests

The bridge does not validate or restrict destination hosts, and it never follows HTTP redirects. Deploy it behind authenticated ingress and grant access only to clients trusted to select destinations and supply OpenAPI documents.

## Credentials and headers

DIAL external-service credentials are fetched per request and injected through request-local context. Resolution failures return an MCP error result with `_meta["dial.epam.com/error"].status_code`: `404` when the service is not configured on the application, `401` with a `_meta["dial.epam.com/auth-challenge"]` when the service is configured but the user has not signed in, and `500` for other failures. See the [configuration reference](../CONFIGURATION.md#dial-credentials).

Client-forwarded headers are gated by a block list and, only when `OUTBOUND_HEADER_ALLOWLIST` is set, an additional allowlist. The exact semantics are:

- unset allowlist: every non-blocked header may be forwarded;
- explicitly empty allowlist: no client header may be forwarded;
- populated allowlist: only listed, non-blocked names may be forwarded.

Both lists are operator-overridable. Loosening the block list also loosens which header name a DIAL-resolved credential may use, so changes require security review. See the [configuration reference](../CONFIGURATION.md#outbound-header-forwarding).

Forwarded header values and DIAL credentials are request-scoped. Cache keys are derived only from the OpenAPI spec and base URL; cached definitions and HTTP clients retain no header names, header values, or credentials.

## Sensitive data and logging

Treat OpenAPI documents, destinations, URL queries, tool arguments, request and response bodies, header values, credentials, and DIAL metadata as potentially sensitive. Logs must contain operational metadata only. Do not put sensitive values in logs, examples, test fixtures, issue reports, pull requests, or screenshots.

## Reporting vulnerabilities

Do not open a public issue. Follow [SECURITY.md](../SECURITY.md) to report vulnerabilities privately.
