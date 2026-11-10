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

"""Out-of-bounding-box exits and the area handoffs deduced from them."""

import json
from datetime import UTC, datetime, timedelta

from ais_poller.cli import main
from ais_poller.config import Area
from ais_poller.handoff import find_handoffs, load_exits
from ais_poller.poller import _pushed_exits
from ais_poller.sink import NdjsonSink, events_path_for

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)

#: The notification shape assumed for EuRIS: like featureStream, params are feature JSON strings.
EXIT_MESSAGE = {
    "method": "outOfBoundingBox",
    "params": [json.dumps({"properties": {"TrackID": "t-1", "MMSI": 244660000, "Lat": 51.7}})],
}


def test_exit_notification_is_recognised():
    (left,) = _pushed_exits(EXIT_MESSAGE)
    assert (left["track_id"], left["mmsi"], left["method"]) == (
        "t-1",
        244660000,
        "outOfBoundingBox",
    )


def test_other_payload_shapes_still_yield_a_track_id():
    """Bare ids and a params object must not be lost just because the shape differs."""
    assert _pushed_exits({"method": "outOfBoundingBox", "params": ["t-2"]})[0]["track_id"] == "t-2"
    nested = {"method": "OutOfBoundingBox", "params": {"tracks": [{"trackId": "t-3"}]}}
    assert _pushed_exits(nested)[0]["track_id"] == "t-3"
    assert _pushed_exits({"method": "featureStream", "params": []}) is None
    assert _pushed_exits({"result": {"features": []}}) is None


def test_exits_go_to_a_sibling_events_file_not_the_dataset(tmp_path):
    area = Area("north", (51.0, 4.0, 52.0, 5.0), tmp_path / "north.ndjson")
    events = events_path_for(area)
    sink = NdjsonSink(area.output_path, area.name, events_path=events)

    assert sink.write_exits(_pushed_exits(EXIT_MESSAGE), T0, "north") == 1

    assert not area.output_path.exists()  # no exit row in the vessel data
    (line,) = events.read_text().splitlines()
    event = json.loads(line)
    assert event["event"] == "exit"
    assert (event["area"], event["track_id"], event["observed_at"]) == (
        "north",
        "t-1",
        T0.isoformat(),
    )


def _ping(area: str, track: str, when: datetime) -> str:
    return json.dumps(
        {
            "properties": {"TrackID": track, "MMSI": 244660000, "Lat": 51.7, "Lon": 4.3},
            "timestamp": when.isoformat(),
            "area": area,
        }
    )


def _run_dir(tmp_path, *, enter_after: timedelta = timedelta(seconds=30)) -> object:
    """A poller output directory: north's exit event, then the same track pinging in south."""
    root = tmp_path / "data"
    (root / "events").mkdir(parents=True)
    (root / "events" / "north.ndjson").write_text(
        json.dumps(
            {
                "event": "exit",
                "area": "north",
                "observed_at": T0.isoformat(),
                "track_id": "t-1",
                "mmsi": 244660000,
            }
        )
        + "\n"
    )
    (root / "north.ndjson").write_text(_ping("north", "t-1", T0 - timedelta(seconds=10)) + "\n")
    (root / "south.ndjson").write_text(
        _ping("south", "t-1", T0 + enter_after) + "\n" + _ping("south", "t-9", T0) + "\n"
    )
    return root


def test_handoff_is_deduced_across_areas(tmp_path):
    root = _run_dir(tmp_path)
    exits = load_exits(root / "events")

    (handoff,) = find_handoffs(exits, [root / "north.ndjson", root / "south.ndjson"])

    assert (handoff.from_area, handoff.to_area) == ("north", "south")
    assert handoff.gap_s == 30.0
    assert handoff.track_id == "t-1"


def test_ping_outside_the_window_is_not_a_handoff(tmp_path):
    root = _run_dir(tmp_path, enter_after=timedelta(hours=2))
    exits = load_exits(root / "events")
    paths = [root / "north.ndjson", root / "south.ndjson"]

    assert find_handoffs(exits, paths) == []
    assert len(find_handoffs(exits, paths, window_s=3 * 3600)) == 1


def test_handoffs_command(capsys, tmp_path):
    root = _run_dir(tmp_path)

    assert main(["handoffs", str(root)]) == 0
    out = capsys.readouterr().out
    assert "exits: 1  handoffs: 1" in out
    assert "north" in out and "south" in out

    assert main(["handoffs", str(root), "--json"]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["to_area"] == "south"

    assert main(["handoffs", str(tmp_path / "empty")]) == 2
