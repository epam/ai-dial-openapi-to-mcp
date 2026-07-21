"""
Main entry point for running the OpenAPI to MCP server
"""


def main() -> None:
    import os

    from .server import mcp

    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
