# How to contribute

OpenAPI to MCP welcomes bug fixes, security improvements, tests, documentation, and new compatibility coverage.

Use the shared [DIAL contribution guidance](https://github.com/epam/ai-dial/blob/main/CONTRIBUTING.md), then run:

```bash
make format
make lint
make test
```

Use Conventional Commit titles and include tests and documentation for behavior changes. Do not commit credentials, internal URLs, request captures, or generated local configuration.
