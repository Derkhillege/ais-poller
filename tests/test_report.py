"""Tests for the inspect report."""

import json

from ais_poller.cli import main
from ais_poller.report import inspect_file


def _line(ts, area="north", track="1", lat=51.7, lon=4.3):
    feat = {
        "properties": {"TrackID": track, "Lat": lat, "Lon": lon},
        "requested_at": ts,
        "area": area,
    }
    return json.dumps(feat) + "\n"


def test_inspect_fixture(sample_ndjson):
    r = inspect_file(sample_ndjson)
    assert (r.records, r.ticks, r.bad_lines) == (727, 3, 0)
    assert r.gaps == []
    assert r.records_per_tick == {"min": 242, "median": 242, "max": 243}


def test_inspect_detects_gap_areas_and_bad_lines(tmp_path):
    path = tmp_path / "x.ndjson"
    times = [
        "2026-05-01T12:00:00+00:00",
        "2026-05-01T12:00:10+00:00",
        "2026-05-01T12:00:20+00:00",
        "2026-05-01T12:10:00+00:00",
    ]
    path.write_text(
        _line(times[0], track="a", lat=51.0)
        + _line(times[0], track="b", lat=52.0)
        + _line(times[1], area="south")
        + "garbage\n"
        + _line(times[2])
        + _line(times[3])
    )
    r = inspect_file(path)
    assert (r.records, r.ticks, r.bad_lines, r.vessels) == (5, 4, 1, 3)
    assert r.areas == {"north": 4, "south": 1}
    assert r.median_interval_s == 10.0
    assert [(g.after, g.before, g.seconds) for g in r.gaps] == [(times[2], times[3], 580.0)]
    assert r.extent["minLat"] == 51.0 and r.extent["maxLat"] == 52.0


def test_inspect_cli_json(capsys, sample_ndjson):
    assert main(["inspect", "--json", str(sample_ndjson)]) == 0
    (report,) = json.loads(capsys.readouterr().out)
    assert report["records"] == 727
