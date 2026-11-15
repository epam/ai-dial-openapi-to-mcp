"""The DIAL application schema must accept the spec as an object or as JSON/YAML text."""

import json

import pytest
from jsonschema import Draft7Validator

from dial_openapi_to_mcp.server import APPLICATION_SCHEMA
from helpers import MINIMAL_OPENAPI_30

YAML_TEXT = "openapi: 3.0.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n"


def _errors(config):
    schema = {k: v for k, v in APPLICATION_SCHEMA.items() if k != "$schema"}
    return list(Draft7Validator(schema).iter_errors(config))


@pytest.mark.parametrize(
    "config",
    [
        {"openapi": MINIMAL_OPENAPI_30},
        {"openapi": YAML_TEXT},
        {"openapi": json.dumps(MINIMAL_OPENAPI_30)},
        {"openapi_url": "https://api.example.com/openapi.yaml"},
        {"openapi": YAML_TEXT, "base_url": "https://api.example.com"},
    ],
)
def test_valid_configs(config):
    assert _errors(config) == []


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"base_url": "https://api.example.com"},
        {"openapi": 42},
        {"openapi": ["not", "a", "spec"]},
    ],
)
def test_invalid_configs(config):
    assert _errors(config) != []
