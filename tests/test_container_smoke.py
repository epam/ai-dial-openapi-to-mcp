"""Container runtime smoke tests.

Marked `integration`: these build and run the real Docker image. Requires a
working Docker daemon. Excluded from `make test` by default.
"""

import json
import socket
import subprocess
import time
import uuid
from typing import Iterator

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

pytestmark = pytest.mark.integration

IMAGE_TAG = "ai-dial-openapi-to-mcp:smoketest"
HEALTH_PATH = "/v1/configuration-support/application-schema"

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Smoke Test API", "version": "1.0.0"},
    "paths": {
        "/todos": {
            "get": {
                "operationId": "listTodos",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def _docker() -> None:
    result = subprocess.run(["docker", "version"], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip("Docker daemon is not available")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}{HEALTH_PATH}", timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as e:
            last_error = e
        time.sleep(0.5)
    raise RuntimeError(f"Container did not become healthy in {timeout}s: {last_error}")


@pytest.fixture(scope="module")
def docker_image() -> Iterator[str]:
    _docker()
    repo_root = __import__("pathlib").Path(__file__).resolve().parent.parent
    subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, "."],
        cwd=repo_root,
        check=True,
    )
    yield IMAGE_TAG


class _RunningContainer:
    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url


def _run_container(image: str, host_port: int, extra_env: dict | None = None) -> _RunningContainer:
    name = f"openapi-to-mcp-smoketest-{uuid.uuid4().hex[:8]}"
    cmd = ["docker", "run", "-d", "--name", name, "-p", f"{host_port}:8080"]
    for key, value in (extra_env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd.append(image)
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    base_url = f"http://127.0.0.1:{host_port}"
    _wait_for_health(base_url)
    return _RunningContainer(name, base_url)


def _stop_container(container: _RunningContainer) -> float:
    start = time.monotonic()
    subprocess.run(
        ["docker", "stop", "--time", "10", container.name], check=True, capture_output=True
    )
    elapsed = time.monotonic() - start
    subprocess.run(["docker", "rm", "-f", container.name], capture_output=True)
    return elapsed


class TestContainerHealthAndPorts:
    def test_default_internal_port_is_healthy(self, docker_image: str):
        host_port = _free_port()
        container = _run_container(docker_image, host_port)
        try:
            resp = httpx.get(f"{container.base_url}{HEALTH_PATH}", timeout=5.0)
            assert resp.status_code == 200
            assert "dial:applicationTypeDisplayName" in resp.text
        finally:
            _stop_container(container)

    def test_custom_host_port_maps_to_fixed_internal_port(self, docker_image: str):
        """docker-compose exposes HOST_PORT on the host while the container's
        internal listener stays fixed at 8080; verify that mapping works for
        an arbitrary, non-default host port."""
        host_port = _free_port()
        assert host_port != 8080
        container = _run_container(docker_image, host_port)
        try:
            resp = httpx.get(f"{container.base_url}{HEALTH_PATH}", timeout=5.0)
            assert resp.status_code == 200
        finally:
            _stop_container(container)

    def test_container_stops_gracefully(self, docker_image: str):
        host_port = _free_port()
        container = _run_container(docker_image, host_port)
        elapsed = _stop_container(container)
        # A clean SIGTERM shutdown finishes well inside the 10s grace period;
        # hitting the timeout would mean docker escalated to SIGKILL.
        assert elapsed < 9.0, f"Container took {elapsed:.1f}s to stop (likely SIGKILLed)"


class TestContainerMcpFlow:
    async def test_full_mcp_init_list_call_flow(self, docker_image: str):
        host_port = _free_port()
        container = _run_container(docker_image, host_port)
        try:
            transport = StreamableHttpTransport(
                url=f"{container.base_url}/mcp",
                headers={"X-META": json.dumps(MINIMAL_SPEC)},
            )
            async with Client(transport) as client:
                tools = await client.list_tools()
                names = [t.name for t in tools]
                assert any("listTodos" in n for n in names)
        finally:
            _stop_container(container)
