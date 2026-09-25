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

"""Tests for the NDJSON and GeoParquet output sinks."""

import json
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from ais_poller.parse import iter_records
from ais_poller.sink import SCHEMA, NdjsonSink, ParquetSink

REPO_ROOT = Path(__file__).parent.parent
T0 = datetime(2026, 9, 26, 1, 19, 7, 507389, tzinfo=UTC)


def _feature(track: str, lat: float = 51.7, lon: float = 4.3) -> dict:
    return {
        "type": "Feature",
        "id": track,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"Lat": lat, "Lon": lon, "TrackID": track, "MMSI": "0", "ST": 5},
    }


def _parquet_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.parquet"))


def test_ndjson_sink_stamps_and_appends(tmp_path):
    out = tmp_path / "sub" / "north.ndjson"
    with NdjsonSink(out, "north") as sink:
        assert sink.write([{"id": "1"}, {"id": "2"}], T0) == 2
        sink.write([{"id": "3"}], T0 + timedelta(seconds=1))

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines[0] == {"id": "1", "requested_at": T0.isoformat(), "area": "north"}
    assert [r["id"] for r in lines] == ["1", "2", "3"]


def test_parquet_round_trip_as_hive_dataset(tmp_path):
    root = tmp_path / "area=north"
    with ParquetSink(root, 3600, flush_rows=1) as sink:
        assert sink.write([_feature("a"), _feature("b", 51.8, 4.4)], T0) == 2
        # Hidden while open: dataset readers must not pick up a file without a footer.
        assert _parquet_files(root) == []
        assert len(list(root.rglob(".part-*.parquet.tmp"))) == 1

    assert list(root.rglob("*.tmp")) == []
    (path,) = _parquet_files(root)
    assert path.relative_to(root) == Path("date=2026-09-26/part-20260926T011907Z.parquet")

    table = ds.dataset(tmp_path, format="parquet", partitioning="hive").to_table()
    assert table.schema.remove_metadata().names == [*SCHEMA.names, "area", "date"]
    rows = table.to_pylist()
    assert [r["mmsi"] for r in rows] == ["a", "b"]
    assert rows[0]["base_date_time"] == T0
    assert rows[0]["status_label"] == "Moored"
    assert {r["area"] for r in rows} == {"north"}
    assert struct.unpack("<BIdd", rows[1]["geometry"]) == (1, 1, 4.4, 51.8)


def test_parquet_schema_carries_geoparquet_metadata(tmp_path):
    with ParquetSink(tmp_path, 3600) as sink:
        sink.write([_feature("a")], T0)
    (path,) = _parquet_files(tmp_path)
    schema = pq.read_schema(path)
    assert schema.remove_metadata() == SCHEMA.remove_metadata()
    geo = json.loads(schema.metadata[b"geo"])
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"] == {"encoding": "WKB", "geometry_types": ["Point"]}


def test_parquet_rotates_per_window_and_date(tmp_path):
    before_midnight = datetime(2026, 9, 26, 23, 59, 30, tzinfo=UTC)
    with ParquetSink(tmp_path, 3600) as sink:
        sink.write([_feature("a")], datetime(2026, 9, 26, 22, 10, tzinfo=UTC))
        sink.write([_feature("b")], datetime(2026, 9, 26, 22, 50, tzinfo=UTC))
        sink.write([_feature("c")], before_midnight)
        sink.write([_feature("d")], before_midnight + timedelta(minutes=1))

    files = [p.relative_to(tmp_path).as_posix() for p in _parquet_files(tmp_path)]
    assert files == [
        "date=2026-09-26/part-20260926T221000Z.parquet",
        "date=2026-09-26/part-20260926T235930Z.parquet",
        "date=2026-09-27/part-20260927T000030Z.parquet",
    ]
    counts = [pq.read_metadata(tmp_path / f).num_rows for f in files]
    assert counts == [2, 1, 1]


def test_parquet_restart_in_same_second_does_not_overwrite(tmp_path):
    for track in ("a", "b"):
        with ParquetSink(tmp_path, 3600) as sink:
            sink.write([_feature(track)], T0)
    names = [p.name for p in _parquet_files(tmp_path)]
    assert names == ["part-20260926T011907Z-1.parquet", "part-20260926T011907Z.parquet"]


def test_parquet_flushes_row_groups(tmp_path):
    with ParquetSink(tmp_path, 3600, flush_rows=2) as sink:
        for i in range(5):
            sink.write([_feature(str(i))], T0 + timedelta(seconds=i))
    (path,) = _parquet_files(tmp_path)
    meta = pq.read_metadata(path)
    assert (meta.num_rows, meta.num_row_groups) == (5, 3)


def test_parquet_skips_unparseable_features(tmp_path):
    bad = [{"properties": {"Lat": 51.0}}, {"properties": {"Lat": 51, "Lon": 4, "SOG": "x"}}]
    with ParquetSink(tmp_path, 3600) as sink:
        assert sink.write([*bad, _feature("a"), "not a feature"], T0) == 1
    (path,) = _parquet_files(tmp_path)
    assert pq.read_table(path).column("mmsi").to_pylist() == ["a"]


def test_parquet_without_rows_writes_no_file(tmp_path):
    with ParquetSink(tmp_path, 3600) as sink:
        sink.write([], T0)
    assert list(tmp_path.rglob("*")) == [tmp_path / "date=2026-09-26"]


def _replay(ndjson: Path, root: Path) -> None:
    """Write a poller NDJSON file's features through a ParquetSink, tick by tick."""
    with ParquetSink(root, 3600) as sink, ndjson.open() as f:
        for line in f:
            feat = json.loads(line)
            sink.write([feat], datetime.fromisoformat(feat["requested_at"]))


def test_parquet_matches_parse_records(tmp_path, sample_ndjson):
    _replay(sample_ndjson, tmp_path)
    table = ds.dataset(tmp_path, format="parquet", partitioning="hive").to_table()
    expected = list(iter_records(sample_ndjson))
    rows = table.drop_columns(["geometry", "date"]).to_pylist()
    for row in rows:
        row["base_date_time"] = row["base_date_time"].isoformat()
    assert rows == expected


@pytest.mark.slow
def test_parquet_full_dataset(tmp_path):
    path = REPO_ROOT / "data.json"
    if not path.exists():
        pytest.skip("data.json test set not present")
    _replay(path, tmp_path)
    table = ds.dataset(tmp_path, format="parquet", partitioning="hive").to_table()
    assert table.num_rows == sum(1 for _ in iter_records(path)) == 47794
