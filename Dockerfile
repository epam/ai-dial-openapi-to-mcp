FROM python:3.13-alpine AS builder

RUN apk update && apk upgrade --no-cache libcrypto3 libssl3 zlib musl musl-utils
RUN apk add --no-cache gcc alpine-sdk linux-headers musl-dev
RUN pip install --no-cache-dir poetry==2.3.2

WORKDIR /app

COPY pyproject.toml poetry.lock poetry.toml README.md MANIFEST.in ./
RUN poetry install --no-interaction --no-ansi --no-cache --no-root --only main

COPY src/ ./src/
RUN poetry install --no-interaction --no-ansi --no-cache --only main

FROM python:3.13-alpine AS runtime

RUN apk update && apk upgrade --no-cache libcrypto3 libssl3 libexpat zlib musl musl-utils && \
    apk add --no-cache ca-certificates wget

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_PORT=8080 \
    LOG_LEVEL=INFO \
    PATH="/app/.venv/bin:$PATH"

RUN adduser -u 1001 --disabled-password --gecos "" appuser
COPY --chown=appuser --from=builder /app /app

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:8080/v1/configuration-support/application-schema || exit 1

CMD ["openapi-to-mcp"]
