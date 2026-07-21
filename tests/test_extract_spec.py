"""Tests for _extract_spec_from_request."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from dial_openapi_to_mcp.server import _extract_spec_from_request
from helpers import MINIMAL_OPENAPI_30


def _make_request(body: dict, headers: dict = None):
    mock = MagicMock()
    mock.headers = {k.lower(): v for k, v in (headers or {}).items()}
    mock.json = AsyncMock(return_value=body)
    return mock


SPEC_JSON = json.dumps(MINIMAL_OPENAPI_30)


@pytest.mark.asyncio
async def test_extract_from_x_meta_and_x_base_url_headers():
    request = _make_request(
        {}, headers={"X-META": SPEC_JSON, "X-BASE-URL": "https://api.example.com"}
    )
    spec, base_url = await _extract_spec_from_request(request)
    assert spec == SPEC_JSON
    assert base_url == "https://api.example.com"


@pytest.mark.asyncio
async def test_extract_spec_from_meta_openapi_dict():
    request = _make_request(
        {
            "params": {
                "_meta": {"openapi": MINIMAL_OPENAPI_30, "base_url": "https://api.example.com"}
            }
        }
    )
    spec, base_url = await _extract_spec_from_request(request)
    assert spec is not None
    assert json.loads(spec)["openapi"] == "3.0.0"
    assert base_url == "https://api.example.com"


@pytest.mark.asyncio
async def test_extract_spec_from_meta_openapi_json_string():
    request = _make_request({"params": {"_meta": {"openapi": SPEC_JSON}}})
    spec, base_url = await _extract_spec_from_request(request)
    assert spec == SPEC_JSON


@pytest.mark.asyncio
async def test_extract_spec_from_ai_dial_config():
    request = _make_request(
        {
            "params": {
                "_meta": {
                    "ai_dial_config": {
                        "openapi": MINIMAL_OPENAPI_30,
                        "base_url": "https://dial.example.com",
                    }
                }
            }
        }
    )
    spec, base_url = await _extract_spec_from_request(request)
    assert spec is not None
    assert base_url == "https://dial.example.com"


@pytest.mark.asyncio
async def test_extract_base_url_variants():
    for key in ("base_url", "baseurl", "baseURL"):
        request = _make_request(
            {"params": {"_meta": {"openapi": MINIMAL_OPENAPI_30, key: "https://example.com"}}}
        )
        spec, base_url = await _extract_spec_from_request(request)
        assert base_url == "https://example.com", f"Failed for key: {key}"


@pytest.mark.asyncio
async def test_extract_returns_none_when_no_spec():
    request = _make_request({"params": {}})
    spec, base_url = await _extract_spec_from_request(request)
    assert spec is None
    assert base_url is None


@pytest.mark.asyncio
async def test_x_meta_header_takes_priority_over_body():
    request = _make_request(
        body={
            "params": {
                "_meta": {"openapi": {"openapi": "3.0.0", "info": {"title": "Body"}, "paths": {}}}
            }
        },
        headers={"X-META": SPEC_JSON, "X-BASE-URL": "https://header.example.com"},
    )
    spec, base_url = await _extract_spec_from_request(request)
    assert spec == SPEC_JSON
    assert base_url == "https://header.example.com"
