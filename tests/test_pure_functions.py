"""Unit tests for pure/stateless functions in server.py."""

import pytest

from dial_openapi_to_mcp.server import (
    _convert_swagger2_to_openapi30,
    _normalize_base_url,
    _rewrite_refs,
    get_api_id,
)

# ---------------------------------------------------------------------------
# get_api_id
# ---------------------------------------------------------------------------


def test_get_api_id_deterministic():
    spec = {"openapi": "3.0.0"}
    assert get_api_id(spec, "https://example.com") == get_api_id(spec, "https://example.com")


def test_get_api_id_different_base_url():
    spec = {"openapi": "3.0.0"}
    assert get_api_id(spec, "https://a.com") != get_api_id(spec, "https://b.com")


def test_get_api_id_extra_headers_change_id():
    spec = {"openapi": "3.0.0"}
    base = "https://example.com"
    no_headers = get_api_id(spec, base, None)
    with_headers = get_api_id(spec, base, [{"name": "X-Key", "value": "secret"}])
    assert no_headers != with_headers


def test_get_api_id_ignores_extra_header_values():
    spec = {"openapi": "3.0.0"}
    base = "https://example.com"
    a = get_api_id(spec, base, [{"name": "X-Key", "value": "aaa"}])
    b = get_api_id(spec, base, [{"name": "X-Key", "value": "bbb"}])
    assert a == b


def test_get_api_id_empty_extra_headers_same_as_none():
    spec = {"openapi": "3.0.0"}
    base = "https://example.com"
    assert get_api_id(spec, base, None) == get_api_id(spec, base, None)


# ---------------------------------------------------------------------------
# _normalize_base_url
# ---------------------------------------------------------------------------


def test_normalize_base_url_none():
    assert _normalize_base_url(None) is None


def test_normalize_base_url_empty():
    assert _normalize_base_url("") is None
    assert _normalize_base_url("   ") is None


def test_normalize_base_url_adds_https():
    assert _normalize_base_url("example.com") == "https://example.com"


def test_normalize_base_url_passthrough_https():
    assert _normalize_base_url("https://example.com") == "https://example.com"


def test_normalize_base_url_passthrough_http():
    assert _normalize_base_url("http://localhost:8080") == "http://localhost:8080"


# ---------------------------------------------------------------------------
# _rewrite_refs
# ---------------------------------------------------------------------------


def test_rewrite_refs_simple():
    obj = {"$ref": "#/definitions/Foo"}
    mapping = {"#/definitions/Foo": "#/components/schemas/Foo"}
    assert _rewrite_refs(obj, mapping) == {"$ref": "#/components/schemas/Foo"}


def test_rewrite_refs_nested():
    obj = {"schema": {"$ref": "#/definitions/Bar"}}
    mapping = {"#/definitions/Bar": "#/components/schemas/Bar"}
    result = _rewrite_refs(obj, mapping)
    assert result["schema"]["$ref"] == "#/components/schemas/Bar"


def test_rewrite_refs_in_list():
    obj = [{"$ref": "#/definitions/A"}, {"$ref": "#/definitions/B"}]
    mapping = {
        "#/definitions/A": "#/components/schemas/A",
        "#/definitions/B": "#/components/schemas/B",
    }
    result = _rewrite_refs(obj, mapping)
    assert result[0]["$ref"] == "#/components/schemas/A"
    assert result[1]["$ref"] == "#/components/schemas/B"


def test_rewrite_refs_leaves_other_keys_unchanged():
    obj = {"type": "object", "title": "Foo"}
    assert _rewrite_refs(obj, {}) == {"type": "object", "title": "Foo"}


def test_rewrite_refs_unknown_ref_unchanged():
    obj = {"$ref": "#/definitions/Unknown"}
    result = _rewrite_refs(obj, {})
    assert result["$ref"] == "#/definitions/Unknown"


# ---------------------------------------------------------------------------
# _convert_swagger2_to_openapi30
# ---------------------------------------------------------------------------


@pytest.fixture
def swagger_spec():
    return {
        "swagger": "2.0",
        "info": {"title": "Test", "version": "1.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "produces": ["application/json"],
                    "parameters": [{"in": "query", "name": "limit", "type": "integer"}],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"$ref": "#/definitions/ItemList"},
                        }
                    },
                }
            }
        },
        "definitions": {"ItemList": {"type": "array", "items": {"type": "string"}}},
    }


def test_convert_sets_openapi_version(swagger_spec):
    result = _convert_swagger2_to_openapi30(swagger_spec)
    assert result["openapi"] == "3.0.0"
    assert "swagger" not in result


def test_convert_builds_servers(swagger_spec):
    result = _convert_swagger2_to_openapi30(swagger_spec)
    assert result["servers"] == [{"url": "https://api.example.com/v1"}]


def test_convert_moves_definitions_to_components(swagger_spec):
    result = _convert_swagger2_to_openapi30(swagger_spec)
    assert "schemas" in result["components"]
    assert "ItemList" in result["components"]["schemas"]


def test_convert_rewrites_refs(swagger_spec):
    result = _convert_swagger2_to_openapi30(swagger_spec)
    response_schema = result["paths"]["/items"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["$ref"] == "#/components/schemas/ItemList"


def test_convert_moves_security_definitions():
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "securityDefinitions": {"api_key": {"type": "apiKey", "in": "header", "name": "X-Key"}},
        "paths": {},
    }
    result = _convert_swagger2_to_openapi30(spec)
    assert "securitySchemes" in result["components"]
    assert "api_key" in result["components"]["securitySchemes"]


def test_convert_body_param_to_request_body():
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "post": {
                    "consumes": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {"type": "object"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    result = _convert_swagger2_to_openapi30(spec)
    op = result["paths"]["/items"]["post"]
    assert "requestBody" in op
    assert "application/json" in op["requestBody"]["content"]
    assert op["requestBody"]["required"] is True


def test_convert_form_data_to_request_body():
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/upload": {
                "post": {
                    "parameters": [
                        {"in": "formData", "name": "file", "type": "string", "required": True}
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    result = _convert_swagger2_to_openapi30(spec)
    op = result["paths"]["/upload"]["post"]
    assert "requestBody" in op
    assert "application/x-www-form-urlencoded" in op["requestBody"]["content"]


def test_convert_null_responses_gets_default():
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/download": {
                "get": {
                    "responses": None,
                }
            }
        },
    }
    result = _convert_swagger2_to_openapi30(spec)
    responses = result["paths"]["/download"]["get"]["responses"]
    assert "default" in responses
    assert responses["default"]["description"] == "No response schema defined"


def test_convert_response_schema_uses_produces():
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "produces": ["application/json"],
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"type": "array"},
                        }
                    }
                }
            }
        },
    }
    result = _convert_swagger2_to_openapi30(spec)
    response = result["paths"]["/items"]["get"]["responses"]["200"]
    assert "content" in response
    assert "application/json" in response["content"]
    assert response["content"]["application/json"]["schema"] == {"type": "array"}
