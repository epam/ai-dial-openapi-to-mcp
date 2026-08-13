"""OpenTelemetry SDK bootstrap.

Each signal (traces/metrics/logs) is enabled only when the operator sets the
matching OTEL_*_EXPORTER environment variable; with none set, setup_telemetry()
does nothing, so local/dev runs without OTEL configuration are unaffected.
"""

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_initialized = False


def _parse_resource_attributes(raw: Optional[str]) -> Dict[str, str]:
    attributes: Dict[str, str] = {}
    if not raw:
        return attributes
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        if key:
            attributes[key] = value.strip()
    return attributes


def _build_resource():
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource

    attributes: Dict[str, str] = {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "openapi-to-mcp")}
    attributes.update(_parse_resource_attributes(os.getenv("OTEL_RESOURCE_ATTRIBUTES")))
    return Resource.create(attributes)


def _otlp_endpoint() -> Optional[str]:
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")


def _use_http_protocol() -> bool:
    return os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc") in ("http/json", "http/protobuf")


def _setup_traces(resource) -> None:
    exporters = {v.strip() for v in os.getenv("OTEL_TRACES_EXPORTER", "").split(",") if v.strip()}
    if "otlp" not in exporters:
        return

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if _use_http_protocol():
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otlp_endpoint())))
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry traces exporter enabled (otlp)")


def _setup_logs(resource) -> None:
    exporters = {v.strip() for v in os.getenv("OTEL_LOGS_EXPORTER", "").split(",") if v.strip()}
    if "otlp" not in exporters:
        return

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    if _use_http_protocol():
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    else:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=_otlp_endpoint()))
    )
    set_logger_provider(provider)

    # Bridge the existing stdlib logging (configured via logging.basicConfig
    # in server.py) into OTLP without changing any logger.* call sites.
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))
    logger.info("OpenTelemetry logs exporter enabled (otlp)")


def _setup_metrics(resource) -> None:
    exporters = {v.strip() for v in os.getenv("OTEL_METRICS_EXPORTER", "").split(",") if v.strip()}
    if not exporters:
        return

    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider

    readers = []

    if "otlp" in exporters:
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        if _use_http_protocol():
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        else:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=_otlp_endpoint())))
        logger.info("OpenTelemetry metrics exporter enabled (otlp)")

    if "prometheus" in exporters:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from prometheus_client import start_http_server

        readers.append(PrometheusMetricReader())
        start_http_server(port=9464)
        logger.info("OpenTelemetry metrics exporter enabled (prometheus, port 9464)")

    if not readers:
        return

    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)


def setup_telemetry() -> None:
    """Initialize OpenTelemetry traces/metrics/logs per OTEL_* env vars.

    Safe to call multiple times; only the first call takes effect. No-op when
    no OTEL_*_EXPORTER variable is set.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    if not any(
        os.getenv(name)
        for name in ("OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER")
    ):
        return

    try:
        resource = _build_resource()
        _setup_traces(resource)
        _setup_logs(resource)
        _setup_metrics(resource)
    except Exception:
        logger.exception("Failed to initialize OpenTelemetry; continuing without it")
