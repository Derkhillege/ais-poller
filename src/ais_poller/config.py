import copy
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_AREA_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Area:
    """One polled region; each area gets its own socket, request id counter and output file."""

    name: str
    # (minLat, minLon, maxLat, maxLon), or None to send the request template unchanged.
    bbox: tuple[float, float, float, float] | None
    output_path: Path
    id: int | None = None


@dataclass(frozen=True)
class Config:
    wss_url: str
    interval: int
    request_template: dict[str, Any]
    areas: tuple[Area, ...]
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
        if area.bbox is not None:
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
    env_file: Path | None = None,
    *,
    config_file: Path | None = None,
    wss_url: str | None = None,
    interval: int | None = None,
    output_path: Path | None = None,
    duration: float | None = None,
) -> Config:
    """Load and validate configuration; explicit arguments override file and env values.

    With config_file, areas come from a TOML file and output_path is the output directory.
    Without it, env vars describe a single area and output_path is the NDJSON file.
    """
    if config_file is not None:
        return _load_toml(
            config_file,
            wss_url=wss_url,
            interval=interval,
            output_dir=output_path,
            duration=duration,
        )
    return _load_env(
        env_file,
        wss_url=wss_url,
        interval=interval,
        output_path=output_path,
        duration=duration,
    )


def _load_env(
    env_file: Path | None,
    *,
    wss_url: str | None,
    interval: int | None,
    output_path: Path | None,
    duration: float | None,
) -> Config:
    load_dotenv(env_file)
    wss_url = wss_url or os.environ["EURIS_WSS"]
    request_params = json.loads(os.environ["REQUEST_PARAMS"])
    interval = interval if interval is not None else int(os.environ["INTERVAL"])
    if duration is None and "DURATION" in os.environ:
        duration = float(os.environ["DURATION"])

    _check_request(request_params, "REQUEST_PARAMS")
    output_path = output_path or Path(os.getenv("OUTPUT_PATH", "data.json"))
    # The bounding box stays inside REQUEST_PARAMS, so the request is sent exactly as configured.
    area = Area(name="default", bbox=None, output_path=output_path)
    return Config(wss_url, interval, request_params, (area,), duration=duration)


def _load_toml(
    path: Path,
    *,
    wss_url: str | None,
    interval: int | None,
    output_dir: Path | None,
    duration: float | None,
) -> Config:
    with path.open("rb") as f:
        data = tomllib.load(f)

    wss_url = wss_url or data.get("url")
    if not isinstance(wss_url, str) or not wss_url:
        raise ValueError(f"{path}: 'url' must be a non-empty string")

    interval = interval if interval is not None else data.get("interval")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        raise ValueError(f"{path}: 'interval' must be a positive integer")

    raw_duration = data.get("duration")
    if duration is None and raw_duration is not None:
        if (
            not isinstance(raw_duration, (int, float))
            or isinstance(raw_duration, bool)
            or raw_duration <= 0
        ):
            raise ValueError(f"{path}: 'duration' must be a positive number")
        duration = float(raw_duration)

    request = data.get("request")
    _check_request(request, f"{path}: [request]")

    output_dir = output_dir or Path(data.get("output_dir", "."))
    raw_areas = data.get("areas")
    if not isinstance(raw_areas, list) or not raw_areas:
        raise ValueError(f"{path}: at least one [[areas]] entry is required")

    areas: list[Area] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_areas):
        where = f"{path}: areas[{i}]"
        name = raw.get("name")
        if not isinstance(name, str) or not _AREA_NAME.match(name):
            raise ValueError(f"{where}: 'name' must match [A-Za-z0-9_-]+")
        if name in seen:
            raise ValueError(f"{where}: duplicate area name {name!r}")
        seen.add(name)
        area_id = raw.get("id")
        if area_id is not None and (not isinstance(area_id, int) or isinstance(area_id, bool)):
            raise ValueError(f"{where}: 'id' must be an integer")
        output = Path(raw["output"]) if "output" in raw else output_dir / f"{name}.ndjson"
        areas.append(Area(name, _check_bbox(raw.get("bbox"), where), output, id=area_id))

    outputs = [a.output_path.resolve() for a in areas]
    if len(set(outputs)) != len(outputs):
        raise ValueError(f"{path}: areas must write to distinct output files")

    return Config(wss_url, interval, request, tuple(areas), duration=duration)


def _check_request(request: Any, where: str) -> None:
    if not isinstance(request, dict) or not isinstance(request.get("id"), int):
        raise ValueError(f"{where} must be an object containing a numeric 'id' field")


def _check_bbox(bbox: Any, where: str) -> tuple[float, float, float, float]:
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in bbox)
    ):
        raise ValueError(f"{where}: 'bbox' must be [minLat, minLon, maxLat, maxLon]")
    min_lat, min_lon, max_lat, max_lon = (float(v) for v in bbox)
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError(
            f"{where}: 'bbox' needs -90 <= minLat < maxLat <= 90 and -180 <= minLon < maxLon <= 180"
        )
    return min_lat, min_lon, max_lat, max_lon
