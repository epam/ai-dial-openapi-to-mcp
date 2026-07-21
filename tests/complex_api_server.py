"""
Animal Shelter test API — Starlette ASGI app with complex schema patterns:
oneOf/allOf/discriminator, nullable fields, nested objects, arrays,
additionalProperties, enums, and multiple HTTP methods.
"""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# ---------------------------------------------------------------------------
# In-memory data store
# ---------------------------------------------------------------------------

_ANIMALS = [
    {
        "id": 1,
        "type": "cat",
        "name": "Whiskers",
        "tags": ["indoor", "friendly"],
        "metadata": {"color": "orange", "adopted": True},
        "indoor": True,
        "whisker_count": 24,
    },
    {
        "id": 2,
        "type": "dog",
        "name": "Rex",
        "tags": ["outdoor", "trained"],
        "metadata": {"color": "brown"},
        "breed": "German Shepherd",
        "trained": True,
    },
    {
        "id": 3,
        "type": "cat",
        "name": "Shadow",
        "tags": ["shy"],
        "metadata": {},
        "indoor": False,
        "whisker_count": None,
    },
]

_TRICKS = {
    1: [
        {"name": "sit", "difficulty": "easy", "success_rate": 0.95},
        {"name": "high-five", "difficulty": "medium", "success_rate": None},
    ],
    2: [
        {"name": "sit", "difficulty": "easy", "success_rate": 0.99},
        {"name": "roll over", "difficulty": "hard", "success_rate": 0.7},
        {"name": "fetch", "difficulty": "medium", "success_rate": 0.88},
    ],
    3: [],
}

_NEXT_ID = 4


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def list_animals(request: Request) -> JSONResponse:
    return JSONResponse(_ANIMALS)


async def create_animal(request: Request) -> JSONResponse:
    global _NEXT_ID
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"code": 400, "message": "Invalid JSON", "details": None}, status_code=400
        )
    animal = dict(body)
    animal["id"] = _NEXT_ID
    _NEXT_ID += 1
    _ANIMALS.append(animal)
    _TRICKS[animal["id"]] = []
    return JSONResponse(animal, status_code=201)


async def get_animal(request: Request) -> JSONResponse:
    try:
        animal_id = int(request.path_params["animal_id"])
    except (KeyError, ValueError):
        return JSONResponse(
            {"code": 400, "message": "Bad animal_id", "details": None}, status_code=400
        )
    for a in _ANIMALS:
        if a["id"] == animal_id:
            return JSONResponse(a)
    return JSONResponse(
        {"code": 404, "message": f"Animal {animal_id} not found", "details": None}, status_code=404
    )


async def get_tricks(request: Request) -> JSONResponse:
    try:
        animal_id = int(request.path_params["animal_id"])
    except (KeyError, ValueError):
        return JSONResponse(
            {"code": 400, "message": "Bad animal_id", "details": None}, status_code=400
        )
    if animal_id not in _TRICKS:
        return JSONResponse(
            {"code": 404, "message": f"Animal {animal_id} not found", "details": None},
            status_code=404,
        )
    return JSONResponse(_TRICKS[animal_id])


async def search_animals(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"code": 400, "message": "Invalid JSON", "details": None}, status_code=400
        )
    query = body.get("query", "").lower()
    filters = body.get("filters", {})
    limit = body.get("limit", 100)
    results = []
    for a in _ANIMALS:
        if query and query not in a["name"].lower():
            continue
        match = True
        for k, v in filters.items():
            if str(a.get(k, "")).lower() != str(v).lower():
                match = False
                break
        if match:
            results.append(a)
    return JSONResponse(results[:limit])


async def secret_animal(request: Request) -> JSONResponse:
    api_key = request.headers.get("x-api-key", "")
    if api_key != "test-secret":
        return JSONResponse(
            {"code": 401, "message": "Unauthorized", "details": None}, status_code=401
        )
    return JSONResponse({"secret": "There are 3 animals in total"})


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------

animal_api_app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/animals", list_animals, methods=["GET"]),
        Route("/animals", create_animal, methods=["POST"]),
        Route("/animals/search", search_animals, methods=["POST"]),
        Route("/animals/secret", secret_animal, methods=["GET"]),
        Route("/animals/{animal_id}", get_animal, methods=["GET"]),
        Route("/animals/{animal_id}/tricks", get_tricks, methods=["GET"]),
    ]
)


