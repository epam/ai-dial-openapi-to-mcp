"""Swagger 2.0 -> OpenAPI 3.0 conversion.

FastMCP only understands OpenAPI 3.x, so Swagger 2.0 documents are converted before
the MCP definition is built. The conversion covers the parts that affect tool
generation: servers, parameters, request bodies, responses, schemas and security.
"""

import copy
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options", "head", "trace")
_FORM_MEDIA_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")
_PARAM_SCHEMA_KEYS = (
    "type",
    "format",
    "items",
    "enum",
    "default",
    "maximum",
    "exclusiveMaximum",
    "minimum",
    "exclusiveMinimum",
    "maxLength",
    "minLength",
    "pattern",
    "maxItems",
    "minItems",
    "uniqueItems",
    "multipleOf",
)
_PARAM_KEEP_KEYS = ("name", "in", "description", "required", "allowEmptyValue", "deprecated")
_OAUTH_FLOW_NAMES = {
    "implicit": "implicit",
    "password": "password",
    "application": "clientCredentials",
    "accessCode": "authorizationCode",
}
_REF_PREFIXES = {
    "#/definitions/": "#/components/schemas/",
    "#/responses/": "#/components/responses/",
}


def rewrite_refs(obj: Any, mapping: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {
            k: (
                mapping[v]
                if k == "$ref" and isinstance(v, str) and v in mapping
                else rewrite_refs(v, mapping)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [rewrite_refs(item, mapping) for item in obj]
    return obj


def _collect_ref_mapping(obj: Any, mapping: Dict[str, str]) -> None:
    """Map every Swagger 2 local $ref in the document to its OpenAPI 3 location."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "$ref" and isinstance(value, str):
                for old, new in _REF_PREFIXES.items():
                    if value.startswith(old):
                        mapping[value] = new + value[len(old) :]
            else:
                _collect_ref_mapping(value, mapping)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ref_mapping(item, mapping)


def _fix_schema(schema: Any) -> Any:
    """Adjust Swagger 2 schema-object quirks that OpenAPI 3.0 does not accept."""
    if not isinstance(schema, dict):
        return schema
    result = dict(schema)
    if result.get("type") == "file":
        result["type"] = "string"
        result["format"] = "binary"
    if isinstance(result.get("discriminator"), str):
        result["discriminator"] = {"propertyName": result["discriminator"]}
    if result.pop("x-nullable", None) is True:
        result["nullable"] = True
    if isinstance(result.get("properties"), dict):
        result["properties"] = {k: _fix_schema(v) for k, v in result["properties"].items()}
    for key in ("items", "additionalProperties", "not"):
        if isinstance(result.get(key), dict):
            result[key] = _fix_schema(result[key])
    for key in ("allOf", "anyOf", "oneOf"):
        if isinstance(result.get(key), list):
            result[key] = [_fix_schema(item) for item in result[key]]
    return result


def _param_schema(param: Dict[str, Any]) -> Dict[str, Any]:
    schema = {k: copy.deepcopy(param[k]) for k in _PARAM_SCHEMA_KEYS if k in param}
    if schema.get("type") == "file":
        schema = {"type": "string", "format": "binary"}
    if isinstance(schema.get("items"), dict):
        items = {k: v for k, v in schema["items"].items() if k != "collectionFormat"}
        schema["items"] = _param_schema(items)
    return schema


def _convert_parameter(param: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a non-body, non-form Swagger 2 parameter to OpenAPI 3.0 form."""
    out: Dict[str, Any] = {k: v for k, v in param.items() if k in _PARAM_KEEP_KEYS}
    out.update({k: v for k, v in param.items() if k.startswith("x-")})
    schema = _param_schema(param) or {"type": "string"}
    out["schema"] = schema
    if param.get("in") == "path":
        out["required"] = True

    if schema.get("type") == "array" and param.get("in") in ("query", "header", "path"):
        collection = param.get("collectionFormat", "csv")
        if param["in"] == "query":
            if collection == "multi":
                out["style"] = "form"
                out["explode"] = True
            elif collection == "ssv":
                out["style"] = "spaceDelimited"
                out["explode"] = False
            elif collection == "pipes":
                out["style"] = "pipeDelimited"
                out["explode"] = False
            else:
                out["style"] = "form"
                out["explode"] = False
    return out


def _resolve_param(param: Any, top_level_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(param, dict):
        return None
    ref = param.get("$ref")
    if not isinstance(ref, str):
        return param
    if ref.startswith("#/parameters/"):
        resolved = top_level_params.get(ref[len("#/parameters/") :])
        if isinstance(resolved, dict):
            return copy.deepcopy(resolved)
    logger.warning("Skipping Swagger parameter with unresolved reference %r", ref)
    return None


def _convert_response(
    response: Any, produces: List[str], default_description: str = ""
) -> Dict[str, Any]:
    if not isinstance(response, dict) or "$ref" in response:
        return response if isinstance(response, dict) else {"description": default_description}
    new_resp = {k: v for k, v in response.items() if k not in ("schema", "examples", "headers")}
    new_resp.setdefault("description", default_description)

    if isinstance(response.get("headers"), dict):
        new_resp["headers"] = {
            name: {
                **{k: v for k, v in header.items() if k in ("description",)},
                "schema": _param_schema(header) or {"type": "string"},
            }
            for name, header in response["headers"].items()
            if isinstance(header, dict)
        }

    examples = response.get("examples") if isinstance(response.get("examples"), dict) else {}
    if "schema" in response:
        schema = _fix_schema(response["schema"])
        content = {mt: {"schema": schema} for mt in produces}
        for mt, example in examples.items():
            content.setdefault(mt, {"schema": schema})["example"] = example
        new_resp["content"] = content
    elif examples:
        new_resp["content"] = {mt: {"example": example} for mt, example in examples.items()}
    return new_resp


def _build_form_body(form_params: List[Dict[str, Any]], consumes: List[str]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required_fields: List[str] = []
    has_file = False
    for param in form_params:
        prop = _param_schema(param) or {"type": "string"}
        if param.get("type") == "file":
            has_file = True
        if param.get("description"):
            prop["description"] = param["description"]
        properties[param["name"]] = prop
        if param.get("required"):
            required_fields.append(param["name"])
    form_schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required_fields:
        form_schema["required"] = required_fields

    if has_file or "multipart/form-data" in consumes:
        media_type = "multipart/form-data"
    else:
        media_type = "application/x-www-form-urlencoded"
    return {"content": {media_type: {"schema": form_schema}}}


def _convert_operation(
    operation: Dict[str, Any],
    path_params: List[Dict[str, Any]],
    top_level_params: Dict[str, Any],
    global_consumes: List[str],
    global_produces: List[str],
) -> Dict[str, Any]:
    new_op = {k: v for k, v in operation.items() if k not in ("consumes", "produces", "parameters")}
    consumes = operation.get("consumes") or global_consumes
    produces = operation.get("produces") or global_produces

    # Operation-level parameters override path-level ones with the same (name, in).
    own: List[Dict[str, Any]] = []
    for raw in operation.get("parameters") or []:
        resolved = _resolve_param(raw, top_level_params)
        if resolved is not None:
            own.append(resolved)
    overridden = {(p.get("name"), p.get("in")) for p in own}
    merged = [p for p in path_params if (p.get("name"), p.get("in")) not in overridden] + own

    body_params = [p for p in merged if p.get("in") == "body"]
    form_params = [p for p in merged if p.get("in") == "formData"]
    other_params = [
        _convert_parameter(p) for p in merged if p.get("in") not in ("body", "formData")
    ]
    if other_params:
        new_op["parameters"] = other_params

    if body_params:
        body = body_params[0]
        schema = _fix_schema(body.get("schema", {}))
        body_media_types = [mt for mt in consumes if mt not in _FORM_MEDIA_TYPES] or [
            "application/json"
        ]
        request_body: Dict[str, Any] = {
            "content": {mt: {"schema": schema} for mt in body_media_types},
            "required": body.get("required", False),
        }
        if body.get("description"):
            request_body["description"] = body["description"]
        new_op["requestBody"] = request_body
    elif form_params:
        new_op["requestBody"] = _build_form_body(form_params, consumes)

    responses = {
        str(status): _convert_response(response, produces)
        for status, response in (operation.get("responses") or {}).items()
    }
    new_op["responses"] = (
        responses if responses else {"default": {"description": "No response schema defined"}}
    )
    return new_op


def _convert_security_definitions(definitions: Dict[str, Any]) -> Dict[str, Any]:
    schemes: Dict[str, Any] = {}
    for name, definition in definitions.items():
        if not isinstance(definition, dict):
            continue
        kind = definition.get("type")
        if kind == "basic":
            schemes[name] = {
                "type": "http",
                "scheme": "basic",
                **(
                    {"description": definition["description"]}
                    if "description" in definition
                    else {}
                ),
            }
        elif kind == "oauth2":
            flow_name = _OAUTH_FLOW_NAMES.get(definition.get("flow", ""), "implicit")
            flow: Dict[str, Any] = {"scopes": definition.get("scopes") or {}}
            for src, dst in (("authorizationUrl", "authorizationUrl"), ("tokenUrl", "tokenUrl")):
                if src in definition:
                    flow[dst] = definition[src]
            schemes[name] = {"type": "oauth2", "flows": {flow_name: flow}}
            if "description" in definition:
                schemes[name]["description"] = definition["description"]
        else:
            schemes[name] = definition
    return schemes


def _build_servers(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    host = spec.get("host")
    if not host:
        return []
    schemes = spec.get("schemes") or ["https"]
    base_path = spec.get("basePath", "/")
    if base_path and not base_path.startswith("/"):
        base_path = f"/{base_path}"
    return [{"url": f"{schemes[0]}://{host}{base_path}"}]


def convert_swagger2_to_openapi30(spec: Dict[str, Any]) -> Dict[str, Any]:
    spec = copy.deepcopy(spec)
    global_consumes = spec.get("consumes") or ["application/json"]
    global_produces = spec.get("produces") or ["application/json"]
    top_level_params = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}

    components: Dict[str, Any] = {}
    if isinstance(spec.get("definitions"), dict):
        components["schemas"] = {k: _fix_schema(v) for k, v in spec["definitions"].items()}
    if isinstance(spec.get("responses"), dict):
        components["responses"] = {
            k: _convert_response(v, global_produces) for k, v in spec["responses"].items()
        }
    if isinstance(spec.get("securityDefinitions"), dict):
        components["securitySchemes"] = _convert_security_definitions(spec["securityDefinitions"])

    new_paths: Dict[str, Any] = {}
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            new_paths[path] = path_item
            continue
        path_params = [
            resolved
            for resolved in (
                _resolve_param(p, top_level_params) for p in path_item.get("parameters") or []
            )
            if resolved is not None
        ]
        new_item: Dict[str, Any] = {}
        for key, value in path_item.items():
            if key == "parameters":
                continue
            if key in _HTTP_METHODS and isinstance(value, dict):
                new_item[key] = _convert_operation(
                    value, path_params, top_level_params, global_consumes, global_produces
                )
            else:
                new_item[key] = value
        new_paths[path] = new_item

    result: Dict[str, Any] = {
        "openapi": "3.0.0",
        "info": spec.get("info", {}),
        "servers": _build_servers(spec),
        "paths": new_paths,
    }
    if components:
        result["components"] = components
    for key in ("tags", "externalDocs", "security"):
        if key in spec:
            result[key] = spec[key]
    for key, value in spec.items():
        if key.startswith("x-"):
            result[key] = value

    ref_map: Dict[str, str] = {}
    _collect_ref_mapping(result, ref_map)
    return rewrite_refs(result, ref_map)
