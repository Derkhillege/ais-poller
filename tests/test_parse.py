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

"""Tests for parse_euris_feature and the streaming NDJSON parser."""

import io
import json
from pathlib import Path

import pytest

from ais_poller.parse import FIELDS, iter_records, parse_euris_feature, parse_file

REPO_ROOT = Path(__file__).parent.parent

NOW = "2026-05-01T13:05:15+00:00"


def _feature(**props):
    base = {
        "Lat": 51.7,
        "Lon": 4.3,
        "MMSI": "244123456",
        "TrackID": "140509",
        "Name": "Vessel",
        "COG": 97.4,
        "SOG": 3.2,
        "TH": 90.0,
        "ST": 5,
        "VT": 80,
        "DimA": 4,
        "DimB": 6,
        "DimC": 3,
        "DimD": 1,
    }
    base.update(props)
    return {
        "type": "Feature",
        "id": "1",
        "geometry": {"coordinates": [4.3, 51.7]},
        "properties": base,
    }


def test_parse_happy_path():
    r = parse_euris_feature(_feature(), NOW)
    assert r["mmsi"] == "244123456"
    assert r["base_date_time"] == NOW
    assert (r["latitude"], r["longitude"]) == (51.7, 4.3)
    assert r["heading"] == 90.0
    assert (r["length"], r["beam"]) == (10.0, 4.0)
    assert r["shiptypeAIS"] == 80
    assert (r["status"], r["status_label"]) == (5, "Moored")
    assert list(r) == FIELDS


def test_mmsi_zero_falls_back_to_track_id():
    assert parse_euris_feature(_feature(MMSI="0"), NOW)["mmsi"] == "140509"


def test_no_identifier_returns_none():
    feat = _feature(MMSI="0", TrackID=None)
    feat["id"] = None
    assert parse_euris_feature(feat, NOW) is None


def test_missing_coordinates_returns_none():
    feat = _feature(Lat=None)
    assert parse_euris_feature(feat, NOW) is None


def test_coordinates_fall_back_to_geometry():
    feat = _feature()
    del feat["properties"]["Lat"], feat["properties"]["Lon"]
    r = parse_euris_feature(feat, NOW)
    assert (r["latitude"], r["longitude"]) == (51.7, 4.3)


def test_zero_dimensions_become_none():
    r = parse_euris_feature(_feature(DimA=0, DimB=0, DimC=None, DimD=0), NOW)
    assert r["length"] is None and r["beam"] is None


def test_zero_true_heading_falls_back_to_cog():
    assert parse_euris_feature(_feature(TH=0), NOW)["heading"] == 97.4


def test_status_zero_is_kept():
    r = parse_euris_feature(_feature(ST=0), NOW)
    assert (r["status"], r["status_label"]) == (0, "Under way using engine")


def test_missing_status_is_undefined():
    r = parse_euris_feature(_feature(ST=None), NOW)
    assert (r["status"], r["status_label"]) == (15, "Undefined")


def test_iter_records_on_fixture(sample_ndjson):
    records = list(iter_records(sample_ndjson))
    raw = [json.loads(line) for line in sample_ndjson.open()]
    assert len(records) == len(raw) == 727
    assert all(list(r) == FIELDS for r in records)
    assert [r["base_date_time"] for r in records] == [f["requested_at"] for f in raw]


def test_iter_records_fails_on_bad_lines(tmp_path, sample_ndjson):
    good = sample_ndjson.open().readline()
    path_bad_json = tmp_path / "bad_json.ndjson"
    path_bad_json.write_text(f"{good}not json\n")
    with pytest.raises(json.JSONDecodeError):
        list(iter_records(path_bad_json))

    path_no_ts = tmp_path / "no_ts.ndjson"
    path_no_ts.write_text(f"{good}{json.dumps(_feature())}\n")
    with pytest.raises(KeyError):
        list(iter_records(path_no_ts))


def test_parse_file_rejects_unknown_format(sample_ndjson):
    with pytest.raises(ValueError):
        parse_file(sample_ndjson, io.StringIO(), "xml")


@pytest.mark.slow
def test_full_dataset():
    path = REPO_ROOT / "data.json"
    if not path.exists():
        pytest.skip("data.json test set not present")
    assert sum(1 for _ in iter_records(path)) == 47794
