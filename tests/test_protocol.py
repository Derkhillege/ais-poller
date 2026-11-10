# ais-poller - For polling sockets and parsing them into flat vessel records.
# Copyright (C) 2026  Derk Hillege
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The message catalogue: evidence of what the EuRIS socket actually sends."""

import json
from datetime import UTC, datetime

from ais_poller.cli import main
from ais_poller.protocol import (
    SAMPLE_CHARS,
    Catalog,
    catalog_path_for,
    format_catalog,
    load,
    merge,
    message_signature,
    to_markdown,
)

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def test_signatures_group_by_shape_not_content():
    push = {"method": "featureStream", "params": ['{"a": 1}']}
    other_push = {"method": "featureStream", "params": ['{"b": 2}', '{"c": 3}']}
    assert message_signature(push) == message_signature(other_push)
    assert message_signature(push) == "notification:featureStream(params[str])"
    assert message_signature({"result": {"features": []}}) == "result{features}"
    assert message_signature({"method": "outOfBoundingBox", "params": ["t-1"]}) == (
        "notification:outOfBoundingBox(params[str])"
    )
    assert message_signature({"error": {"code": -1}}) == "error"
    assert message_signature([1, 2]) == "non-object:list"


def test_catalog_counts_kinds_and_keeps_one_sample():
    catalog = Catalog(area="north", url="wss://example/ws")
    first = json.dumps({"method": "featureStream", "params": ['{"id": 1}']})
    second = json.dumps({"method": "featureStream", "params": ['{"id": 2}']})
    catalog.record(first, json.loads(first), T0)
    catalog.record(second, json.loads(second), T0.replace(minute=5))
    catalog.record('{"error": {"code": -1}}', {"error": {"code": -1}}, T0.replace(minute=9))

    assert catalog.messages == 3
    push = catalog.kinds["notification:featureStream(params[str])"]
    assert push.count == 2
    assert push.sample == first  # the first message of its kind, not the latest
    assert (push.first_seen, push.last_seen) == (T0.isoformat(), T0.replace(minute=5).isoformat())
    assert set(catalog.kinds) == {"notification:featureStream(params[str])", "error"}


def test_long_messages_are_truncated():
    catalog = Catalog()
    raw = json.dumps({"method": "featureStream", "params": ["x" * (SAMPLE_CHARS * 2)]})
    catalog.record(raw, json.loads(raw), T0)
    (kind,) = catalog.kinds.values()
    assert kind.truncated and len(kind.sample) == SAMPLE_CHARS


def test_write_read_and_merge(tmp_path):
    north = Catalog(area="north", url="wss://example/ws")
    north.record('{"result": {"features": []}}', {"result": {"features": []}}, T0)
    south = Catalog(area="south")
    south.record('{"result": {"features": []}}', {"result": {"features": []}}, T0.replace(hour=9))
    south.record('{"method": "ping", "params": null}', {"method": "ping", "params": None}, T0)

    north.write(tmp_path / "protocol" / "north.json")
    south.write(tmp_path / "protocol" / "south.json")
    merged = merge(load([tmp_path / "protocol"]))

    assert merged.area == "north, south"
    assert merged.kinds["result{features}"].count == 2
    assert merged.kinds["result{features}"].first_seen == T0.replace(hour=9).isoformat()
    assert merged.messages == 3


def test_catalog_path_is_a_sibling_of_the_data(tmp_path):
    assert catalog_path_for(tmp_path / "data" / "north.ndjson", "north") == (
        tmp_path / "data" / "protocol" / "north.json"
    )
    assert catalog_path_for(tmp_path / "data" / "area=north", "north") == (
        tmp_path / "data" / "protocol" / "north.json"
    )


def test_stream_records_every_kind_it_receives(tmp_path, monkeypatch):
    """A streaming run must leave behind the evidence for every message kind it saw."""
    import threading

    from ais_poller import poller
    from ais_poller.config import Area, Config

    area = Area("north", (51.0, 4.0, 52.0, 5.0), tmp_path / "data" / "north.ndjson")
    config = Config("wss://example/ws", 10, {"id": 1, "method": "StreamFeatures"}, (area,))
    stop = threading.Event()
    messages = [
        json.dumps({"result": {"features": []}}),
        json.dumps({"method": "featureStream", "params": [json.dumps({"properties": {}})]}),
        json.dumps({"method": "outOfBoundingBox", "params": ["t-1"]}),
        json.dumps({"method": "somethingNew", "params": {"a": 1}}),
    ]

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send(self, _message):
            pass

        def recv(self, timeout=None):
            if not messages:
                stop.set()
                raise TimeoutError
            return messages.pop(0)

    monkeypatch.setattr(poller, "connect", lambda _url: Socket())
    poller.stream_area(config, area, stop)

    catalog = Catalog.read(catalog_path_for(area.output_path, area.name))
    assert set(catalog.kinds) == {
        "result{features}",
        "notification:featureStream(params[str])",
        "notification:outOfBoundingBox(params[str])",
        "notification:somethingNew(params{a})",
    }
    assert catalog.url == "wss://example/ws"


def test_protocol_command(capsys, tmp_path):
    catalog = Catalog(area="north")
    catalog.record('{"result": {"features": []}}', {"result": {"features": []}}, T0)
    catalog.write(tmp_path / "protocol" / "north.json")

    assert main(["protocol", str(tmp_path / "protocol")]) == 0
    assert "result{features}" in capsys.readouterr().out

    assert main(["protocol", str(tmp_path / "protocol"), "--markdown"]) == 0
    out = capsys.readouterr().out
    assert "| Messages | First seen | Last seen | Signature |" in out
    assert "```json" in out

    assert main(["protocol", str(tmp_path / "nope")]) == 2


def test_markdown_is_pasteable_without_a_run():
    assert "no messages recorded" in format_catalog(Catalog())
    assert "Messages recorded: 0" in to_markdown(Catalog())
