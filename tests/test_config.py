"""Smoke tests for load_config — happy path and fail-fast error paths."""

import json
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from ais_poller.config import load_config


@pytest.fixture(autouse=True)
def no_dotenv():
    """Prevent load_dotenv() from reading .env so monkeypatched env vars are authoritative."""
    with patch("ais_poller.config.load_dotenv", lambda *_a, **_kw: None):
        yield


def test_load_config_happy_path(monkeypatch):
    """All required env vars present and valid — should return correct values."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1, "method": "getVessels"}))
    monkeypatch.setenv("INTERVAL", "30")
    monkeypatch.delenv("OUTPUT_PATH", raising=False)

    config = load_config()

    assert config.wss_url == "wss://example.com/ws"
    assert config.interval == 30
    (area,) = config.areas
    assert str(area.output_path) == "data.json"
    # Env mode sends REQUEST_PARAMS unchanged.
    assert config.request_for(area) == {"id": 1, "method": "getVessels"}


def test_load_config_missing_required_var(monkeypatch):
    """Missing EURIS_WSS must raise KeyError (fail-fast)."""
    monkeypatch.delenv("EURIS_WSS", raising=False)
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.setenv("INTERVAL", "10")

    with pytest.raises(KeyError):
        load_config()


def test_load_config_missing_interval(monkeypatch):
    """Missing INTERVAL must raise KeyError."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.delenv("INTERVAL", raising=False)

    with pytest.raises(KeyError):
        load_config()


def test_load_config_invalid_interval(monkeypatch):
    """Non-integer INTERVAL must raise ValueError."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.setenv("INTERVAL", "not-a-number")

    with pytest.raises(ValueError):
        load_config()


def test_load_config_malformed_request_params(monkeypatch):
    """Invalid JSON in REQUEST_PARAMS must raise JSONDecodeError."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", "{not valid json}")
    monkeypatch.setenv("INTERVAL", "10")

    with pytest.raises(json.JSONDecodeError):
        load_config()


def test_load_config_request_params_missing_id(monkeypatch):
    """REQUEST_PARAMS without 'id' field must raise ValueError."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"method": "getVessels"}))
    monkeypatch.setenv("INTERVAL", "10")

    with pytest.raises(ValueError):
        load_config()


def test_load_config_custom_output_path(monkeypatch, tmp_path):
    """OUTPUT_PATH env var is respected."""
    custom_path = str(tmp_path / "output.ndjson")
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 42}))
    monkeypatch.setenv("INTERVAL", "5")
    monkeypatch.setenv("OUTPUT_PATH", custom_path)

    assert str(load_config().areas[0].output_path) == custom_path


def test_load_config_overrides_take_precedence(monkeypatch, tmp_path):
    """Explicit arguments (CLI flags) override env vars."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.setenv("INTERVAL", "5")

    config = load_config(wss_url="wss://other/ws", interval=60, output_path=tmp_path / "x.json")

    assert config.wss_url == "wss://other/ws"
    assert config.interval == 60
    assert config.areas[0].output_path == tmp_path / "x.json"


def test_load_config_override_skips_missing_env(monkeypatch):
    """A CLI-provided interval means INTERVAL need not be set."""
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.delenv("INTERVAL", raising=False)

    assert load_config(interval=10).interval == 10


# --- TOML (multi-area) config -------------------------------------------------------------

TOML = """
url = "wss://example.com/ws"
interval = 10
output_dir = "out"

[request]
jsonrpc = "2.0"
method = "StreamFeatures"
id = 1
params.filters.filterQuery = "()"

[[areas]]
name = "north"
bbox = [51.0, 4.0, 52.0, 5.0]

[[areas]]
name = "south"
bbox = [50, 3, 51, 4]
output = "elsewhere/south.json"
"""


def _write(tmp_path, text):
    path = tmp_path / "ais-poller.toml"
    path.write_text(text)
    return path


def test_toml_config(tmp_path):
    config = load_config(config_file=_write(tmp_path, TOML))

    assert config.wss_url == "wss://example.com/ws"
    assert config.interval == 10
    north, south = config.areas
    assert north.output_path == Path("out/north.ndjson")
    assert south.output_path == Path("elsewhere/south.json")
    assert south.bbox == (50.0, 3.0, 51.0, 4.0)


def test_request_for_fills_bbox_and_copies(tmp_path):
    config = load_config(config_file=_write(tmp_path, TOML))
    north, south = config.areas

    req_north = config.request_for(north)
    req_south = config.request_for(south)

    assert req_north["params"]["filters"] == {
        "filterQuery": "()",
        "boundingBox": {"minLat": 51.0, "minLon": 4.0, "maxLat": 52.0, "maxLon": 5.0},
    }
    # Independent copies: incrementing one area's id must not affect another or the template.
    req_north["id"] += 5
    assert req_south["id"] == 1
    assert config.request_template["id"] == 1
    assert "boundingBox" not in config.request_template["params"]["filters"]


def test_toml_overrides(tmp_path):
    config = load_config(
        config_file=_write(tmp_path, TOML),
        wss_url="wss://other/ws",
        interval=3,
        output_path=tmp_path / "dir",
    )
    assert config.wss_url == "wss://other/ws"
    assert config.interval == 3
    assert config.areas[0].output_path == tmp_path / "dir" / "north.ndjson"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('url = "wss://example.com/ws"', ""),
        ("interval = 10", "interval = 0"),
        ("id = 1", 'id = "one"'),
        ("bbox = [51.0, 4.0, 52.0, 5.0]", "bbox = [52.0, 4.0, 51.0, 5.0]"),
        ("bbox = [51.0, 4.0, 52.0, 5.0]", "bbox = [51.0, 4.0, 52.0]"),
        ('name = "south"', 'name = "north"'),
        ('name = "south"', 'name = "../south"'),
        ('output = "elsewhere/south.json"', 'output = "out/north.ndjson"'),
    ],
)
def test_toml_invalid(tmp_path, old, new):
    assert old in TOML
    with pytest.raises(ValueError):
        load_config(config_file=_write(tmp_path, TOML.replace(old, new, 1)))


def test_toml_requires_areas(tmp_path):
    text = TOML.split("[[areas]]")[0]
    with pytest.raises(ValueError):
        load_config(config_file=_write(tmp_path, text))


def test_toml_missing_or_malformed_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(config_file=tmp_path / "nope.toml")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(config_file=_write(tmp_path, "url = "))


def test_example_config_is_valid():
    example = Path(__file__).parent.parent / "ais-poller.example.toml"
    config = load_config(config_file=example)
    assert [a.name for a in config.areas] == ["moerdijk"]
    assert config.duration == 30.0
    assert config.areas[0].id == 2


def test_load_config_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("EURIS_WSS", "wss://example.com/ws")
    monkeypatch.setenv("REQUEST_PARAMS", json.dumps({"id": 1}))
    monkeypatch.setenv("INTERVAL", "10")
    monkeypatch.setenv("DURATION", "45")
    assert load_config().duration == 45.0
    assert load_config(duration=15.0).duration == 15.0
