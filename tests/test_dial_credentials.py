"""Tests for _resolve_dial_credentials misconfiguration handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dial_openapi_to_mcp.server import (
    DialCredentialsError,
    _fetch_dial_credentials,
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
async def test_service_not_in_registry_status_code_propagates(monkeypatch):
    """_fetch_dial_credentials raises 404 directly; _resolve_dial_credentials must not
    swallow the status_code/www_authenticate by re-wrapping it into a generic error."""
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    async def _not_in_registry(*args, **kwargs):
        raise DialCredentialsError(
            "External service 'sample-external-service' not found (status_code=404)",
            "sample-external-service",
            status_code=404,
        )

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _not_in_registry)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError) as exc_info:
        await _resolve_dial_credentials(request)
    assert exc_info.value.status_code == 404
    assert exc_info.value.www_authenticate is None


@pytest.mark.asyncio
async def test_credentials_not_yet_stored_raises_401_with_challenge(monkeypatch):
    """DIAL core has no stored credential for a configured external service: the user
    needs to log in, and the challenge is exposed via www_authenticate."""
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")

    expected_www_authenticate = (
        'DIAL-External-Service url="applications/bucket/app/external_services/'
        'sample-external-service", method="external-service/signin"'
    )

    async def _needs_login(*args, **kwargs):
        raise DialCredentialsError(
            "No stored credential for external service 'sample-external-service'; "
            "user login required (status_code=401)",
            "sample-external-service",
            status_code=401,
            www_authenticate=expected_www_authenticate,
        )

    monkeypatch.setattr("dial_openapi_to_mcp.server._fetch_dial_credentials", _needs_login)

    request = _make_request(
        _dial_body(),
        headers={"x-dial-application-id": "applications/bucket/app", "api-key": "secret"},
    )
    with pytest.raises(DialCredentialsError) as exc_info:
        await _resolve_dial_credentials(request)
    assert exc_info.value.status_code == 401
    assert exc_info.value.www_authenticate == expected_www_authenticate


@pytest.mark.asyncio
async def test_fetch_dial_credentials_404_for_service_not_in_registry():
    """Real _fetch_dial_credentials: application has no such external service configured."""

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/applications/bucket/app"
        return httpx.Response(200, json={"external_services": {}})

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient
    with patch(
        "dial_openapi_to_mcp.server.httpx.AsyncClient",
        lambda **kwargs: real_async_client(**{**kwargs, "transport": transport}),
    ):
        with pytest.raises(DialCredentialsError) as exc_info:
            await _fetch_dial_credentials(
                "https://dial.example.com",
                "applications/bucket/app",
                "sample-external-service",
                "secret",
            )
    assert exc_info.value.status_code == 404
    assert exc_info.value.www_authenticate is None


@pytest.mark.asyncio
async def test_fetch_dial_credentials_401_when_no_stored_credential():
    """Real _fetch_dial_credentials: service is configured but DIAL core returns 404
    from the credentials endpoint because the user hasn't logged in yet."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/applications/bucket/app":
            return httpx.Response(200, json={"external_services": {"sample-external-service": {}}})
        assert request.url.path == "/v1/ops/external-service/credentials"
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient
    with patch(
        "dial_openapi_to_mcp.server.httpx.AsyncClient",
        lambda **kwargs: real_async_client(**{**kwargs, "transport": transport}),
    ):
        with pytest.raises(DialCredentialsError) as exc_info:
            await _fetch_dial_credentials(
                "https://dial.example.com",
                "applications/bucket/app",
                "sample-external-service",
                "secret",
            )
    assert exc_info.value.status_code == 401
    assert exc_info.value.www_authenticate == (
        'DIAL-External-Service url="applications/bucket/app/external_services/'
        'sample-external-service", method="external-service/signin"'
    )


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
