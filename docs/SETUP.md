# Setup Guide

## Prerequisites

- Python 3.13
- Poetry 2.x
- Docker, optionally, for container verification

## Install and run

```bash
git clone https://github.com/epam/ai-dial-openapi-to-mcp.git
cd ai-dial-openapi-to-mcp
cp .env.template .env
poetry install --with dev
poetry run openapi-to-mcp
```

The CLI loads `.env` at startup. Do not commit `.env` or place real credentials in examples, logs, issue reports, or test fixtures. The streamable HTTP endpoint is available at `http://localhost:8080/mcp`.

Run with the module entry point if preferred:

```bash
poetry run python -m dial_openapi_to_mcp
```

Run local checks with Poetry:

```bash
poetry run black --check src tests
poetry run isort --check-only src tests
poetry run flake8 src tests
poetry run mypy src
poetry run pyright
poetry run pytest
```

The bridge accepts client-selected destinations. Deploy it behind authenticated ingress, and add network restrictions only when required by your environment.

See the [configuration reference](../CONFIGURATION.md), [main README](../README.md), and [security model](security.md) for operational details.
