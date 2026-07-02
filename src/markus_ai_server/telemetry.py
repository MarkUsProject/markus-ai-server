"""Audit logging and proxy handling for intrusion detection.

Emits structured authentication audit events through the OpenTelemetry logs SDK
over OTLP, so they can be stored in Loki and monitored for brute-force / spray
attacks. Setup is best-effort: a missing collector or a missing/experimental SDK
never blocks a request.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger('ai-server')


def apply_proxy_fix(app, trusted_proxy_hops: int) -> None:
    """Make ``request.remote_addr`` report the real client IP behind a proxy.

    When ``trusted_proxy_hops > 0`` the Werkzeug ProxyFix middleware rewrites the
    remote address from the rightmost N entries of ``X-Forwarded-For``. When 0
    (the app is reached directly) the middleware is deliberately NOT applied:
    trusting a client-supplied header would let any caller spoof their IP and
    defeat per-IP intrusion detection.
    """
    if trusted_proxy_hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_hops)


def setup_audit_logging(service_name: str) -> None:
    """Route ``ai-server`` logs through OTLP to the collector (best-effort).

    Uses the OpenTelemetry logs SDK, which is still experimental. Any failure is
    swallowed so the application keeps serving even when telemetry is
    unavailable. Activates only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, so
    local/test/CLI runs neither attempt nor retry exports.
    """
    if not os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT'):
        return
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        provider = LoggerProvider(resource=Resource.create({'service.name': service_name}))
        set_logger_provider(provider)
        # BatchLogRecordProcessor exports on a background thread, so a down
        # collector never blocks the request path.
        provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        logger.addHandler(LoggingHandler(logger_provider=provider))
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the app
        logger.warning(f'OTLP audit logging not initialized: {exc}')


def configure_audit_logging(app) -> None:
    """Wire audit logging and proxy handling from environment configuration.

    Reads ``TRUSTED_PROXY_HOPS`` (trusted reverse proxies in front of the app;
    0 = reached directly) and ``OTEL_SERVICE_NAME`` (service.name on telemetry).
    """
    apply_proxy_fix(app, int(os.getenv('TRUSTED_PROXY_HOPS', '0')))
    setup_audit_logging(os.getenv('OTEL_SERVICE_NAME', 'ai-server'))


def log_auth_failure(result: str, client_ip: Optional[str], endpoint: str) -> None:
    """Emit a structured auth-failure audit event.

    Carries metadata only -- never the API key, prompt, or response body -- so
    the log store stays free of sensitive content. The fields travel as OTel log
    attributes and become Loki structured metadata, queryable by ``client_ip``.

    ``result`` is one of ``missing_key`` or ``invalid_key``.
    """
    logger.warning(
        'authentication failure',
        extra={
            'event': 'auth_failure',
            'result': result,
            'client_ip': client_ip,
            'endpoint': endpoint,
        },
    )
