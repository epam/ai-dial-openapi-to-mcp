# MCP Cache Guide

OpenAPI to MCP caches generated MCP definitions and their HTTP clients in-process. The cache exists to avoid rebuilding a FastMCP definition for every request; it is not a credential store.

## Behavior

| Capability | Behavior |
|---|---|
| Cache key | Derived from the OpenAPI document, selected destination, and forwarded header names—not header values. |
| LRU eviction | When `MCP_CACHE_MAX_SIZE` is reached, the least recently used entry is removed. |
| TTL | Entries expire after idle time defined by `MCP_CACHE_TTL`. |
| Construction | Concurrent requests for the same key share one in-flight construction. |
| Ownership | Each cache entry owns its HTTP client; removal, replacement, expiry, and clear close it. |
| Credentials | DIAL credentials are request-scoped and are never written to cached client defaults. |

## Settings

| Variable | Default | Meaning |
|---|---:|---|
| `MCP_CACHE_MAX_SIZE` | `100` | Maximum entries. `0` removes the size limit. |
| `MCP_CACHE_TTL` | `3600` | Idle expiry in seconds. `0` disables expiry. |
| `MCP_CACHE_CLEANUP_INTERVAL` | `300` | Cleanup interval in seconds. `0` disables periodic cleanup. |

## Operational guidance

- Use a finite size and TTL in multi-tenant deployments.
- Do not expose cache identifiers or mutation controls to untrusted MCP clients.
- Treat cache metrics as protected operational data.
- Shut down the service through its ASGI lifecycle so cached clients and cleanup tasks close on the active event loop.
