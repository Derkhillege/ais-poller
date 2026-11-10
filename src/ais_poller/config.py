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

import copy
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AREA_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
FORMATS = ("ndjson", "parquet")


@dataclass(frozen=True)
class Area:
    """One polled region; each area gets its own socket, request id counter and output file."""

    name: str
    bbox: tuple[float, float, float, float]  # (minLat, minLon, maxLat, maxLon)
    output_path: Path  # NDJSON file, or the Parquet dataset directory
    id: int | None = None


@dataclass(frozen=True)
class Config:
    wss_url: str
    interval: int
    request_template: dict[str, Any]
    areas: tuple[Area, ...]
    format: str = "ndjson"
    rotate: int = 3600  # seconds per Parquet file
    duration: float | None = None

    @property
    def streaming(self) -> bool:
        """StreamFeatures subscribes once per connection and the server pushes messages."""
        return self.request_template.get("method") == "StreamFeatures"

    def request_for(self, area: Area) -> dict[str, Any]:
        """Return a fresh request dict for area with its bounding box filled in.

        Each area's poller mutates its own copy's "id" in place, so the counters stay independent.
        """
        request = copy.deepcopy(self.request_template)
        if area.id is not None:
            request["id"] = area.id
        elif request.get("id") is None:
            # [request] carries no id: EuRIS tracks requests by it, so start this area's at 0.
            request["id"] = 0
        min_lat, min_lon, max_lat, max_lon = area.bbox
        filters = request.setdefault("params", {}).setdefault("filters", {})
        filters["boundingBox"] = {
            "minLat": min_lat,
            "minLon": min_lon,
            "maxLat": max_lat,
            "maxLon": max_lon,
        }
        return request


def load_config(
    path: Path,
    *,
    wss_url: str | None = None,
    interval: int | None = None,
    output_path: Path | None = None,
    fmt: str | None = None,
    rotate: int | None = None,
    duration: float | None = None,
) -> Config:
    """Load and validate a TOML config; explicit arguments (CLI flags) override file values."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise SystemExit(
            f"config file not found: {path} (copy ais-poller.example.toml to get started)"
        ) from None
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"invalid config file {path}: {e}") from e

    wss_url = wss_url or data.get("url")
    if not isinstance(wss_url, str) or not wss_url:
        raise SystemExit(f"{path}: 'url' (or --url) must be a non-empty string")

    interval = interval if interval is not None else data.get("interval")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        raise SystemExit(f"{path}: 'interval' (or --interval) must be a positive integer")

    request = data.get("request")
    if not isinstance(request, dict) or not request:
        raise SystemExit(f"{path}: a [request] table is required")

    request_id = request.get("id")
    if request_id is not None and (not isinstance(request_id, int) or isinstance(request_id, bool)):
        raise SystemExit(f"{path}: 'request.id' must be an integer (or omitted)")

    output_path = output_path or Path(data.get("output_path", "."))
    fmt = fmt or data.get("format", "ndjson")
    if fmt not in FORMATS:
        raise SystemExit(f"{path}: 'format' (or --format) must be one of {', '.join(FORMATS)}")

    rotate = rotate if rotate is not None else data.get("rotate", 3600)
    if not isinstance(rotate, int) or isinstance(rotate, bool) or rotate <= 0:
        raise SystemExit(f"{path}: 'rotate' (or --rotate) must be a positive integer")

    duration = duration if duration is not None else data.get("duration")
    if duration is not None and (
        not isinstance(duration, int | float) or isinstance(duration, bool) or duration <= 0
    ):
        raise SystemExit(
            f"{path}: 'duration' (or --duration) must be a positive number of seconds (or omitted)"
        )

    raw_areas = data.get("areas")
    if not isinstance(raw_areas, list) or not raw_areas:
        raise SystemExit(f"{path}: at least one [[areas]] entry is required")

    areas: list[Area] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_areas):
        where = f"{path}: areas[{i}]"
        name = raw.get("name")
        if not isinstance(name, str) or not _AREA_NAME.match(name):
            raise SystemExit(f"{where}: 'name' must match [A-Za-z0-9_-]+")
        if name in seen:
            raise SystemExit(f"{where}: duplicate area name {name!r}")
        seen.add(name)

        area_id = raw.get("id")
        if area_id is not None and (not isinstance(area_id, int) or isinstance(area_id, bool)):
            raise SystemExit(f"{where}: 'id' must be an integer (or omitted)")

        if "output" in raw:
            output = Path(raw["output"])
        elif fmt == "parquet":
            output = output_path / f"area={name}"
        else:
            output = output_path / f"{name}.ndjson"
        areas.append(Area(name, _check_bbox(raw.get("bbox"), where), output, id=area_id))

    outputs = [a.output_path.resolve() for a in areas]
    if len(set(outputs)) != len(outputs):
        raise SystemExit(f"{path}: areas must write to distinct outputs")

    return Config(
        wss_url,
        interval,
        request,
        tuple(areas),
        fmt,
        rotate,
        duration=float(duration) if duration is not None else None,
    )


def _check_bbox(bbox: Any, where: str) -> tuple[float, float, float, float]:
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in bbox)
    ):
        raise SystemExit(f"{where}: 'bbox' must be [minLat, minLon, maxLat, maxLon]")
    min_lat, min_lon, max_lat, max_lon = (float(v) for v in bbox)
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise SystemExit(
            f"{where}: 'bbox' needs -90 <= minLat < maxLat <= 90 and -180 <= minLon < maxLon <= 180"
        )
    return min_lat, min_lon, max_lat, max_lon
