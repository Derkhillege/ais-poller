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

"""Check a produced dataset against the ais-shader GeoParquet specification.

The rules below are transcribed from `docs/geoparquet_specification.md` in
TUDelft-CITG/ais-colab (sections 1, 2.1, 2.2, 3.1 and 3.2): column names and
types, WGS84 longitude/latitude, the `geo` file metadata, and the Hive
`year=`/`month=`/`day=` partition layout its `detect_hive_partitioning()` reads.

Run it with `ais-poller check <path>`; `tests/test_conformance.py` runs the same
function over a freshly written dataset.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

#: Spec §2.1/§2.2: default column name → (accepted alternatives, type predicate, description).
TIMESTAMP = "timestamp with UTC timezone"
FLOAT = "float"
ID = "string or integer"
CODE = "string or integer"


def _is_timestamp(t: pa.DataType) -> bool:
    return pa.types.is_timestamp(t) and (t.tz or "").upper() in {"UTC", "+00:00"}


def _is_float(t: pa.DataType) -> bool:
    return pa.types.is_floating(t) or pa.types.is_integer(t)


def _is_id(t: pa.DataType) -> bool:
    return pa.types.is_string(t) or pa.types.is_integer(t) or pa.types.is_large_string(t)


def _is_code(t: pa.DataType) -> bool:
    if pa.types.is_dictionary(t):
        return _is_code(t.value_type)
    return _is_id(t)


#: Columns ais-shader needs for trajectorization (spec §2.1), with its accepted aliases.
REQUIRED = {
    "mmsi": (("track_id", "mmsi_hash"), _is_id, ID),
    "base_date_time": (("timestamp", "timestamp_recv"), _is_timestamp, TIMESTAMP),
    "longitude": (("lon", "x"), _is_float, FLOAT),
    "latitude": (("lat", "y"), _is_float, FLOAT),
    "sog": (("speed", "speed_knots"), _is_float, FLOAT),
    "cog": (("course",), _is_float, FLOAT),
}

#: Optional columns (spec §2.2); checked only for type when present.
OPTIONAL = {
    "shiptypeAIS": (("shiptypeais", "VesselType", "vessel_type", "VesselGroup"), _is_code, CODE),
    "draught": (("draft",), _is_float, FLOAT),
    "length": (("vessel_length",), _is_float, FLOAT),
    "beam": (("width", "vessel_width"), _is_float, FLOAT),
    "heading": (("true_heading",), _is_float, FLOAT),
    "status": (("navstatus", "objecttype"), _is_code, CODE),
}

#: Spec §1: accepted geometry column names, in the order ais-shader looks for them.
GEOMETRY_COLUMNS = ("geometry", "geom", "Shape")

#: Spec §3.1: partition keys `detect_hive_partitioning()` maps to dataset fields.
TIME_PARTITIONS = ("year", "month", "day")

#: Spec §2.1 value ranges, plus 511 = "heading not available".
RANGES = {
    "longitude": (-180.0, 180.0),
    "latitude": (-90.0, 90.0),
    "sog": (0.0, 102.2),
    "cog": (0.0, 360.0),
}


@dataclass
class Result:
    """What `check_dataset` found: `problems` empty means the dataset conforms."""

    path: str
    files: int = 0
    rows: int = 0
    problems: list[str] = field(default_factory=list)
    partition_keys: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def parquet_files(path: Path) -> list[Path]:
    """Finished Parquet files of a dataset, ignoring sinks' hidden `.tmp` files."""
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.parquet") if not p.name.startswith("."))


def _column(schema: pa.Schema, default: str, aliases: tuple[str, ...]) -> str | None:
    for name in (default, *aliases):
        if name in schema.names:
            return name
    return None


def _check_schema(schema: pa.Schema, result: Result) -> None:
    for default, (aliases, predicate, wanted) in REQUIRED.items():
        name = _column(schema, default, aliases)
        if name is None:
            result.problems.append(f"missing required column {default!r} (or {', '.join(aliases)})")
        elif not predicate(schema.field(name).type):
            result.problems.append(
                f"column {name!r} is {schema.field(name).type}, spec wants {wanted}"
            )
    for default, (aliases, predicate, wanted) in OPTIONAL.items():
        name = _column(schema, default, aliases)
        if name is not None and not predicate(schema.field(name).type):
            result.problems.append(
                f"column {name!r} is {schema.field(name).type}, spec wants {wanted}"
            )


