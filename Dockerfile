FROM ghcr.io/astral-sh/uv:0.5-python3.13-alpine AS builder

RUN apk update && apk upgrade --no-cache libcrypto3 libssl3 zlib musl musl-utils
RUN apk add --no-cache gcc musl-dev linux-headers

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

COPY pyproject.toml uv.lock README.md MANIFEST.in ./
RUN uv sync --locked --no-install-project

COPY src/ ./src/
RUN uv sync --locked

FROM python:3.13-alpine AS runtime

RUN apk update && apk upgrade --no-cache libcrypto3 libssl3 libexpat zlib musl musl-utils && \
    apk add --no-cache ca-certificates wget

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    PATH="/app/.venv/bin:$PATH"

RUN adduser -u 1001 --disabled-password --gecos "" appuser
COPY --chown=appuser --from=builder /app /app

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:8080/v1/configuration-support/application-schema || exit 1

CMD ["openapi-to-mcp"]
