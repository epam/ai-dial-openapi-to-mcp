# Tool responses

Tools return the whole HTTP response, not just a JSON body. fastmcp alone decodes every non-JSON
body as text (binary content fails or is corrupted), drops response headers, and fails MCP output
validation when a tool with an output schema returns anything but JSON.

## Body

The response `Content-Type` decides the result, not the media type the spec declares:

| Response body | Tool result |
|---|---|
| JSON (`application/json`, `*+json`) | structured content (validated, see below) plus the JSON as text |
| text (`text/*`, XML, YAML, …) | a text block with the body |
| image (`image/*`) / audio (`audio/*`) | an image / audio block, base64 encoded |
| any other binary | an embedded blob resource (`uri` is the request URL without its query) |
| empty | only the status line below |

If the tool's output schema wraps a single value (e.g. a declared `type: string, format: binary`
body), text and binary results also set structured content `{"result": <text or base64>}`, so
they satisfy the schema.

## Status and headers

Every successful result carries the status code and selected response headers:

- in `_meta["dial.epam.com/response"]` as `{"status_code": 200, "headers": {...}}`;
- as a final text block, e.g. `HTTP 200; etag: "v2"`, because many MCP clients show only content.

Exposed headers: `ETag`, `Last-Modified`, `Location`, `Content-Type`, `Content-Length`,
`Content-Disposition`, plus any header the spec declares on the operation's 2xx responses.
`Set-Cookie`, `Cookie`, `Authorization`, `Proxy-Authorization` and `Proxy-Authenticate` are never
returned.

HTTP error responses still fail the tool call with the status and error body.

## Output validation

JSON results are validated against the output schema derived from the spec. Results carrying
`_meta` skip the MCP SDK's own validation, so the bridge validates them itself and fails the call
with `Output validation error: ...` on a mismatch.

- Set `MCP_VALIDATE_OUTPUT=false` to turn validation off for every spec (see
  [CONFIGURATION.md](../CONFIGURATION.md#tool-responses)). Tools then publish no output schema.
- Add `x-mcp: {validateOutput: false}` to an operation to turn it off for that tool only, or
  `validateOutput: true` to keep it on when the global switch is off.
- Operations whose 2xx responses declare no JSON body publish no output schema.
