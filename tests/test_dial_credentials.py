"""Tests for _resolve_dial_credentials misconfiguration handling."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from dial_openapi_to_mcp.server import (
    DialCredentialsError,
    _resolve_dial_credentials,
)


def _make_request(body: dict, headers: dict = None):
    mock = MagicMock()
    mock.headers = {k.lower(): v for k, v in (headers or {}).items()}
    mock.json = AsyncMock(return_value=body)
    return mock


def _dial_body(external_service: str = "sample-external-service"):
    return {
        "params": {
            "_meta": {
                "ai_dial_config": {
                    "external_service": external_service,
                }
            }
        }
    }


@pytest.mark.asyncio
async def test_no_external_service_returns_none():
    request = _make_request({"params": {"_meta": {}}})
    result = await _resolve_dial_credentials(request)
    assert result is None


@pytest.mark.asyncio
async def test_missing_application_header_raises(monkeypatch):
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
    request = _make_request(_dial_body(), headers={"api-key": "secret"})
    with pytest.raises(DialCredentialsError) as exc_info:
        await _resolve_dial_credentials(request)
    assert exc_info.value.external_service == "sample-external-service"


@pytest.mark.asyncio
async def test_missing_dial_url_raises(monkeypatch):
    monkeypatch.delenv("DIAL_URL", raising=False)
    monkeypatch.delenv("DIAL_CORE_URL", raising=False)
    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError):
        await _resolve_dial_credentials(request)


@pytest.mark.asyncio
async def test_missing_api_key_raises(monkeypatch):
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app"},
    )
    with pytest.raises(DialCredentialsError):
        await _resolve_dial_credentials(request)


@pytest.mark.asyncio
async def test_fetch_failure_raises(monkeypatch):
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _boom)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError):
        await _resolve_dial_credentials(request)


@pytest.mark.asyncio
async def test_fetch_http_status_error_message_is_concise(monkeypatch):
    """A 404/etc. from DIAL core should surface as a short message, not httpx's
    verbose text (which includes the full request URL and an MDN doc link)."""
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _not_found(*args, **kwargs):
        request = httpx.Request(
            "POST", "https://dial.example.com/v1/ops/external-service/credentials"
        )
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _not_found)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError) as exc_info:
        await _resolve_dial_credentials(request)
    message = str(exc_info.value)
    assert "404" in message
    assert "developer.mozilla.org" not in message
    assert "https://dial.example.com" not in message


@pytest.mark.asyncio
async def test_dial_unreachable_raises(monkeypatch):
    """DIAL_URL is set but the host can't be resolved/reached (DNS failure, connection refused)."""
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _unreachable(*args, **kwargs):
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _unreachable)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError):
        await _resolve_dial_credentials(request)


@pytest.mark.asyncio
async def test_dial_timeout_raises(monkeypatch):
    """DIAL_URL is set and resolves, but the request times out before reaching a response."""
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _timeout)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError):
        await _resolve_dial_credentials(request)


@pytest.mark.asyncio
async def test_service_not_in_registry_raises(monkeypatch):
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _not_found(*args, **kwargs):
        return None

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _not_found)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError):
        await _resolve_dial_credentials(request)


@pytest.mark.asyncio
async def test_successful_resolution_returns_credentials(monkeypatch):
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _ok(*args, **kwargs):
        return {"header_name": "X-Sample-Token", "header_value": "tok123", "expires_at": None}

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _ok)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    result = await _resolve_dial_credentials(request)
    assert result == {"header_name": "X-Sample-Token", "header_value": "tok123", "expires_at": None}
