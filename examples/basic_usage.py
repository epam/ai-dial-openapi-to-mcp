#!/usr/bin/env python3
"""Basic usage example for OpenAPI to MCP.

Demonstrates connecting with a FastMCP client and calling a generated tool
against a public API. Requires the running server's network egress control
to permit jsonplaceholder.typicode.com.
"""

import asyncio
import json

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

TODO_API_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "JSONPlaceholder TODO API", "version": "1.0.0"},
    "servers": [{"url": "https://jsonplaceholder.typicode.com"}],
    "paths": {
        "/todos": {
            "get": {
                "operationId": "listTodos",
                "summary": "List all todos",
                "responses": {"200": {"description": "Success"}},
            }
        }
    },
}


async def main() -> None:
    transport = StreamableHttpTransport(
        url="http://localhost:8080/mcp",
        headers={"X-META": json.dumps(TODO_API_SPEC)},
    )

    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"Found {len(tools)} tools:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description or 'No description'}")

        result = await client.call_tool("listTodos", {})
        todos = result.data if hasattr(result, "data") else result
        print(f"Retrieved {len(todos)} todos")
        print(f"First todo: {todos[0]['title']}")


if __name__ == "__main__":
    print("Make sure the server is running: openapi-to-mcp\n")
    asyncio.run(main())
