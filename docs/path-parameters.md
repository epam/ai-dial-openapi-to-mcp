# Path parameters

Path parameter values are percent-encoded before they are substituted into the URL template.

- `.` is kept, so file names such as `SKILL.md` reach the API unchanged. fastmcp alone sends
  `SKILL%2Emd`.
- `/` is encoded as `%2F` by default, so a value can never address a different path. A parameter
  that is itself a hierarchical path (`folder/sub/file.txt`) can opt in to keep `/`.
- Values that are, or contain, a `.` or `..` segment, and slash-separated values with an empty
  segment (`a//b`, `/a`, `a/`), are rejected before any request is sent, so opted-in parameters
  cannot be used for path traversal. An empty value is allowed; APIs use it to address a root.

## Keeping `/` in a parameter

Mark the path parameter in the OpenAPI spec with either key (on the operation, the path item, or
a referenced `components.parameters` entry):

```yaml
parameters:
  - name: path
    in: path
    required: true
    schema: {type: string}
    x-mcp-allow-reserved: true   # or: allowReserved: true
```

To keep `/` in every path parameter of every spec instead, set
`PATH_PARAMS_ALLOW_RESERVED=true` (see [CONFIGURATION.md](../CONFIGURATION.md#path-parameters)).

Parameters that keep `/` get a note in their tool input description, so models know they may pass
nested paths.

| Value | Default | Opted in |
|---|---|---|
| `SKILL.md` | `SKILL.md` | `SKILL.md` |
| `skills/my-skill` | `skills%2Fmy-skill` | `skills/my-skill` |
| `my skill/ü` | `my%20skill%2F%C3%BC` | `my%20skill/%C3%BC` |
| `a/../b`, `./a`, `a//b`, `..` | rejected | rejected |
