<h1 align="center">OpenAPI to MCP</h1>
<p align="center">
  <a href="https://dialx.ai/"><img src="https://dialx.ai/logo/dialx_logo.svg" alt="About DIALX"></a>
</p>
<p align="center">
  <a href="https://discord.gg/ukzj9U9tEe"><img src="https://img.shields.io/static/v1?label=DIALX%20Community%20on&message=Discord&color=blue&logo=Discord&style=flat-square" alt="Discord"></a>
</p>

OpenAPI to MCP is a DIAL-compatible bridge that creates MCP tools from a client-supplied OpenAPI 3.x document. Clients send the document and an API destination in MCP metadata; the bridge creates tools with FastMCP and reuses safe process-local definitions.

## Quick highlights

- Creates MCP tools dynamically from OpenAPI 3.x JSON or YAML.
- Supports optional `x-mcp` names, descriptions, and parameter descriptions.
- Converts Swagger 2.0 documents to OpenAPI 3.0 through `openapi_convert`.
- Resolves DIAL external-service credentials per request and fails closed when resolution fails.
- Controls forwarded headers through an operator-configured block list and optional allowlist.
- Keeps all forwarded header values and credentials request-scoped; cached entries contain no header values.
- Runs as a non-root container and publishes images to GitHub Container Registry.

## Documentation

- [Configuration reference](CONFIGURATION.md)
- [Technical documentation](docs/README.md)
- [Setup guide](docs/SETUP.md)
- [Security model](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## Quick start

```bash
git clone https://github.com/epam/ai-dial-openapi-to-mcp.git
cd ai-dial-openapi-to-mcp
cp .env.template .env
poetry install --with dev
poetry run openapi-to-mcp
```

Or, with `make`:

```bash
git clone https://github.com/epam/ai-dial-openapi-to-mcp.git
cd ai-dial-openapi-to-mcp
cp .env.template .env
make install_dev
openapi-to-mcp
```

The CLI loads `.env` at startup. The streamable HTTP endpoint is available at `http://localhost:8080/mcp`, with `GET /health` and `GET /ready` available for liveness/readiness probes (see [CONFIGURATION.md](CONFIGURATION.md#health--readiness)).

> [!WARNING]
> OpenAPI documents, destinations, tool arguments, and request headers are untrusted and may contain sensitive data. Run the bridge behind authenticated ingress, grant access only to trusted clients, and never publish real credentials in examples, logs, issues, or test fixtures.

## Configuration

The complete server, cache, DIAL, and header-forwarding reference is in [CONFIGURATION.md](CONFIGURATION.md).

A minimal configuration:

```env
OUTBOUND_HEADER_ALLOWLIST=x-request-id
LOG_LEVEL=INFO
```

`OUTBOUND_HEADER_ALLOWLIST` has three distinct states: unset allows every header not blocked by `OUTBOUND_HEADER_BLOCKLIST`; explicitly empty permits no client-forwarded headers; a populated value restricts forwarding to the listed names. Header values remain request-scoped in every mode and are not stored in cache entries.

## Local development

### Prerequisites

1. Python 3.13.
2. [Poetry](https://python-poetry.org/) 2.x.
3. Docker, optionally, for container verification.

### Setup and run

```bash
cp .env.template .env
poetry install --with dev
poetry run openapi-to-mcp
# or
poetry run python -m dial_openapi_to_mcp
```

Or, with `make`:

```bash
cp .env.template .env
make install_dev
openapi-to-mcp
```

Run the container:

```bash
docker compose up --build
```

### Checks

```bash
poetry run black --check src tests
poetry run isort --check-only src tests
poetry run flake8 src tests
poetry run mypy src
poetry run pyright
poetry run pytest
poetry run pytest --cov=dial_openapi_to_mcp
```

Or, with `make` (each target bundles several of the checks above):

```bash
make lint       # black --check, isort --check, flake8, autoflake --check, mypy, pyright, poetry check --lock
make test       # unit tests only
make test_cov   # unit tests with coverage report and floor
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
