"""Shared test data for all test modules."""

MINIMAL_OPENAPI_30 = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
                "summary": "List items",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}

MINIMAL_SWAGGER_20 = {
    "swagger": "2.0",
    "info": {"title": "Test API", "version": "1.0"},
    "host": "api.example.com",
    "basePath": "/v1",
    "schemes": ["https"],
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
                "summary": "List items",
                "produces": ["application/json"],
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}