# ---------------------------------------------------------------------------
# OpenAPI 3.0 spec — hand-crafted with complex polymorphic schemas
# ---------------------------------------------------------------------------

ANIMAL_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Animal Shelter API", "version": "1.0.0"},
    "paths": {
        "/animals": {
            "get": {
                "operationId": "list_animals",
                "summary": "List all animals",
                "responses": {
                    "200": {
                        "description": "List of animals",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Animal"},
                                }
                            }
                        },
                    }
                },
            },
            "post": {
                "operationId": "create_animal",
                "summary": "Create a new animal",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Animal"}}
                    },
                },
                "responses": {
                    "201": {
                        "description": "Created animal",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Animal"}}
                        },
                    },
                    "400": {
                        "description": "Bad request",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                        },
                    },
                },
            },
        },
        "/animals/search": {
            "post": {
                "operationId": "search_animals",
                "summary": "Search animals with complex filters",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/SearchQuery"}}
                    },
                },
                "responses": {
                    "200": {
                        "description": "Matching animals",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Animal"},
                                }
                            }
                        },
                    }
                },
            }
        },
        "/animals/secret": {
            "get": {
                "operationId": "get_secret_info",
                "summary": "Secret endpoint — requires X-API-Key header injected via extra_headers",
                "responses": {
                    "200": {
                        "description": "Secret info",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"secret": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "401": {
                        "description": "Unauthorized",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                        },
                    },
                },
            }
        },
        "/animals/{animal_id}": {
            "get": {
                "operationId": "get_animal",
                "summary": "Get one animal by ID",
                "parameters": [
                    {
                        "name": "animal_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "The animal",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Animal"}}
                        },
                    },
                    "404": {
                        "description": "Not found",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                        },
                    },
                },
            }
        },
        "/animals/{animal_id}/tricks": {
            "get": {
                "operationId": "get_animal_tricks",
                "summary": "Get tricks for an animal",
                "parameters": [
                    {
                        "name": "animal_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "List of tricks",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Trick"},
                                }
                            }
                        },
                    },
                    "404": {
                        "description": "Not found",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                        },
                    },
                },
            }
        },
    },
    "components": {
        "schemas": {
            "BaseAnimal": {
                "type": "object",
                "required": ["type", "name"],
                "properties": {
                    "id": {"type": "integer", "description": "Auto-assigned ID"},
                    "type": {
                        "type": "string",
                        "enum": ["cat", "dog"],
                        "description": "Discriminator field",
                    },
                    "name": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "metadata": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Arbitrary key-value metadata",
                    },
                },
            },
            "Cat": {
                "allOf": [
                    {"$ref": "#/components/schemas/BaseAnimal"},
                    {
                        "type": "object",
                        "properties": {
                            "indoor": {"type": "boolean", "default": True},
                            "whisker_count": {
                                "type": "integer",
                                "nullable": True,
                                "description": "Null if not yet measured",
                            },
                        },
                    },
                ]
            },
            "Dog": {
                "allOf": [
                    {"$ref": "#/components/schemas/BaseAnimal"},
                    {
                        "type": "object",
                        "properties": {
                            "breed": {"type": "string"},
                            "trained": {"type": "boolean", "default": False},
                        },
                    },
                ]
            },
            "Animal": {
                "oneOf": [
                    {"$ref": "#/components/schemas/Cat"},
                    {"$ref": "#/components/schemas/Dog"},
                ],
                "discriminator": {
                    "propertyName": "type",
                    "mapping": {
                        "cat": "#/components/schemas/Cat",
                        "dog": "#/components/schemas/Dog",
                    },
                },
            },
            "Trick": {
                "type": "object",
                "required": ["name", "difficulty"],
                "properties": {
                    "name": {"type": "string"},
                    "difficulty": {
                        "type": "string",
                        "enum": ["easy", "medium", "hard"],
                    },
                    "success_rate": {
                        "type": "number",
                        "format": "float",
                        "nullable": True,
                        "description": "Null if not yet measured",
                    },
                },
            },
            "SearchQuery": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Name substring to match"},
                    "filters": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Arbitrary field filters",
                    },
                    "sort_by": {"type": "string", "nullable": True},
                    "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000},
                },
            },
            "Error": {
                "type": "object",
                "required": ["code", "message"],
                "properties": {
                    "code": {"type": "integer"},
                    "message": {"type": "string"},
                    "details": {
                        "type": "object",
                        "nullable": True,
                        "additionalProperties": True,
                    },
                },
            },
        }
    },
}
