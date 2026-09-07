"""Unit tests for audit logging / intrusion detection."""

import logging
import os
from unittest.mock import patch

import pytest
from flask import Flask, request
from werkzeug.exceptions import HTTPException

# Importing the app loads redis_helper, which reads REDIS_URL at import time.
# Set a default first so the import works in a bare test environment.
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379')

from markus_ai_server import telemetry  # noqa: E402
from markus_ai_server.server import app, authenticate  # noqa: E402


def _auth_events(caplog):
    """Return only the structured auth-failure records captured."""
    return [r for r in caplog.records if getattr(r, 'event', None) == 'auth_failure']


def _reject_and_capture(caplog, headers=None, environ_overrides=None):
    """Run authenticate() with no valid key, assert the 401, return the audit record."""
    with patch('markus_ai_server.server.REDIS_CONNECTION') as redis_mock:
        redis_mock.get.return_value = None  # any presented key is unknown
        with app.test_request_context('/chat', method='POST', headers=headers, environ_overrides=environ_overrides):
            with caplog.at_level(logging.WARNING, logger='ai-server'):
                with pytest.raises(HTTPException) as exc:
                    authenticate()
    assert exc.value.code == 401
    return _auth_events(caplog)[-1]


def _post_chat(known_user, headers=None, data=None):
    """POST /chat with REDIS_CONNECTION.get returning ``known_user`` (None = unknown key)."""
    with patch('markus_ai_server.server.REDIS_CONNECTION') as redis_mock:
        redis_mock.get.return_value = known_user
        return app.test_client().post('/chat', headers=headers, data=data)


class TestStructuredAuditEvents:
    def test_log_auth_failure_emits_all_fields(self, caplog):
        with caplog.at_level(logging.WARNING, logger='ai-server'):
            telemetry.log_auth_failure('invalid_key', '1.2.3.4', '/chat')
        rec = _auth_events(caplog)[-1]
        assert rec.event == 'auth_failure'
        assert rec.result == 'invalid_key'
        assert rec.client_ip == '1.2.3.4'
        assert rec.endpoint == '/chat'

    def test_log_auth_failure_carries_no_secrets(self, caplog):
        """Audit events must be metadata only -- no key, prompt, or body."""
        with caplog.at_level(logging.WARNING, logger='ai-server'):
            telemetry.log_auth_failure('invalid_key', '1.2.3.4', '/chat')
        rec = _auth_events(caplog)[-1]
        blob = (rec.getMessage() + str(rec.__dict__)).lower()
        assert 'api-key' not in blob
        assert 'x-api-key' not in blob
        assert not hasattr(rec, 'api_key')


class TestAuthenticateLogsEvents:
    def test_missing_key_logs_missing_result(self, caplog):
        rec = _reject_and_capture(caplog)
        assert rec.result == 'missing_key'
        assert rec.endpoint == '/chat'

    def test_invalid_key_logs_invalid_result_with_ip(self, caplog):
        rec = _reject_and_capture(
            caplog, headers={'X-API-KEY': 'bogus'}, environ_overrides={'REMOTE_ADDR': '203.0.113.7'}
        )
        assert rec.result == 'invalid_key'
        assert rec.client_ip == '203.0.113.7'

    def test_attempted_key_value_never_in_record(self, caplog):
        """The key a caller tried must not appear in the audit record."""
        secret = 'sk-attacker-guess-2f9a'
        rec = _reject_and_capture(caplog, headers={'X-API-KEY': secret})
        assert secret not in (rec.getMessage() + str(rec.__dict__))


class TestAuthHttpContract:
    """A rejected key must surface as 401, not a catch-all 500."""

    def test_missing_key_returns_401(self):
        resp = _post_chat(None, data={'content': 'hi'})
        assert resp.status_code == 401

    def test_invalid_key_returns_401(self):
        resp = _post_chat(None, headers={'X-API-KEY': 'nope'}, data={'content': 'hi'})
        assert resp.status_code == 401

    def test_missing_content_returns_400_not_500(self):
        """A valid key with empty content keeps its 400 (handler preserves it)."""
        resp = _post_chat(b'alice', headers={'X-API-KEY': 'ok'}, data={'content': '   '})
        assert resp.status_code == 400


class TestSetupAuditLogging:
    """The OTLP setup is gated and best-effort."""

    def test_noop_without_endpoint(self, monkeypatch):
        monkeypatch.delenv('OTEL_EXPORTER_OTLP_ENDPOINT', raising=False)
        before = list(telemetry.logger.handlers)
        telemetry.setup_audit_logging('ai-server')
        assert telemetry.logger.handlers == before  # nothing attached

    def test_attaches_handler_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4317')
        before = len(telemetry.logger.handlers)
        with (
            patch('opentelemetry._logs.set_logger_provider') as set_lp,
            patch('opentelemetry.sdk._logs.LoggerProvider'),
            patch('opentelemetry.sdk._logs.LoggingHandler'),
            patch('opentelemetry.sdk._logs.export.BatchLogRecordProcessor'),
            patch('opentelemetry.exporter.otlp.proto.grpc._log_exporter.OTLPLogExporter'),
        ):
            telemetry.setup_audit_logging('ai-server')
        try:
            assert set_lp.called
            assert len(telemetry.logger.handlers) == before + 1
        finally:
            while len(telemetry.logger.handlers) > before:
                telemetry.logger.removeHandler(telemetry.logger.handlers[-1])

    def test_swallows_setup_errors(self, monkeypatch, caplog):
        monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4317')
        with patch('opentelemetry.sdk.resources.Resource.create', side_effect=RuntimeError('boom')):
            with caplog.at_level(logging.WARNING, logger='ai-server'):
                telemetry.setup_audit_logging('ai-server')  # must not raise
        assert any('not initialized' in r.getMessage() for r in caplog.records)


class TestClientIpResolution:
    """ProxyFix must give the real client IP only when behind a trusted proxy."""

    @staticmethod
    def _ip_app(trusted_proxy_hops):
        test_app = Flask(__name__)
        telemetry.apply_proxy_fix(test_app, trusted_proxy_hops)

        @test_app.route('/ip')
        def ip():
            return request.remote_addr or ''

        return test_app.test_client()

    def test_behind_proxy_uses_forwarded_for(self):
        client = self._ip_app(trusted_proxy_hops=1)
        resp = client.get(
            '/ip',
            headers={'X-Forwarded-For': '9.9.9.9'},
            environ_overrides={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.get_data(as_text=True) == '9.9.9.9'

    def test_direct_access_ignores_spoofed_forwarded_for(self):
        client = self._ip_app(trusted_proxy_hops=0)  # ProxyFix not applied
        resp = client.get(
            '/ip',
            headers={'X-Forwarded-For': '9.9.9.9'},  # attacker-supplied
            environ_overrides={'REMOTE_ADDR': '10.0.0.1'},
        )
        assert resp.get_data(as_text=True) == '10.0.0.1'
