# Changelog

All notable changes to OpenAPI to MCP are documented in this file.

The project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `multipart/form-data` properties with `format: binary` (single or array) are sent as real file parts with a filename and content type instead of plain form fields, so file upload endpoints accept them. File values can be text, a `data:` URI, or an object `{filename, content, contentType, encoding}`. See [docs/file-uploads.md](docs/file-uploads.md).
- A file argument passed as a JSON-serialized object string (`"{\"filename\": \"SKILL.md\", \"content\": ...}"`) is read as the file object instead of being uploaded as text under the field name.
- Path parameters keep `.` (`SKILL.md` was sent as `SKILL%2Emd`), and keep `/` when the parameter is marked `x-mcp-allow-reserved: true` / `allowReserved: true` or `PATH_PARAMS_ALLOW_RESERVED=true` is set. Empty, `.` and `..` segments are rejected. See [docs/path-parameters.md](docs/path-parameters.md).
- Tool results are built from the whole HTTP response: text bodies are returned as text, images/audio/binary bodies as base64 content (they failed or were corrupted), the status and headers such as `ETag` are returned in `_meta` and a status line, and non-JSON bodies no longer fail output validation. `MCP_VALIDATE_OUTPUT=false` or `x-mcp: {validateOutput: false}` turns validation off. See [docs/responses.md](docs/responses.md).

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
- Documented the client-selected destination trust boundary; operators may add network restrictions according to their environment.

[Unreleased]: https://github.com/epam/ai-dial-openapi-to-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/epam/ai-dial-openapi-to-mcp/releases/tag/v0.1.0
