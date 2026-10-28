# Configuring OpenAPI to MCP in DIAL

OpenAPI to MCP is available in DIAL as an application type named **"OpenAPI to MCP"**. Setting it up has two parts:

1. [Application Runner setup](#part-1--application-runner-setup) — registers the application type in DIAL. An administrator does this once per DIAL environment.
2. [Configuring a DIAL application](#part-2--configuring-a-dial-application) — creates an application of that type that turns an OpenAPI-described API into MCP tools DIAL clients can call.

## Part 1 — Application Runner setup

An **Application Runner** is the DIAL entity that registers the application type. It connects DIAL to two endpoints of this service:

| Purpose | Endpoint on the bridge |
|---|---|
| Application-type configuration form | `GET /v1/configuration-support/application-schema` (see [Application schema endpoint](#application-schema-endpoint)) |
| MCP traffic | `/mcp` |

### Where to configure it

In DIAL Admin open **Builders → Application Runners**, then create a runner or open an existing one. The **JSON Editor** toggle on the runner page shows the runner as JSON. Its format dropdown offers **Core format** and **Admin format**; this document uses the Core format, which is the definition as DIAL Core stores it. Admin format adds read-only fields such as `applications`, `createdAt` and `updatedAt`.

### Core format

```json
{
  "type": "object",
  "$schema": "https://dial.epam.com/application_type_schemas/schema#",
  "$id": "https://<dial-host>/openapi_2_mcp",
  "dial:applicationTypeDisplayName": "OpenAPI 2 MCP Runner",
  "dial:appendApplicationPropertiesHeader": true,
  "dial:applicationTypeInterceptors": [],
  "dial:applicationTypeAssistantAttachmentsInRequestSupported": false,
  "dial:applicationTypeSchemaEndpoint": "http://<service>.<namespace>.svc.cluster.local/v1/configuration-support/application-schema",
  "dial:applicationTypeMcp": {
    "dial:endpoint": "http://<service>.<namespace>.svc.cluster.local/mcp",
    "dial:transport": "HTTP",
    "dial:allowedTools": [],
    "dial:mcpConfigDelivery": "META",
    "dial:forwardPerRequestKey": true
  }
}
```

Replace `<dial-host>`, `<service>` and `<namespace>` with your deployment's values. The example reflects the runner on the dev environment, where the bridge is reached through its in-cluster service URL.

### Runner fields

| JSON key | Admin UI field | Value |
|---|---|---|
| `$schema` | — | Always `https://dial.epam.com/application_type_schemas/schema#`. |
| `$id` | Runner ID | Unique ID of the application type, for example `https://<dial-host>/openapi_2_mcp`. Applications reference the type by this ID. |
| `dial:applicationTypeDisplayName` | Display Name | Name shown to users when they pick the application type. |
| `dial:applicationTypeSchemaEndpoint` | Schema endpoint | URL of the bridge's `/v1/configuration-support/application-schema`. DIAL reads the configuration form from here. |
| `dial:applicationTypeMcp.dial:endpoint` | MCP Endpoint → Endpoint | URL of the bridge's `/mcp`. |
| `dial:applicationTypeMcp.dial:transport` | MCP Endpoint → Transport | `HTTP`. |
| `dial:applicationTypeMcp.dial:allowedTools` | MCP Endpoint → Allowed tools | Empty list in the current runner. |
| `dial:applicationTypeMcp.dial:mcpConfigDelivery` | Configuration delivery | `META` in the current runner. |
| `dial:applicationTypeMcp.dial:forwardPerRequestKey` | Forward per request key | `true` in the current runner. |

The remaining keys (`type`, `dial:appendApplicationPropertiesHeader`, `dial:applicationTypeInterceptors`, `dial:applicationTypeAssistantAttachmentsInRequestSupported`) are shown as they appear on the current runner.

### Application schema endpoint

The bridge serves the application-type schema over HTTP so DIAL can discover the configuration form:

```
GET /v1/configuration-support/application-schema
```

The response is the contents of [`application_schema.json`](../src/dial_openapi_to_mcp/application_schema.json) as `application/json`: a DIAL application-type schema (`$schema: https://dial.epam.com/application_type_schemas/schema#`) with display name "OpenAPI to MCP" and the four configuration properties described in [Part 2](#configuration-fields). The endpoint is served on `MCP_PORT` alongside `/health` and `/ready`, and the schema is static: it does not depend on any application's configuration.

```bash
curl http://localhost:<MCP_PORT>/v1/configuration-support/application-schema
```

The container smoke test also uses this endpoint as its liveness check.

### Checklist

1. Deploy the bridge and make sure both URLs are reachable from DIAL Core: `GET <bridge>/v1/configuration-support/application-schema` returns the schema JSON, and `<bridge>/mcp` is the MCP endpoint.
2. Create the Application Runner and set `$id`, `dial:applicationTypeSchemaEndpoint` and `dial:applicationTypeMcp.dial:endpoint`.
3. Create a DIAL application of the new type and fill in the form. See [Part 2](#part-2--configuring-a-dial-application).

## Part 2 — Configuring a DIAL application

An application of the type registered in Part 1 carries the target API's configuration under `application_properties`, plus DIAL-level keys around it.

### Application structure

| Key | Description |
|---|---|
| `name`, `display_name`, `display_version` | Identity of the application as DIAL shows it. |
| `application_type_schema_id` | The `$id` of the Application Runner this application is created from, for example `https://<dial-host>/openapi_2_mcp`. |
| `application_properties` | The configuration: `openapi`, `base_url`, `extra_headers`, `external_service`. |
| `external_services` | Map of external-service entries, keyed by name. See [External services](#external-services). |

### Configuration fields

The configuration form is generated from [`application_schema.json`](../src/dial_openapi_to_mcp/application_schema.json) and has four fields, described below in the order they appear.

#### `openapi` — OpenAPI Spec (required)

Provide the OpenAPI 3.x document for the target API. Swagger 2.0 documents are accepted too; the bridge converts them to OpenAPI 3.0 automatically. This is the only required field — every tool the application exposes is generated from the operations in this document.

#### `base_url` — Base URL (optional)

The base URL that tool calls are made relative to. Leave it unset to let the bridge infer it from the OpenAPI document itself (the first entry in `servers`, or, for Swagger 2.0, `host`/`schemes`/`basePath`). Set it explicitly when the document has no usable server URL, or when you need to target a different environment (e.g. staging) than the one the document declares.

#### `extra_headers` — Extra Headers (optional)

A list of static `name`/`value` pairs sent on every outbound call to the target API — for example a fixed tenant or client identifier header.

> [!WARNING]
> `extra_headers` is not a place to put secrets. The bridge operator's outbound header block list strips `authorization`, `cookie`, `set-cookie`, and similar headers by default, so a bearer token or API key set here will be silently dropped rather than forwarded. See [CONFIGURATION.md § Outbound header forwarding](../CONFIGURATION.md#outbound-header-forwarding) for the exact default block list and how operators can change it. To authenticate to the target API, use `external_service` below instead.

#### `external_service` — External Service (optional, for APIs that require auth)

Use this field when the target API needs an auth header (API key, bearer token, etc.). Set it to the key of an entry in the application's `external_services` map; see [External services](#external-services) for how to define the entry.

With `external_service` set, the bridge resolves a live credential from DIAL Core on every tool call — the header name and value are fetched fresh per request and injected only into that call; nothing is cached. If the named entry isn't configured on the application, the call fails with a `404`-style error. If it's configured but the signed-in user hasn't completed sign-in for it yet, the call fails with a `401` plus a sign-in challenge (`scope` of `<application>/external_services/<name>`) that the client should act on before retrying the tool call. See [CONFIGURATION.md § DIAL credentials](../CONFIGURATION.md#dial-credentials) and [Security model](security.md) for the exact `_meta` error shape.

### External services

`application_properties.external_service` names the entry in the application's top-level `external_services` map (the **External Services** section in DIAL Admin) that holds the credential for the target API. Each entry has a `display_name` and `auth_settings` describing an OAuth client:

| Key | Description |
|---|---|
| `authentication_type` | `OAUTH`. |
| `client_id` | OAuth client ID registered with the identity provider. |
| client secret | OAuth client secret. This is where the actual secret lives. After it is saved, the admin editor shows only `client_secret_hint`. |
| `authorization_endpoint`, `token_endpoint` | The identity provider's OAuth endpoints. |
| `redirect_uri` | Where the provider sends the user back after sign-in. On the reference application this is the admin host's `/external-service-signin` page; register the same URI with the identity provider. |
| `scopes_supported` | Scopes requested at sign-in, for example `offline_access`. |

The admin editor also shows the read-only sign-in state of each entry (`user_level_auth_status`, `app_level_auth_status`). A user completes sign-in against the entry; until then tool calls fail with a `401` and a sign-in challenge, as described under [`external_service`](#external_service--external-service-optional-for-apis-that-require-auth).

### Example

An application that exposes the DIAL Core API as MCP tools and signs in through a Keycloak external service:

```json
{
  "name": "dial-core-via-openapi",
  "display_name": "DIAL Core via OpenAPI",
  "display_version": "1.0.0",
  "application_type_schema_id": "https://<dial-host>/openapi_2_mcp",
  "application_properties": {
    "openapi": { "...": "the DIAL Core API's OpenAPI 3.0 document" },
    "base_url": "https://<dial-core-host>",
    "extra_headers": [],
    "external_service": "keycloak"
  },
  "external_services": {
    "keycloak": {
      "display_name": "Keycloak",
      "auth_settings": {
        "authentication_type": "OAUTH",
        "client_id": "<client-id>",
        "client_secret": "<client-secret>",
        "authorization_endpoint": "https://<idp-host>/realms/<realm>/protocol/openid-connect/auth",
        "token_endpoint": "https://<idp-host>/realms/<realm>/protocol/openid-connect/token",
        "redirect_uri": "https://<dial-admin-host>/external-service-signin",
        "scopes_supported": ["offline_access"]
      }
    }
  }
}
```

The DIAL Core API document declares two security schemes: an API key sent in the `Api-Key` header, and a JWT sent as a bearer token in `Authorization`.

## See also

- [Configuration reference](../CONFIGURATION.md) — operator-side environment variables, including outbound header forwarding and DIAL credentials.
- [Security model](security.md) — credential resolution failure modes and header-forwarding policy.
- [Architecture](architecture.md) — request lifecycle and where these fields are consumed.