def _check_geo_metadata(schema: pa.Schema, result: Result) -> None:
    raw = (schema.metadata or {}).get(b"geo")
    if raw is None:
        result.problems.append("no 'geo' file metadata: not a GeoParquet file (spec §3.2)")
        return
    try:
        geo = json.loads(raw)
    except json.JSONDecodeError as e:
        result.problems.append(f"'geo' metadata is not valid JSON: {e}")
        return

    primary = geo.get("primary_column")
    if primary not in GEOMETRY_COLUMNS:
        result.problems.append(
            f"geo.primary_column is {primary!r}, spec wants one of {', '.join(GEOMETRY_COLUMNS)}"
        )
    if primary not in schema.names:
        result.problems.append(f"geo.primary_column {primary!r} is not a column in the file")
        return
    column = (geo.get("columns") or {}).get(primary) or {}
    if column.get("encoding") != "WKB":
        result.problems.append(
            f"geo.columns.{primary}.encoding is {column.get('encoding')!r}, spec wants 'WKB'"
        )
    types = column.get("geometry_types") or []
    if types and "Point" not in types and "MultiPoint" not in types:
        result.problems.append(
            f"geo.columns.{primary}.geometry_types is {types}, raw pings must be Point (spec §1)"
        )
    crs = column.get("crs", "absent")
    # Spec §1 wants EPSG:4326; GeoParquet treats an absent crs as OGC:CRS84, which is that.
    if (
        crs not in ("absent", None)
        and "4326" not in json.dumps(crs)
        and "CRS84" not in json.dumps(crs)
    ):
        result.problems.append("geo crs is neither absent (OGC:CRS84) nor WGS84/EPSG:4326")


def _check_partitioning(root: Path, files: list[Path], result: Result) -> None:
    keys: list[str] = []
    for file in files:
        parts = file.relative_to(root).parts[:-1]
        for part in parts:
            if "=" not in part:
                result.problems.append(
                    f"{file.relative_to(root)}: {part!r} is not a Hive "
                    "'key=value' directory (spec §3.1)"
                )
            elif (key := part.split("=", 1)[0]) not in keys:
                keys.append(key)
    result.partition_keys = tuple(keys)
    missing = [k for k in TIME_PARTITIONS if k not in keys]
    if missing and root.is_dir():
        result.problems.append(
            f"partition keys {keys or ['(none)']} miss {', '.join(missing)}: spec §3.1 wants "
            "year=/month=/day= directories"
        )


def _check_values(table: pa.Table, result: Result) -> None:
    schema = table.schema
    for default, (low, high) in RANGES.items():
        aliases = (REQUIRED | OPTIONAL).get(default, ((), None, ""))[0]
        name = _column(schema, default, aliases)
        if name is None:
            continue
        values = [v for v in table.column(name).to_pylist() if v is not None]
        bad = [v for v in values if not low <= float(v) <= high]
        if bad:
            result.problems.append(
                f"{len(bad)} value(s) of {name!r} outside the spec range [{low}, {high}], "
                f"e.g. {bad[0]}"
            )
    name = _column(schema, "heading", ("true_heading",))
    if name is not None:
        bad = [
            v
            for v in table.column(name).to_pylist()
            if v is not None and not (0.0 <= float(v) < 360.0 or float(v) == 511.0)
        ]
        if bad:
            result.problems.append(
                f"{len(bad)} value(s) of {name!r} are neither in [0, 360) nor 511 "
                f"(not available), e.g. {bad[0]}"
            )


def check_dataset(path: Path, sample_rows: int = 10_000) -> Result:
    """Check one Parquet file or a partitioned dataset directory against the spec."""
    result = Result(str(path))
    if not path.exists():
        result.problems.append(f"no such file or directory: {path}")
        return result

    files = parquet_files(path)
    result.files = len(files)
    if not files:
        result.problems.append(f"no .parquet files under {path} (a sink's open window is a .tmp)")
        return result

    root = path if path.is_dir() else path.parent
    _check_partitioning(root, files, result)

    schema = pq.read_schema(files[0])
    _check_schema(schema, result)
    _check_geo_metadata(schema, result)

    for file in files:
        other = pq.read_schema(file)
        if other.names != schema.names:
            result.problems.append(f"{file.name} has different columns than {files[0].name}")
        result.rows += pq.read_metadata(file).num_rows

    _check_values(pq.read_table(files[0]).slice(0, sample_rows), result)
    return result


def format_result(result: Result) -> str:
    """One readable block per checked dataset."""
    head = (
        f"== {result.path}\n"
        f"files: {result.files}  rows: {result.rows}  "
        f"partitions: {'/'.join(result.partition_keys) or '(none)'}"
    )
    if result.ok:
        return f"{head}\nconforms to the ais-shader GeoParquet specification"
    problems = "\n".join(f"  - {p}" for p in result.problems)
    return f"{head}\n{len(result.problems)} problem(s):\n{problems}"
