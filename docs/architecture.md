# Architecture

OpenAPI to MCP is a streamable-HTTP MCP server that creates a tool surface from request metadata.

```mermaid
sequenceDiagram
    participant C as MCP client
    participant B as OpenAPI to MCP
    participant D as DIAL Core
    participant A as destination API
    C->>B: /mcp + OpenAPI metadata
    B->>B: validate spec and headers; select destination
    alt DIAL external service requested
        B->>D: resolve request-scoped credential
        D-->>B: credential header
    end
    B->>B: reuse or create cached MCP definition
    C->>B: tools/call
    B->>A: request with request-scoped headers
    A-->>B: response
    B-->>C: MCP tool result
```

## Trust boundaries

- MCP metadata, OpenAPI documents, tool arguments, destinations, and forwarded headers are untrusted input and may contain sensitive data.
- The bridge intentionally accepts client-selected destinations. It has no application destination allowlist and does not require external egress controls; operators can apply network policy when their environment requires it.
- Operators configure the forwarded-header block list and optional allowlist. An unset allowlist permits non-blocked headers, an explicitly empty allowlist permits none, and a populated allowlist permits only listed, non-blocked names.
- Forwarded header values and DIAL credentials are resolved per request and injected only into that outbound request.
- Cache entries own generated MCP definitions and HTTP clients, keyed only by the OpenAPI spec and base URL; entries contain no forwarded header names, values, or credentials.
- The process must be deployed behind authenticated ingress; the bridge does not authenticate clients itself.
