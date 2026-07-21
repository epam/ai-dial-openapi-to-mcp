# Architecture

OpenAPI to MCP is a streamable-HTTP MCP server that creates a tool surface from request metadata.

```mermaid
sequenceDiagram
    participant C as MCP client
    participant B as OpenAPI to MCP
    participant D as DIAL Core
    participant A as allowlisted API
    C->>B: /mcp + OpenAPI metadata
    B->>B: validate spec, destination, and headers
    alt DIAL external service requested
        B->>D: resolve request-scoped credential
        D-->>B: credential header
    end
    B->>B: reuse or create cached MCP definition
    C->>B: tools/call
    B->>A: request with allowed headers and scoped credential
    A-->>B: response
    B-->>C: MCP tool result
```

## Trust boundaries

- MCP metadata, OpenAPI documents, tool arguments, destinations, and forwarded headers are untrusted input.
- Operators configure the host and forwarded-header allowlists.
- DIAL credentials are resolved per request and injected only into the outbound request context.
- Cache entries own generated MCP definitions and HTTP clients, but do not own request credentials.
- The process must be deployed behind authenticated ingress; the bridge does not authenticate clients itself.
