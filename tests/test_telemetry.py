"""Smoke tests for OpenTelemetry bootstrap: no-op by default, opt-in via env vars."""

import pytest

from dial_openapi_to_mcp import telemetry


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(telemetry, "_initialized", False)
    for name in (
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    monkeypatch.setattr(telemetry, "_initialized", False)


def test_setup_is_noop_without_otel_env(monkeypatch):
    calls = []
    monkeypatch.setattr("prometheus_client.start_http_server", lambda **kw: calls.append(kw))

    telemetry.setup_telemetry()

    assert telemetry._initialized is True
    assert calls == []


def test_setup_starts_prometheus_server_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "prometheus")
    monkeypatch.setattr("prometheus_client.start_http_server", lambda **kw: calls.append(kw))

    telemetry.setup_telemetry()

    assert calls == [{"port": 9464}]


def test_setup_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "prometheus")
    monkeypatch.setattr("prometheus_client.start_http_server", lambda **kw: calls.append(kw))

    telemetry.setup_telemetry()
    telemetry.setup_telemetry()

    assert len(calls) == 1
