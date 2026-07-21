# Testing

## Local checks

```bash
make format
make lint
make test
make test_cov
```

## Integration tests

The streamable-HTTP integration suite starts a local Starlette API. It verifies MCP initialization, generated tools, tool execution, and restricted header forwarding.

```bash
uv run pytest tests/test_real_integration.py -q
```

## Security checks

Run DIAL credential tests after changes to logging, headers, or credentials:

```bash
uv run pytest tests/test_dial_credentials.py -q
```

Before release, run full-history secret scanning, build/install the wheel in a clean environment, build the container, and exercise the image from behind the intended network egress control.
