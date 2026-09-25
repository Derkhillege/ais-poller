"""Smoke tests for _load_config — happy path and fail-fast error paths."""
import json
from unittest.mock import patch

import pytest

from ais_poller import _load_config


@pytest.fixture(autouse=True)
def no_dotenv():
    """Prevent load_dotenv() from reading .env so monkeypatched env vars are authoritative."""
    with patch("ais_poller.load_dotenv", lambda **_kw: None):
        yield


def test_load_config_happy_path(monkeypatch):
    """All required env vars present and valid — should return correct values."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1, "method": "getVessels"}))
    monkeypatch.setenv("INTERVAL", "30")
    monkeypatch.delenv("OUTPUT_PATH", raising=False)

    wss_url, request_params, interval, output_path = _load_config()

    assert wss_url == "wss://example.com/ws"
    assert request_params["id"] == 1
    assert interval == 30
    assert str(output_path) == "data.json"


def test_load_config_missing_required_var(monkeypatch):
    """Missing EURIS_WSS must raise SystemExit (fail-fast)."""
    monkeypatch.delenv("EURIS_WSS", raising=False)
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.setenv("INTERVAL", "10")

    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_missing_interval(monkeypatch):
    """Missing INTERVAL must raise SystemExit."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.delenv("INTERVAL", raising=False)

    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_invalid_interval(monkeypatch):
    """Non-integer INTERVAL must raise SystemExit."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.setenv("INTERVAL", "not-a-number")

    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_malformed_request_params(monkeypatch):
    """Invalid JSON in REQUEST_PARAMS must raise SystemExit."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", "{not valid json}")
    monkeypatch.setenv("INTERVAL", "10")

    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_request_params_missing_id(monkeypatch):
    """REQUEST_PARAMS without 'id' field must raise SystemExit."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"method": "getVessels"}))
    monkeypatch.setenv("INTERVAL", "10")

    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_custom_output_path(monkeypatch, tmp_path):
    """OUTPUT_PATH env var is respected."""
    custom_path = str(tmp_path / "output.ndjson")
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 42}))
    monkeypatch.setenv("INTERVAL", "5")
    monkeypatch.setenv("OUTPUT_PATH", custom_path)

    _, _, _, output_path = _load_config()

    assert str(output_path) == custom_path
