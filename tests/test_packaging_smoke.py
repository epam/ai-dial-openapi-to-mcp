"""Package build and clean-install smoke tests.

Marked `integration`: these build real sdist/wheel artifacts, install the
wheel over the active environment (no-deps, so it reuses already-resolved
dependencies instead of hitting the network), and restore the editable dev
install afterward. Excluded from `make test` by default.
"""

import shutil
import socket
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent


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
            time.sleep(0.2)
    raise RuntimeError(f"No TCP listener on port {port} after {timeout}s")


def _poetry() -> str:
    poetry = shutil.which("poetry")
    assert poetry, "poetry executable not found on PATH"
    return poetry


@pytest.fixture(scope="module")
def built_artifacts(tmp_path_factory) -> Path:
    dist_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [_poetry(), "build", "-o", str(dist_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return dist_dir


def _wheel_and_sdist(dist_dir: Path) -> tuple[Path, Path]:
    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))
    assert len(wheels) == 1, f"Expected exactly one wheel, found: {wheels}"
    assert len(sdists) == 1, f"Expected exactly one sdist, found: {sdists}"
    return wheels[0], sdists[0]


class TestBuiltArtifactContents:
    def test_wheel_contains_package_data_and_entry_point(self, built_artifacts: Path):
        wheel, _ = _wheel_and_sdist(built_artifacts)
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert "dial_openapi_to_mcp/application_schema.json" in names
            assert "dial_openapi_to_mcp/py.typed" in names
            assert "dial_openapi_to_mcp/server.py" in names

            entry_points_name = next(n for n in names if n.endswith("entry_points.txt"))
            entry_points = archive.read(entry_points_name).decode()
            assert "openapi-to-mcp" in entry_points
            assert "dial_openapi_to_mcp.__main__:main" in entry_points

    def test_sdist_includes_linked_top_level_docs(self, built_artifacts: Path):
        _, sdist = _wheel_and_sdist(built_artifacts)
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
            for doc in (
                "README.md",
                "CONFIGURATION.md",
                "CHANGELOG.md",
                "CONTRIBUTING.md",
                "SECURITY.md",
                "LICENSE",
            ):
                assert any(name.endswith(f"/{doc}") for name in names), f"{doc} missing from sdist"
            assert any(
                name.endswith("/dial_openapi_to_mcp/application_schema.json") for name in names
            )


class TestInstalledWheelSmoke:
    @pytest.fixture
    def installed_wheel(self, built_artifacts: Path):
        """Install the built wheel over the active venv (no-deps) and restore
        the editable dev install afterward."""
        wheel, _ = _wheel_and_sdist(built_artifacts)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            yield
        finally:
            subprocess.run(
                [_poetry(), "install", "--with", "dev"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_import_and_packaged_schema(self, installed_wheel):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from dial_openapi_to_mcp.server import APPLICATION_SCHEMA;"
                "assert isinstance(APPLICATION_SCHEMA, dict) and APPLICATION_SCHEMA",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_console_entry_point_starts_and_serves(self, installed_wheel):
        port = _free_port()
        script = shutil.which("openapi-to-mcp")
        assert script, "openapi-to-mcp console script not found on PATH after install"
        process = subprocess.Popen(
            [script],
            env={"MCP_PORT": str(port), **__import__("os").environ},
        )
        try:
            _wait_for_tcp(port)
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_module_entry_point_starts_and_serves(self, installed_wheel):
        import os

        port = _free_port()
        process = subprocess.Popen(
            [sys.executable, "-m", "dial_openapi_to_mcp"],
            env={**os.environ, "MCP_PORT": str(port)},
        )
        try:
            _wait_for_tcp(port)
        finally:
            process.terminate()
            process.wait(timeout=10)
