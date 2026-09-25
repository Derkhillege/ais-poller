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

"""Smoke tests for load_config — happy path and fail-fast error paths."""

from pathlib import Path

import pytest

from ais_poller.config import load_config

TOML = """
url = "wss://example.com/ws"
interval = 10
output_path = "out"

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
    config = load_config(_write(tmp_path, TOML))

    assert config.wss_url == "wss://example.com/ws"
    assert config.interval == 10
    north, south = config.areas
    assert north.output_path == Path("out/north.ndjson")
    assert south.output_path == Path("elsewhere/south.json")
    assert south.bbox == (50.0, 3.0, 51.0, 4.0)


def test_request_for_fills_bbox_and_copies(tmp_path):
    config = load_config(_write(tmp_path, TOML))
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


def test_request_for_supplies_a_missing_id(tmp_path):
    """A [request] without an id still polls: the counter starts at 0 per area."""
    config = load_config(_write(tmp_path, TOML.replace("id = 1\n", "", 1)))
    north, south = config.areas
    assert "id" not in config.request_template
    assert config.request_for(north)["id"] == 0
    assert config.request_for(south)["id"] == 0


def test_request_for_uses_the_area_id(tmp_path):
    config = load_config(
        _write(tmp_path, TOML.replace('name = "north"', 'name = "north"\nid = 42'))
    )
    north, south = config.areas
    assert config.request_for(north)["id"] == 42
    assert config.request_for(south)["id"] == 1  # unset areas keep the template id


def test_toml_overrides(tmp_path):
    config = load_config(
        _write(tmp_path, TOML),
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
        ("interval = 10", 'interval = 10\nformat = "csv"'),
        ("interval = 10", "interval = 10\nrotate = 0"),
    ],
)
def test_toml_invalid(tmp_path, old, new):
    assert old in TOML
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, TOML.replace(old, new, 1)))


def test_format_defaults_to_ndjson(tmp_path):
    config = load_config(_write(tmp_path, TOML))
    assert (config.format, config.rotate) == ("ndjson", 3600)


def test_parquet_format_writes_hive_area_directories(tmp_path):
    text = TOML.replace("interval = 10", 'interval = 10\nformat = "parquet"\nrotate = 600')
    config = load_config(_write(tmp_path, text))
    assert (config.format, config.rotate) == ("parquet", 600)
    north, south = config.areas
    assert north.output_path == Path("out/area=north")
    assert south.output_path == Path("elsewhere/south.json")


def test_format_and_rotate_overrides(tmp_path):
    config = load_config(_write(tmp_path, TOML), fmt="parquet", rotate=60)
    assert (config.format, config.rotate) == ("parquet", 60)
    assert config.areas[0].output_path == Path("out/area=north")
    with pytest.raises(SystemExit, match="rotate"):
        load_config(_write(tmp_path, TOML), rotate=-1)


def test_toml_interval_override_must_be_positive(tmp_path):
    with pytest.raises(SystemExit, match="interval"):
        load_config(_write(tmp_path, TOML), interval=0)


def test_toml_requires_areas(tmp_path):
    text = TOML.split("[[areas]]")[0]
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, text))


def test_toml_missing_or_malformed_file(tmp_path):
    with pytest.raises(SystemExit, match="ais-poller.example.toml"):
        load_config(tmp_path / "nope.toml")
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, "url = "))


def test_example_config_is_valid():
    example = Path(__file__).parent.parent / "ais-poller.example.toml"
    config = load_config(example)
    assert [a.name for a in config.areas] == ["moerdijk", "rotterdam"]
