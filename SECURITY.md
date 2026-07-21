# Reporting Security Issues

We take security reports seriously and appreciate responsible disclosure.

> [!CAUTION]
> Do **not** file public GitHub issues for vulnerabilities.

Report vulnerabilities through the [GitHub Security Advisory report form](https://github.com/epam/ai-dial-openapi-to-mcp/security/advisories/new). Include enough detail to reproduce the issue without publishing credentials, tokens, private URLs, or customer data.

## Security model

OpenAPI to MCP sends outbound HTTP requests based on client-provided OpenAPI metadata. Deploy it behind authenticated ingress and behind external network egress controls (a firewall or egress proxy) that restrict which destinations it can reach — the bridge itself does not restrict outbound destinations. Credentials are request-scoped and logs must contain metadata rather than payloads or header values.

The initial supported version is the latest release on the `development` branch until the first tagged release establishes a formal support policy.
