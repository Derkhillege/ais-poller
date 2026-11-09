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

"""Conformance of the Parquet output with the ais-shader GeoParquet specification.

The spec lives in `docs/geoparquet_specification.md` of TUDelft-CITG/ais-colab;
`ais_poller.conformance` transcribes its rules and `ais-poller check` runs them.
"""

import json
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from ais_poller.cli import main
from ais_poller.conformance import TIME_PARTITIONS, check_dataset, parquet_files
from ais_poller.sink import ParquetSink

T0 = datetime(2026, 9, 26, 1, 19, 7, tzinfo=UTC)


def _feature(track: str, lat: float = 51.7, lon: float = 4.3) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "TrackID": track,
            "MMSI": 244660000,
            "Lat": lat,
            "Lon": lon,
            "Sog": 42,  # 0.1 kn units in the EuRIS feed
            "Cog": 1800,
            "Heading": 180,
            "DimA": 40.0,
            "DimB": 40.0,
            "DimC": 5.0,
            "DimD": 5.0,
            "ShiptypeAIS": 80,
            "Status": 0,
            "Name": "TEST VESSEL",
        },
    }


@pytest.fixture
def dataset(tmp_path):
    """A small two-window Parquet dataset, written the way `poll --format parquet` writes it."""
    root = tmp_path / "area=north"
    with ParquetSink(root, rotate_s=3600) as sink:
        sink.write([_feature("a"), _feature("b", 51.8, 4.4)], T0)
        sink.write([_feature("c", 51.9, 4.5)], T0.replace(hour=3))
    return root


def test_written_dataset_conforms(dataset):
    result = check_dataset(dataset)
    assert result.problems == []
    assert (result.files, result.rows) == (2, 3)


def test_hive_layout_is_year_month_day(dataset):
    paths = [p.relative_to(dataset).parts for p in parquet_files(dataset)]
    assert all([part.split("=")[0] for part in p[:-1]] == list(TIME_PARTITIONS) for p in paths)


def test_pyarrow_reads_it_as_a_partitioned_dataset(dataset):
    """What ais-shader's detect_hive_partitioning() + dask_geopandas do with the directory."""
    dataset_obj = ds.dataset(dataset, format="parquet", partitioning="hive")
    table = dataset_obj.to_table()
    assert {"year", "month", "day"} <= set(table.schema.names)
    assert table.num_rows == 3
    assert {str(v) for v in table.column("day").to_pylist()} == {"26"}


def test_geoparquet_metadata_and_wkb(dataset):
    schema = pq.read_schema(parquet_files(dataset)[0])
    geo = json.loads(schema.metadata[b"geo"])
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["encoding"] == "WKB"
    assert geo["columns"]["geometry"]["geometry_types"] == ["Point"]
    # Little-endian Point WKB: 1 byte order + 4 type + 2 doubles.
    wkb = pq.read_table(parquet_files(dataset)[0]).column("geometry")[0].as_py()
    assert len(wkb) == 21 and wkb[0] == 1 and wkb[1] == 1


def test_required_columns_and_types(dataset):
    schema = pq.read_schema(parquet_files(dataset)[0])
    assert schema.field("base_date_time").type == pa.timestamp("us", tz="UTC")
    for name in ("longitude", "latitude", "sog", "cog"):
        assert pa.types.is_floating(schema.field(name).type), name
    assert pa.types.is_string(schema.field("mmsi").type)


def test_check_detects_a_flat_dataset(tmp_path, dataset):
    """Without year=/month=/day= directories the spec's partition discovery fails."""
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "part-0.parquet").write_bytes(parquet_files(dataset)[0].read_bytes())

    result = check_dataset(flat)

    assert not result.ok
    assert any("year" in p and "§3.1" in p for p in result.problems)


def test_check_detects_missing_geo_metadata_and_bad_values(tmp_path):
    bad = tmp_path / "year=2026" / "month=09" / "day=26"
    bad.mkdir(parents=True)
    table = pa.table(
        {
            "mmsi": ["1"],
            "base_date_time": pa.array([T0], pa.timestamp("us", tz="UTC")),
            "longitude": [4.3],
            "latitude": [51.7],
            "sog": [999.0],  # outside the spec's [0, 102.2]
            "cog": [10.0],
        }
    )
    pq.write_table(table, bad / "part-0.parquet")

    result = check_dataset(tmp_path)

    assert not result.ok
    assert any("'geo' file metadata" in p for p in result.problems)
    assert any("'sog' outside the spec range" in p for p in result.problems)


def test_check_command_exit_codes(capsys, dataset, tmp_path):
    assert main(["check", str(dataset)]) == 0
    assert "conforms" in capsys.readouterr().out
    assert main(["check", str(tmp_path / "nope")]) == 1
    assert "no such file" in capsys.readouterr().out
