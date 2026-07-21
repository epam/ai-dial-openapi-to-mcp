"""OpenAPI to MCP — a secure OpenAPI-to-MCP bridge."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ai-dial-openapi-to-mcp")
except PackageNotFoundError:
    __version__ = "0.1.0"

__author__ = "EPAM Systems"

from .cache import CacheEntry, MCPCache
from .server import OpenAPI2MCPBridge

__all__ = [
    "MCPCache",
    "CacheEntry",
    "OpenAPI2MCPBridge",
]
