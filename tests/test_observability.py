"""Tests for observability: JSON logging, request-id middleware, CORS (GAP)."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from cappo_backend.observability.logging import JsonLogFormatter


class TestJsonLogFormatter:
    def test_basic_record_is_json(self) -> None:
        record = logging.makeLogRecord(
            {"name": "cappo.test", "levelno": logging.INFO, "levelname": "INFO",
             "msg": "hello %s", "args": ("world",)}
        )
        out = JsonLogFormatter().format(record)
        parsed = json.loads(out)
        assert parsed["logger"] == "cappo.test"
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_extra_fields_promoted(self) -> None:
        record = logging.makeLogRecord(
            {"name": "cappo.law0", "levelno": logging.WARNING, "levelname": "WARNING",
             "msg": "alert", "operation_type": "law0_violation", "workspace_id": "ws1"}
        )
        parsed = json.loads(JsonLogFormatter().format(record))
        assert parsed["operation_type"] == "law0_violation"
        assert parsed["workspace_id"] == "ws1"


class TestRequestId:
    def test_response_has_request_id(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID")

    def test_request_id_echoed_when_supplied(self, client: TestClient) -> None:
        resp = client.get("/health", headers={"X-Request-ID": "abc-123"})
        assert resp.headers.get("X-Request-ID") == "abc-123"


class TestCors:
    def test_preflight_allows_origin(self, client: TestClient) -> None:
        resp = client.options(
            "/v1/exec",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code in (200, 204)
        assert resp.headers.get("access-control-allow-origin") in ("*", "https://example.com")
