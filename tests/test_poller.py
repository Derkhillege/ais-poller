"""Tests for poll_once using a fake websocket."""

import json
import threading
from datetime import datetime
from pathlib import Path

from ais_poller import poller
from ais_poller.config import Area, Config
from ais_poller.poller import poll_area, poll_once


class FakeWebSocket:
    def __init__(self, response: str):
        self.response = response
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self) -> str:
        return self.response


def test_poll_once_appends_stamped_features(tmp_path):
    out = tmp_path / "out.json"
    out.write_text('{"existing": true}\n')
    features = [{"id": "1"}, {"id": "2"}]
    ws = FakeWebSocket(json.dumps({"result": {"features": features}}))

    n = poll_once(ws, {"id": 7}, out)

    assert n == 2
    assert json.loads(ws.sent[0]) == {"id": 7}
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines[0] == {"existing": True}
    assert [r["id"] for r in lines[1:]] == ["1", "2"]
    assert all(datetime.fromisoformat(r["requested_at"]).tzinfo for r in lines[1:])


def test_poll_once_without_features_writes_nothing(tmp_path):
    out = tmp_path / "out.json"
    assert poll_once(FakeWebSocket('{"jsonrpc": "2.0"}'), {"id": 1}, out) == 0
    assert out.read_text() == ""


def test_poll_once_tags_area(tmp_path):
    out = tmp_path / "out.json"
    ws = FakeWebSocket(json.dumps({"result": {"features": [{"id": "1"}]}}))
    poll_once(ws, {"id": 1}, out, area="north")
    assert json.loads(out.read_text())["area"] == "north"


class ScriptedWebSocket(FakeWebSocket):
    """Fake socket that stops the poller after a fixed number of ticks."""

    def __init__(self, stop: threading.Event, ticks: int):
        super().__init__(json.dumps({"result": {"features": [{"id": "x"}]}}))
        self.stop, self.ticks = stop, ticks

    def recv(self) -> str:
        if len(self.sent) >= self.ticks:
            self.stop.set()
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_areas_poll_independently(tmp_path, monkeypatch):
    """Each area uses its own socket, bbox, id counter and output file."""
    areas = (
        Area("north", (51.0, 4.0, 52.0, 5.0), tmp_path / "north.ndjson"),
        Area("south", (50.0, 3.0, 51.0, 4.0), tmp_path / "south.ndjson"),
    )
    config = Config("wss://example/ws", 0, {"id": 1, "params": {"filters": {}}}, areas)
    sockets: dict[str, ScriptedWebSocket] = {}
    stops = {a.name: threading.Event() for a in areas}

    def fake_connect(_url):
        name = threading.current_thread().name
        sockets[name] = ScriptedWebSocket(stops[name], ticks=3)
        return sockets[name]

    monkeypatch.setattr(poller, "connect", fake_connect)
    threads = [
        threading.Thread(target=poll_area, args=(config, a, stops[a.name]), name=a.name)
        for a in areas
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    for area in areas:
        sent = [json.loads(m) for m in sockets[area.name].sent]
        assert [m["id"] for m in sent] == [1, 2, 3]
        min_lat, min_lon, max_lat, max_lon = area.bbox
        assert sent[0]["params"]["filters"]["boundingBox"] == {
            "minLat": min_lat,
            "minLon": min_lon,
            "maxLat": max_lat,
            "maxLon": max_lon,
        }
        lines = [json.loads(line) for line in Path(area.output_path).read_text().splitlines()]
        assert len(lines) == 3
        assert {r["area"] for r in lines} == {area.name}
    assert config.request_template["id"] == 1
