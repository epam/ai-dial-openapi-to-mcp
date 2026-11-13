"""Swagger 2.0 -> OpenAPI 3.0 conversion details."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastmcp import FastMCP

from dial_openapi_to_mcp.server import _mcp_cache, get_or_create_mcp
from dial_openapi_to_mcp.swagger2 import convert_swagger2_to_openapi30

PETSTORE = yaml.safe_load(
    """
swagger: "2.0"
info: {title: Petstore, version: "1"}
host: pets.example.com
basePath: v2
schemes: [https]
consumes: [application/json]
produces: [application/json]
parameters:
  limitParam:
    name: limit
    in: query
    type: integer
    maximum: 100
  petBody:
    name: pet
    in: body
    required: true
    schema: {$ref: "#/definitions/Pet"}
responses:
  NotFound:
    description: not found
    schema: {$ref: "#/definitions/Error"}
securityDefinitions:
  basic_auth: {type: basic}
  key: {type: apiKey, in: header, name: X-Key}
  oauth:
    type: oauth2
    flow: accessCode
    authorizationUrl: https://auth.example.com/authorize
    tokenUrl: https://auth.example.com/token
    scopes: {read: Read}
definitions:
  Pet:
    type: object
    discriminator: kind
    properties:
      kind: {type: string}
      photo: {type: file}
      tags: {type: array, items: {type: string}}
  Error:
    type: object
    properties:
      message: {type: string, x-nullable: true}
paths:
  /pets:
    parameters:
      - {name: X-Trace, in: header, type: string}
      - {$ref: "#/parameters/limitParam"}
    get:
      operationId: listPets
      parameters:
        - {name: tags, in: query, type: array, items: {type: string}, collectionFormat: multi}
        - {name: ids, in: query, type: array, items: {type: integer}}
        - {name: limit, in: query, type: integer, maximum: 10}
      responses:
        200:
          description: ok
          headers:
            X-Rate: {type: integer, description: rate}
          schema: {type: array, items: {$ref: "#/definitions/Pet"}}
          examples: {application/json: []}
        404: {$ref: "#/responses/NotFound"}
    post:
      operationId: createPet
      parameters:
        - {$ref: "#/parameters/petBody"}
      responses:
        201: {description: created}
  /pets/{petId}:
    get:
      operationId: getPet
      parameters:
        - {name: petId, in: path, type: string}
      responses:
        200: {description: ok, schema: {$ref: "#/definitions/Pet"}}
  /upload:
    post:
      operationId: upload
      consumes: [multipart/form-data]
      parameters:
        - {name: file, in: formData, type: file, required: true}
        - {name: note, in: formData, type: string}
      responses:
        200: {description: ok}
"""
)


@pytest.fixture
def converted():
    return convert_swagger2_to_openapi30(PETSTORE)


def test_input_not_mutated():
    before = json.dumps(PETSTORE, sort_keys=True, default=str)
    convert_swagger2_to_openapi30(PETSTORE)
    assert json.dumps(PETSTORE, sort_keys=True, default=str) == before


def test_server_url(converted):
    assert converted["servers"] == [{"url": "https://pets.example.com/v2"}]


def test_no_host_leaves_servers_empty():
    spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}, "paths": {}}
    assert convert_swagger2_to_openapi30(spec)["servers"] == []


def test_query_param_gets_schema(converted):
    params = {p["name"]: p for p in converted["paths"]["/pets"]["get"]["parameters"]}
    assert params["ids"]["schema"] == {"type": "array", "items": {"type": "integer"}}
    assert params["ids"]["style"] == "form" and params["ids"]["explode"] is False
    assert params["tags"]["explode"] is True
    assert "type" not in params["tags"]


def test_path_level_parameters_merged_and_overridden(converted):
    params = {p["name"]: p for p in converted["paths"]["/pets"]["get"]["parameters"]}
    assert params["X-Trace"]["schema"] == {"type": "string"}
    assert params["limit"]["schema"]["maximum"] == 10  # operation overrides $ref'd path param
    assert "parameters" not in converted["paths"]["/pets"]


def test_path_param_is_required(converted):
    param = converted["paths"]["/pets/{petId}"]["get"]["parameters"][0]
    assert param["required"] is True and param["schema"] == {"type": "string"}


def test_body_param_ref_inlined(converted):
    body = converted["paths"]["/pets"]["post"]["requestBody"]
    assert body["required"] is True
    assert body["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Pet"}


def test_formdata_file_is_multipart_binary(converted):
    body = converted["paths"]["/upload"]["post"]["requestBody"]
    schema = body["content"]["multipart/form-data"]["schema"]
    assert schema["properties"]["file"] == {"type": "string", "format": "binary"}
    assert schema["required"] == ["file"]


def test_response_ref_headers_and_examples(converted):
    responses = converted["paths"]["/pets"]["get"]["responses"]
    assert responses["404"] == {"$ref": "#/components/responses/NotFound"}
    assert converted["components"]["responses"]["NotFound"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/Error"}
    ok = responses["200"]
    assert ok["headers"]["X-Rate"]["schema"] == {"type": "integer"}
    assert ok["content"]["application/json"]["example"] == []


def test_schema_quirks_fixed(converted):
    pet = converted["components"]["schemas"]["Pet"]
    assert pet["discriminator"] == {"propertyName": "kind"}
    assert pet["properties"]["photo"] == {"type": "string", "format": "binary"}
    message = converted["components"]["schemas"]["Error"]["properties"]["message"]
    assert message["nullable"] is True and "x-nullable" not in message


def test_security_schemes(converted):
    schemes = converted["components"]["securitySchemes"]
    assert schemes["basic_auth"] == {"type": "http", "scheme": "basic"}
    assert schemes["key"]["type"] == "apiKey"
    flow = schemes["oauth"]["flows"]["authorizationCode"]
    assert flow["tokenUrl"] == "https://auth.example.com/token"
    assert flow["scopes"] == {"read": "Read"}


@pytest.mark.asyncio
async def test_converted_spec_builds_tools(converted):
    from httpx import AsyncClient

    async with AsyncClient() as client:
        mcp = FastMCP.from_openapi(openapi_spec=converted, client=client, name="petstore")
        names = {tool.name for tool in await mcp.list_tools()}
    assert {"listPets", "createPet", "getPet", "upload"} <= names


@pytest.mark.asyncio
async def test_get_or_create_mcp_accepts_swagger_yaml():
    await _mcp_cache.clear()
    request = MagicMock()
    request.headers = {}
    request.json = AsyncMock(return_value={"params": {}})

    entry = await get_or_create_mcp(yaml.safe_dump(PETSTORE), request)

    assert entry is not None
    assert "listPets" in entry.tools
    await _mcp_cache.clear()


def test_unresolved_parameter_ref_is_dropped_with_warning(caplog):
    spec = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "operationId": "getX",
                    "parameters": [
                        {"$ref": "#/parameters/missing"},
                        {"name": "q", "in": "query", "type": "string"},
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    with caplog.at_level("WARNING", logger="dial_openapi_to_mcp.swagger2"):
        converted = convert_swagger2_to_openapi30(spec)

    assert [p["name"] for p in converted["paths"]["/x"]["get"]["parameters"]] == ["q"]
    assert "#/parameters/missing" in caplog.text
