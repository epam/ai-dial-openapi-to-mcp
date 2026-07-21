<h1 align="center">OpenAPI to MCP</h1>
<p align="center">
  <a href="https://dialx.ai/"><img src="https://dialx.ai/logo/dialx_logo.svg" alt="About DIALX"></a>
</p>
<p align="center">
  <a href="https://discord.gg/ukzj9U9tEe"><img src="https://img.shields.io/static/v1?label=DIALX%20Community%20on&message=Discord&color=blue&logo=Discord&style=flat-square" alt="Discord"></a>
</p>

OpenAPI to MCP is a DIAL-compatible bridge that creates MCP tools from a client-supplied OpenAPI 3.x document. Clients send the document and an API destination in MCP metadata; the bridge creates tools with FastMCP and reuses safe process-local definitions.

The bridge is an outbound network client. Deploy it behind authenticated ingress, and rely on external network egress controls (a firewall or egress proxy) to restrict which destinations it can reach — the bridge does not restrict outbound destinations itself.

## Quick highlights

- Creates MCP tools dynamically from OpenAPI 3.x JSON or YAML.
- Supports optional `x-mcp` names, descriptions, and parameter descriptions.
- Converts Swagger 2.0 documents to OpenAPI 3.0 through `openapi_convert`.
- Resolves DIAL external-service credentials per request and fails closed when resolution fails.
- Restricts forwarded headers by operator configuration.
- Runs as a non-root container and publishes images to GitHub Container Registry.

## Documentation

- [Configuration reference](CONFIGURATION.md)
- [Technical documentation](docs/README.md)
- [Security model](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Quick start (general)

```bash
git clone https://github.com/epam/ai-dial-openapi-to-mcp.git
cd ai-dial-openapi-to-mcp
cp .env.template .env
make install_dev
openapi-to-mcp
```

The streamable HTTP endpoint is available at `http://localhost:8080/mcp`.

> [!IMPORTANT]
> The bridge relies on external network egress controls to restrict outbound destinations. Deploy it only in an environment where such a control (firewall, egress proxy, network policy) already exists.

## Configuration

The complete server, cache, DIAL, and header-forwarding reference is in [CONFIGURATION.md](CONFIGURATION.md).

A minimal configuration:

```env
OUTBOUND_HEADER_ALLOWLIST=x-request-id
LOG_LEVEL=INFO
```

## Local Development

### Pre-requisites

1. Python 3.13.
2. [Poetry](https://python-poetry.org/) 2.x for the current repository workflow.
3. Docker, optionally, for container verification.

### Setup

```bash
cp .env.template .env
make install_dev
```

### Run

```bash
openapi-to-mcp
# or
python -m dial_openapi_to_mcp
```

Run the container:

```bash
docker compose up --build
```

### Utils

```bash
make format
make lint
make test
make test_cov
```

## MCP usage

Pass an OpenAPI document through MCP `_meta.openapi` or the `X-META` request header. A base URL may be supplied through `X-BASE-URL`, `_meta.base_url`, or the OpenAPI `servers` field.

Use a normal MCP client session rather than sending incomplete raw JSON-RPC requests. The integration tests in [`tests/test_real_integration.py`](tests/test_real_integration.py) demonstrate a `FastMCP.Client` streamable-HTTP setup.

The bridge exposes these utility tools:

- `openapi_verify` — validate an OpenAPI 3.x document and FastMCP compatibility.
- `openapi_convert` — convert Swagger 2.0 to OpenAPI 3.0.

The bridge intentionally does not expose filesystem paths or cache mutation tools to MCP clients.

See [`examples/`](examples/README.md) for a runnable client script and sample OpenAPI documents.

## E2E and integration tests

The repository includes unit, concurrency, and streamable-HTTP integration coverage. See [Testing](docs/testing.md) for commands and troubleshooting.

## More

For DIAL documentation and community support, visit [DIALX](https://dialx.ai/docs) and [Discord](https://discord.gg/ukzj9U9tEe).
