"""
Real end-to-end integration tests.

Stack:
  FastMCP.Client
      ↓  MCP protocol (JSON-RPC / streamable-http)
  ai-dial-openapi-to-mcp  ← X-META header carries the OpenAPI spec
      ↓  httpx  (X-BASE-URL header sets the backend base URL)
  Animal Shelter test API  (Starlette + uvicorn)

Both servers run in background daemon threads so they don't interfere
with the pytest-asyncio event loop used by test coroutines.
"""

import json
import os
import socket
import threading
import time
from typing import Iterator

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from complex_api_server import ANIMAL_OPENAPI_SPEC, animal_api_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_tcp(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"No TCP listener on port {port} after {timeout}s")


def _start_server_thread(app, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_tcp(port)
    time.sleep(0.4)  # let the app finish its lifespan startup
    return server, thread


def _stop_server_thread(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=8)


def _to_plain(obj) -> object:
    """Recursively convert any value to a plain JSON-serializable Python type."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, list):
        return [_to_plain(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return _to_plain(obj.model_dump())
    # Pydantic v1 / dataclasses
    if hasattr(obj, "__dict__"):
        return _to_plain({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
    return obj


def _parse_result(result) -> object:
    """Normalize a FastMCP CallToolResult to a plain Python value."""
    # FastMCP 3.x returns a CallToolResult object
    if hasattr(result, "is_error") and hasattr(result, "data"):
        if result.is_error or result.data is None:
            # errors: extract text from content
            if result.content:
                text = getattr(result.content[0], "text", str(result.content[0]))
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return text
            return None
        return _to_plain(result.data)
    # Fallback for older list-of-TextContent style
    if isinstance(result, list) and result:
        text = getattr(result[0], "text", str(result[0]))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return result


# ---------------------------------------------------------------------------
# Module-scoped sync fixtures  (servers stay up for the whole test module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_server() -> Iterator[str]:
    port = _free_port()
    server, thread = _start_server_thread(animal_api_app, port)
    yield f"http://127.0.0.1:{port}"
    _stop_server_thread(server, thread)


@pytest.fixture(scope="module")
def bridge_server(api_server: str) -> Iterator[str]:
    from dial_openapi_to_mcp.server import mcp

    previous_settings = {name: os.environ.get(name) for name in ("OUTBOUND_HEADER_ALLOWLIST",)}
    os.environ.update({"OUTBOUND_HEADER_ALLOWLIST": "x-api-key"})
    port = _free_port()
    bridge_app = mcp.http_app(path="/mcp", transport="streamable-http")
    server, thread = _start_server_thread(bridge_app, port)
    yield f"http://127.0.0.1:{port}/mcp"
    _stop_server_thread(server, thread)
    for name, value in previous_settings.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# ---------------------------------------------------------------------------
# Function-scoped async fixtures  (fresh MCP session per test)
# ---------------------------------------------------------------------------


@pytest.fixture
async def mcp_client(bridge_server: str, api_server: str):
    transport = StreamableHttpTransport(
        url=bridge_server,
        headers={
            "X-META": json.dumps(ANIMAL_OPENAPI_SPEC),
            "X-BASE-URL": api_server,
        },
    )
    async with Client(transport) as client:
        yield client


@pytest.fixture
async def mcp_client_with_api_key(bridge_server: str, api_server: str):
    transport = StreamableHttpTransport(
        url=bridge_server,
        headers={
            "X-META": json.dumps(ANIMAL_OPENAPI_SPEC),
            "X-BASE-URL": api_server,
            "X-EXTRA-HEADERS": json.dumps([{"name": "X-API-Key", "value": "test-secret"}]),
        },
    )
    async with Client(transport) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListTools:
    async def test_list_tools_reflects_all_paths(self, mcp_client: Client):
        tools = await mcp_client.list_tools()
        names = [t.name for t in tools]
        assert len(names) >= 6, f"Expected ≥6 tools, got {len(names)}: {names}"
        assert any("list_animals" in n for n in names)
        assert any("create_animal" in n for n in names)
        assert any("get_animal" in n for n in names)
        assert any("tricks" in n or "get_animal_tricks" in n for n in names)
        assert any("search" in n for n in names)


class TestAnimalCRUD:
    async def test_list_animals_returns_seeded_data(self, mcp_client: Client):
        result = await mcp_client.call_tool("list_animals", {})
        data = _parse_result(result)
        assert isinstance(data, list), f"Expected list, got {type(data)}: {data}"
        assert len(data) >= 3

    async def test_list_animals_contains_cat_and_dog(self, mcp_client: Client):
        result = await mcp_client.call_tool("list_animals", {})
        data = _parse_result(result)
        types = {a["type"] for a in data}
        assert "cat" in types
        assert "dog" in types

    async def test_get_cat_by_id(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal", {"animal_id": 1})
        data = _parse_result(result)
        assert isinstance(data, dict), f"Expected dict: {data}"
        assert data["type"] == "cat"
        assert data["name"] == "Whiskers"
        assert isinstance(data["indoor"], bool)

    async def test_get_dog_by_id(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal", {"animal_id": 2})
        data = _parse_result(result)
        assert data["type"] == "dog"
        assert data["name"] == "Rex"
        assert "breed" in data

    async def test_get_cat_with_nullable_whisker_count(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal", {"animal_id": 3})
        data = _parse_result(result)
        assert data["type"] == "cat"
        assert data["whisker_count"] is None

    async def test_get_animal_with_array_tags(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal", {"animal_id": 1})
        data = _parse_result(result)
        assert isinstance(data["tags"], list)
        assert len(data["tags"]) > 0

    async def test_get_animal_with_metadata_additionalproperties(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal", {"animal_id": 1})
        data = _parse_result(result)
        assert isinstance(data["metadata"], dict)
        assert "color" in data["metadata"]

    async def test_get_missing_animal_propagates_error(self, mcp_client: Client):
        with pytest.raises(Exception, match="404"):
            await mcp_client.call_tool("get_animal", {"animal_id": 9999})

    async def test_create_cat(self, mcp_client: Client):
        body = {
            "type": "cat",
            "name": "Luna",
            "tags": ["fluffy"],
            "metadata": {"color": "white"},
            "indoor": True,
            "whisker_count": 20,
        }
        result = await mcp_client.call_tool("create_animal", {"body": body})
        data = _parse_result(result)
        assert isinstance(data, dict), f"Expected dict: {data}"
        assert data.get("type") == "cat" or data.get("name") == "Luna" or "Luna" in str(data)


class TestTricks:
    async def test_get_tricks_returns_array(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal_tricks", {"animal_id": 2})
        data = _parse_result(result)
        assert isinstance(data, list)
        assert len(data) == 3

    async def test_tricks_have_enum_difficulty(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal_tricks", {"animal_id": 2})
        data = _parse_result(result)
        for trick in data:
            assert trick["difficulty"] in ("easy", "medium", "hard")

    async def test_tricks_nullable_success_rate(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal_tricks", {"animal_id": 1})
        data = _parse_result(result)
        rates = [t["success_rate"] for t in data]
        assert None in rates, "Expected at least one nullable success_rate"

    async def test_cat_with_no_tricks_returns_empty_list(self, mcp_client: Client):
        result = await mcp_client.call_tool("get_animal_tricks", {"animal_id": 3})
        data = _parse_result(result)
        assert data == []


class TestSearch:
    async def test_search_by_name(self, mcp_client: Client):
        result = await mcp_client.call_tool(
            "search_animals", {"query": "rex", "filters": {}, "limit": 10}
        )
        data = _parse_result(result)
        assert isinstance(data, list), f"Expected list: {data}"
        assert len(data) == 1
        assert data[0]["name"] == "Rex"

    async def test_search_with_type_filter(self, mcp_client: Client):
        result = await mcp_client.call_tool(
            "search_animals", {"query": "", "filters": {"type": "cat"}, "limit": 100}
        )
        data = _parse_result(result)
        assert isinstance(data, list)
        assert all(a["type"] == "cat" for a in data)

    async def test_search_no_match_returns_empty(self, mcp_client: Client):
        result = await mcp_client.call_tool(
            "search_animals", {"query": "xyznosuchname", "filters": {}}
        )
        data = _parse_result(result)
        assert data == [] or data == {}, f"Expected empty, got: {data}"

    async def test_search_with_limit(self, mcp_client: Client):
        result = await mcp_client.call_tool(
            "search_animals", {"query": "", "filters": {}, "limit": 1}
        )
        data = _parse_result(result)
        assert isinstance(data, list)
        assert len(data) == 1


class TestExtraHeaders:
    async def test_secret_endpoint_without_api_key_returns_error(self, mcp_client: Client):
        with pytest.raises(Exception, match="401"):
            await mcp_client.call_tool("get_secret_info", {})

    async def test_extra_headers_forwarded_to_backend(self, mcp_client_with_api_key: Client):
        result = await mcp_client_with_api_key.call_tool("get_secret_info", {})
        data = _parse_result(result)
        assert isinstance(data, dict), f"Expected dict with secret, got: {data}"
        assert "secret" in data or "animals" in str(data)


class TestDialCredentialsMisconfig:
    async def test_external_service_without_dial_config_returns_error_result(
        self, mcp_client: Client
    ):
        """external_service requested but no x-dial-application-id/DIAL_URL/api-key set
        must fail the call cleanly instead of hitting the backend without auth."""
        result = await mcp_client.call_tool(
            "list_animals",
            {},
            meta={"ai_dial_config": {"external_service": "sample-external-service"}},
            raise_on_error=False,
        )
        assert result.is_error
        text = result.content[0].text
        assert "external_service" in text
        assert "x-dial-application-id" in text
