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

"""End-to-end tests for the ais-poller CLI."""

import csv
import json

import pytest

from ais_poller import __version__
from ais_poller.cli import main
from ais_poller.parse import FIELDS


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parse_to_csv(tmp_path, sample_ndjson):
    out = tmp_path / "sub" / "out.csv"
    assert main(["parse", str(sample_ndjson), "-o", str(out)]) == 0
    with out.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == FIELDS
    assert len(rows) == 727


def test_parse_to_ndjson_stdout(capsys, sample_ndjson):
    assert main(["parse", str(sample_ndjson), "--format", "ndjson"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 727
    assert list(json.loads(lines[0])) == FIELDS


def test_parse_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        main(["parse", str(tmp_path / "nope.json")])
