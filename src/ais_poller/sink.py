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

"""Output sinks: raw NDJSON lines, or parsed records streamed into rotated GeoParquet files."""

import json
import logging
import struct
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pyarrow as pa
import pyarrow.parquet as pq

from ais_poller.config import Area, Config
from ais_poller.parse import FIELDS, parse_euris_feature

log = logging.getLogger("ais_poller")

# Rows buffered in memory before they are written out as one Parquet row group.
FLUSH_ROWS = 10_000

SCHEMA = pa.schema(
    [
        ("mmsi", pa.string()),
        ("base_date_time", pa.timestamp("us", tz="UTC")),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("sog", pa.float64()),
        ("cog", pa.float64()),
        ("heading", pa.float64()),
        ("length", pa.float64()),
        ("beam", pa.float64()),
        ("shiptypeAIS", pa.int16()),
        ("status", pa.int8()),
        ("status_label", pa.dictionary(pa.int8(), pa.string())),
        ("vessel_name", pa.string()),
        ("geometry", pa.binary()),
    ],
    # GeoParquet 1.1: marks geometry as WKB points so GDAL/QGIS and DuckDB spatial read a layer.
    # No "crs" key means OGC:CRS84 (lon/lat, WGS 84).
    metadata={
        "geo": json.dumps(
            {
                "version": "1.1.0",
                "primary_column": "geometry",
                "columns": {"geometry": {"encoding": "WKB", "geometry_types": ["Point"]}},
            }
        )
    },
)
assert SCHEMA.names[: len(FIELDS)] == FIELDS


class _ContextSink:
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()

    #: Sibling NDJSON file for exit events, set by the subclasses; None disables writing them.
    events_path: Path | None = None

    def write_exits(self, exits: list[dict[str, Any]], observed_at: datetime, area: str) -> int:
        """Append one JSON line per track that left area's bounding box.

        Events go to a sibling `events/<area>.ndjson`, never into the Parquet point dataset,
        which must keep the schema ais-shader expects. Each line carries the identifiers of
        both sides of a handoff (`track_id` and `mmsi`) plus the time it was seen, so an exit
        from one area joins to the next ping of the same track in a neighbouring area.
        """
        if self.events_path is None or not exits:
            return 0
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = observed_at.astimezone(UTC).isoformat()
        with self.events_path.open("a") as f:
            for event in exits:
                json.dump({"event": "exit", "area": area, "observed_at": stamp, **event}, f)
                f.write("\n")
        return len(exits)

    def close(self) -> None:
        pass


class NdjsonSink(_ContextSink):
    """Append raw features as JSON lines, stamped with the receive time and area."""

    def __init__(self, path: Path, area: str | None = None, events_path: Path | None = None):
        self.path, self.area, self.events_path = path, area, events_path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, features: list[Any], received_at: datetime) -> int:
        stamp = received_at.isoformat()
        with self.path.open("a") as f:
            for record in features:
                record["timestamp"] = stamp
                if self.area is not None:
                    record["area"] = self.area
                json.dump(record, f)
                f.write("\n")
        return len(features)


def _wkb_point(lon: float, lat: float) -> bytes:
    # Little-endian byte order (1), geometry type Point (1), then x/y.
    return struct.pack("<BIdd", 1, 1, lon, lat)


class ParquetSink(_ContextSink):
    """Parse features on arrival and stream them into zstd GeoParquet, one file per window.

    Files go to <root>/year=YYYY/month=MM/day=DD/part-<start>Z.parquet, the Hive layout the
    ais-shader GeoParquet spec expects. Windows are aligned to multiples of
    rotate_s seconds (UTC); a Parquet file is only readable once its footer is written, so the
    open file is a hidden .part-*.tmp that is renamed when the window ends or the sink closes.
    """

    def __init__(
        self,
        root: Path,
        rotate_s: int,
        flush_rows: int = FLUSH_ROWS,
        events_path: Path | None = None,
    ):
        self.root, self.rotate_s, self.flush_rows = root, rotate_s, flush_rows
        self.events_path = events_path
        self._window: tuple[int, str] | None = None
        self._tmp: Path | None = None
        self._final: Path | None = None
        self._writer: pq.ParquetWriter | None = None
        self._buffer: dict[str, list[Any]] = {name: [] for name in SCHEMA.names}
        self._rows = self._skipped = 0

    def write(self, features: list[Any], received_at: datetime) -> int:
        received_at = received_at.astimezone(UTC)
        window = (int(received_at.timestamp()) // self.rotate_s, received_at.date().isoformat())
        if window != self._window:
            self._close_file()
            self._window = window
            self._open_file(received_at)

        written = 0
        stamp = received_at.isoformat()
        for feat in features:
            try:
                record = parse_euris_feature(feat, stamp)
            except (AttributeError, KeyError, TypeError, ValueError, IndexError) as e:
                log.debug("unparseable feature: %s", e)
                record = None
            if record is None:
                self._skipped += 1
                continue
            record["base_date_time"] = received_at
            record["geometry"] = _wkb_point(record["longitude"], record["latitude"])
            for name, column in self._buffer.items():
                column.append(record[name])
            written += 1
        if len(self._buffer["mmsi"]) >= self.flush_rows:
            self._flush()
        return written

    def close(self) -> None:
        self._close_file()
        self._window = None

    def _open_file(self, start: datetime) -> None:
        directory = self.root / f"year={start:%Y}" / f"month={start:%m}" / f"day={start:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        name = f"part-{start:%Y%m%dT%H%M%S}Z"
        # A restart within the same second must not overwrite a finished file.
        suffix = 0
        while (directory / f"{name}.parquet").exists():
            suffix += 1
            name = f"part-{start:%Y%m%dT%H%M%S}Z-{suffix}"
        self._final = directory / f"{name}.parquet"
        self._tmp = directory / f".{name}.parquet.tmp"
        self._rows = self._skipped = 0

    def _flush(self) -> None:
        if not self._buffer["mmsi"]:
            return
        table = pa.Table.from_pydict(self._buffer, schema=SCHEMA)
        if self._writer is None:
            self._writer = pq.ParquetWriter(self._tmp, SCHEMA, compression="zstd")
        self._writer.write_table(table)
        self._rows += table.num_rows
        for column in self._buffer.values():
            column.clear()

    def _close_file(self) -> None:
        if self._tmp is None:
            return
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            self._tmp.replace(self._final)
            log.info("closed %s (%d rows, %d skipped)", self._final, self._rows, self._skipped)
        elif self._skipped:
            log.warning("no rows for %s (%d features skipped)", self._final, self._skipped)
        self._tmp = self._final = None


Sink = NdjsonSink | ParquetSink


def events_path_for(area: Area) -> Path:
    """Where an area's exit events go: a sibling `events/<name>.ndjson`, outside the dataset.

    `output_path` is `<output dir>/<name>.ndjson` for NDJSON and the dataset directory
    `<output dir>/area=<name>` for Parquet, so its parent is the output directory either way.
    """
    return area.output_path.parent / "events" / f"{area.name}.ndjson"


def open_sink(config: Config, area: Area) -> Sink:
    """Return the sink for area's configured output format."""
    events = events_path_for(area)
    if config.format == "parquet":
        return ParquetSink(area.output_path, config.rotate, events_path=events)
    return NdjsonSink(area.output_path, area.name, events_path=events)
