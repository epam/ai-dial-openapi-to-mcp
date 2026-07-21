import json
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
EXTENDED_EXAMPLES = ["openapi-users-extended.json", "openapi-todo-extended.json"]


def _collect_x_mcp_blocks(spec: dict) -> list[dict]:
    blocks = []
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return blocks
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            x_mcp = operation.get("x-mcp")
            if isinstance(x_mcp, dict):
                blocks.append(x_mcp)
    return blocks


def _assert_no_refs(schema: object) -> None:
    if isinstance(schema, dict):
        assert "$ref" not in schema
        for value in schema.values():
            _assert_no_refs(value)
    elif isinstance(schema, list):
        for item in schema:
            _assert_no_refs(item)


def test_example_configs_valid_json():
    for filename in EXTENDED_EXAMPLES:
        data = json.loads((EXAMPLES_DIR / filename).read_text(encoding="utf-8"))
        assert data.get("openapi") == "3.0.0"


def test_x_mcp_schemas_inline():
    for filename in EXTENDED_EXAMPLES:
        spec = json.loads((EXAMPLES_DIR / filename).read_text(encoding="utf-8"))
        x_mcp_blocks = _collect_x_mcp_blocks(spec)
        assert x_mcp_blocks, f"No x-mcp blocks found in {filename}"
        for x_mcp in x_mcp_blocks:
            input_schema = x_mcp.get("inputSchema")
            output_schema = x_mcp.get("outputSchema")
            if isinstance(input_schema, dict):
                _assert_no_refs(input_schema)
            if isinstance(output_schema, dict):
                _assert_no_refs(output_schema)
