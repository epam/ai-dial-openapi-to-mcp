# Setup Guide

Setup instructions now live in the [main README](../README.md) and the [configuration reference](../CONFIGURATION.md).

Use the supported local workflow:

```bash
cp .env.template .env
make install_dev
openapi-to-mcp
```

For secure container deployment, see the [README](../README.md#local-development) and [security model](security.md).
