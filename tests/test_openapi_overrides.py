"""
Tests for OpenAPI x-mcp override handling.
"""

from dial_openapi_to_mcp.server import _collect_x_mcp_overrides
from dial_openapi_to_mcp.server import openapi_extend as generate_extended_openapi


class DummyRoute:
    def __init__(self, extensions):
        self.extensions = extensions


class DummyComponent:
    def __init__(self):
        self.description = "default description"
        self.parameters = {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "user_id": {"type": "string"},
            },
        }


def test_collect_x_mcp_overrides_no_extensions():
    openapi_spec = {
        "openapi": "3.0.0",
        "info": {"title": "No Overrides", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "operationId": "list_users",
                    "summary": "List users",
                }
            }
        },
    }

    mcp_names, component_fn = _collect_x_mcp_overrides(openapi_spec)

    assert mcp_names is None
    assert component_fn is None


def test_collect_x_mcp_overrides_apply():
    openapi_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Overrides", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "operationId": "list_users",
                    "summary": "List users",
                    "x-mcp": {
                        "name": "user_list",
                        "description": "List users with pagination and filters",
                        "parameters": {
                            "limit": {"description": "Max users to return (1-100)"},
                            "missing_param": {"description": "Ignored"},
                        },
                    },
                }
            },
            "/users/{user_id}": {
                "get": {
                    "summary": "Get user",
                    "x-mcp": {
                        "description": "Fetch a single user by ID",
                        "parameters": {
                            "user_id": {"description": "User identifier"},
                        },
                    },
                }
            },
        },
    }

    mcp_names, component_fn = _collect_x_mcp_overrides(openapi_spec)

    assert mcp_names == {"list_users": "user_list"}
    assert component_fn is not None

    route = DummyRoute(
        extensions={
            "x-mcp": {
                "description": "List users with pagination and filters",
                "parameters": {
                    "limit": {"description": "Max users to return (1-100)"},
                    "missing_param": {"description": "Ignored"},
                },
            }
        }
    )
    component = DummyComponent()

    component_fn(route, component)

    assert component.description == "List users with pagination and filters"
    assert (
        component.parameters["properties"]["limit"]["description"] == "Max users to return (1-100)"
    )
    assert "description" not in component.parameters["properties"]["user_id"]


def test_generate_extended_openapi_from_inline_spec():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Example", "version": "1.0.0"},
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "summary": "Get item",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "description": "Item identifier",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    result = __import__("asyncio").run(generate_extended_openapi(spec=spec, as_json=False))

    assert result["success"] is True
    extended = result["openapi"]
    operation = extended["paths"]["/items/{id}"]["get"]
    assert "x-mcp" in operation
    assert "name" in operation["x-mcp"]
    assert "description" in operation["x-mcp"]
    assert operation["x-mcp"]["parameters"]["id"]["description"] == "Item identifier"
