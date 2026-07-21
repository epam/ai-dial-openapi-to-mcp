# Code style

- Use Python 3.13 type hints and modern generics.
- Keep internal APIs prefixed with `_`; expose only deliberate public interfaces.
- Parse configuration through typed settings or application factories, not scattered `os.getenv` calls.
- Use Pydantic models for structured external input where practical.
- Import in standard library, third-party, then local groups.
- Use lazy logging (`logger.info("value=%s", value)`). Logs at every level contain metadata only: never headers values, tokens, request bodies, OpenAPI documents, tool arguments, or URL queries.
- Catch expected exceptions narrowly and preserve cancellation behavior.
- Keep unit tests isolated and use `MagicMock(spec=...)`/`AsyncMock` for dependencies.
- Run `make format` and `make lint` before opening a pull request.
