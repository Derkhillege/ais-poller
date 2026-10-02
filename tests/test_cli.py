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
