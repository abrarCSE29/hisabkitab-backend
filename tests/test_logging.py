"""Request logging and observability.

Every request emits one line on the `hisabkitab.request` logger with method,
path, status, duration, and the authenticated user when known. Auth rejections
are logged as warnings, and unhandled exceptions are logged with a stack trace
(uvicorn's access log alone never includes tracebacks).
"""

import logging

import pytest

from app.core.config import get_settings
from app.core.logging import setup_logging
from tests.conftest import TEST_USER_ID, auth_header, make_token


def request_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "hisabkitab.request"]


class TestRequestLog:
    def test_logs_method_path_status_and_duration(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="hisabkitab.request"):
            client.get("/api/v1/health")
        (line,) = request_lines(caplog)
        assert line.startswith("GET /api/v1/health -> 200")
        assert "ms" in line

    def test_logs_verified_user_id(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="hisabkitab.request"):
            client.get("/api/v1/auth/me", headers=auth_header())
        (line,) = request_lines(caplog)
        assert f"user={TEST_USER_ID}" in line

    def test_anonymous_and_rejected_requests_log_no_user(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="hisabkitab.request"):
            client.get("/api/v1/health")
            client.get("/api/v1/vouchers")  # 401, token never verified
        for line in request_lines(caplog):
            assert "user=-" in line

    def test_error_statuses_are_visible(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="hisabkitab.request"):
            client.get("/api/v1/vouchers")
        (line,) = request_lines(caplog)
        assert "-> 401" in line


class TestLogFile:
    def _fresh_root(self):
        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers = []
        return root, saved

    def _restore_root(self, root, saved):
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers = saved

    def test_writes_to_rotating_server_log(self, tmp_path, monkeypatch):
        log_path = tmp_path / "server.log"
        monkeypatch.setattr(get_settings(), "log_file", str(log_path))
        root, saved = self._fresh_root()
        try:
            setup_logging()
            logging.getLogger("hisabkitab.request").info("GET /ping -> 200 (1.0 ms) user=-")
            for handler in root.handlers:
                handler.flush()
            assert "GET /ping -> 200" in log_path.read_text(encoding="utf-8")
        finally:
            self._restore_root(root, saved)

    def test_empty_log_file_setting_disables_file_handler(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "log_file", "")
        root, saved = self._fresh_root()
        try:
            setup_logging()
            assert len(root.handlers) == 1  # stdout only
        finally:
            self._restore_root(root, saved)


class TestAuthRejectionLog:
    def test_expired_token_logged_as_warning(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="app.core.security"):
            client.get("/api/v1/auth/me", headers=auth_header(make_token(expires_in=-60)))
        assert any("expired" in r.getMessage() for r in caplog.records)

    def test_missing_token_logged_with_path(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="app.core.security"):
            client.get("/api/v1/auth/me")
        assert any(
            "without bearer token" in r.getMessage() and "/api/v1/auth/me" in r.getMessage()
            for r in caplog.records
        )

    def test_invalid_token_logged_as_warning(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="app.core.security"):
            client.get("/api/v1/auth/me", headers=auth_header("not-a-jwt"))
        assert any("invalid access token" in r.getMessage() for r in caplog.records)


class TestUnhandledExceptionLog:
    def test_crash_is_logged_with_stack_trace(self, client, caplog):
        @client.app.get("/api/v1/boom")
        def boom():
            raise RuntimeError("kaboom")

        with caplog.at_level(logging.ERROR, logger="hisabkitab.request"):
            with pytest.raises(RuntimeError, match="kaboom"):
                client.get("/api/v1/boom")

        (record,) = [r for r in caplog.records if r.name == "hisabkitab.request"]
        assert record.levelno == logging.ERROR
        assert "GET /api/v1/boom -> unhandled exception" in record.getMessage()
        assert record.exc_info is not None  # full traceback attached
