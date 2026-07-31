# Changelog

All notable changes to OpenAPI to MCP are documented in this file.

The project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-07-29

### Added

- Initial open-source release of the streamable-HTTP OpenAPI-to-MCP bridge.
- Dynamic OpenAPI 3.x tool generation, Swagger 2.0 conversion.
- Process-local LRU/TTL cache for generated MCP definitions and HTTP clients.
- Request-scoped DIAL external-service credential resolution with machine-readable `404`, `401` sign-in challenge, and `500` error metadata.
- Operator-configurable outbound header block list and optional allowlist. An unset allowlist permits non-blocked headers, an explicitly empty allowlist permits none, and a populated allowlist restricts forwarding to listed names.
- Package, container, contribution, security-reporting, and technical documentation.

### Security

- Forwarded header values and DIAL credentials remain request-scoped; cached entries contain no header values or credentials.
- Redirect following is disabled, sensitive values are excluded from logs, and DIAL credential failures do not fall back to unauthenticated requests.
- Documented the client-selected destination trust boundary. No application destination allowlist or external egress control is required; operators may add network restrictions according to their environment.

[Unreleased]: https://github.com/epam/ai-dial-openapi-to-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/epam/ai-dial-openapi-to-mcp/releases/tag/v0.1.0
