# File uploads (multipart/form-data)

Operations whose request body is `multipart/form-data` with `format: binary` properties are exposed
as tools that send those properties as real file parts, with a `filename` and a `Content-Type`.
fastmcp alone sends every multipart value as a plain form field, which upload endpoints reject.

Detection is driven by the OpenAPI request body schema only:

| Schema property | Sent as |
|---|---|
| `type: string, format: binary` | one file part |
| `type: array, items: {type: string, format: binary}` | one file part per item, under the same field name |
| any other property | a plain form field (unchanged) |

## Tool arguments

Tool arguments are JSON, so a file value is one of:

- **a string**: sent as UTF-8 text, or decoded when it is a `data:<mime>;base64,<data>` URI;
- **an object** `{filename, content, contentType, encoding}`:
  - `content` (required): plain text, a `data:` URI, or base64 when `encoding` is `"base64"`;
  - `filename`: part file name (defaults to the field name);
  - `contentType`: part MIME type;
  - `encoding`: `"text"` (default) or `"base64"`.
- **a list** of the above, for array properties.

Some clients serialize object arguments into strings. A string that is a JSON object with a string
`content` and no keys other than `filename`, `content`, `contentType` and `encoding` is read as that
object. Any other string, including other JSON documents, is sent as file content.

Plain strings are never guessed to be base64: text such as `SKILL` is valid base64 too. Binary
content must be passed as a `data:` URI or with `encoding: "base64"`.

The part `Content-Type` is the first available of: the object's `contentType`, the `data:` URI MIME
type, the spec's `encoding.<property>.contentType`, a guess from the file name, and
`application/octet-stream`.

## Example

```yaml
requestBody:
  content:
    multipart/form-data:
      schema:
        type: object
        properties:
          files:
            type: array
            items: {type: string, format: binary}
```

```json
{
  "files": [
    {"filename": "SKILL.md", "content": "---\nname: my-skill\n---\n# My skill\n"},
    {"filename": "logo.png", "content": "iVBORw0KGgo...", "encoding": "base64"}
  ]
}
```
