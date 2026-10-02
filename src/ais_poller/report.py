import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# A gap between ticks counts as abnormal when it exceeds this multiple of the median interval.
GAP_FACTOR = 3.0


@dataclass
class Gap:
    after: str
    before: str
    seconds: float


@dataclass
class FileReport:
    path: str
    records: int = 0
    bad_lines: int = 0
    ticks: int = 0
    first: str | None = None
    last: str | None = None
    median_interval_s: float | None = None
    records_per_tick: dict[str, float] = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)
    areas: dict[str, int] = field(default_factory=dict)
    vessels: int = 0
    extent: dict[str, float] | None = None
    out_of_order: int = 0


def inspect_file(path: Path) -> FileReport:
    """Summarise a poller NDJSON file in one streaming pass.

    Memory grows only with the number of ticks and distinct vessels, not with the record count.
    """
    report = FileReport(str(path))
    tick_times: list[str] = []
    tick_sizes: list[int] = []
    areas: Counter[str] = Counter()
    vessels: set[str] = set()
    lat_min = lon_min = float("inf")
    lat_max = lon_max = float("-inf")

    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            feat = json.loads(line)
            ts = feat["requested_at"]
            report.records += 1
            # Records of one tick share requested_at and are written contiguously.
            if tick_times and ts == tick_times[-1]:
                tick_sizes[-1] += 1
            else:
                if tick_times and ts < tick_times[-1]:
                    report.out_of_order += 1
                tick_times.append(ts)
                tick_sizes.append(1)
            if "area" in feat:
                areas[feat["area"]] += 1
            props = feat.get("properties") or {}
            vessel = props.get("TrackID") or props.get("MMSI") or feat.get("id")
            if vessel is not None:
                vessels.add(str(vessel))
            lat, lon = props.get("Lat"), props.get("Lon")
            if isinstance(lat, int | float) and isinstance(lon, int | float):
                lat_min, lat_max = min(lat_min, lat), max(lat_max, lat)
                lon_min, lon_max = min(lon_min, lon), max(lon_max, lon)

    report.ticks = len(tick_times)
    report.areas = dict(areas)
    report.vessels = len(vessels)
    if lat_min != float("inf"):
        report.extent = {
            "minLat": lat_min,
            "minLon": lon_min,
            "maxLat": lat_max,
            "maxLon": lon_max,
        }
    if tick_times:
        report.first, report.last = tick_times[0], tick_times[-1]
        report.records_per_tick = {
            "min": min(tick_sizes),
            "median": statistics.median(tick_sizes),
            "max": max(tick_sizes),
        }
    # Ticks that returned zero features write no lines, so they show up as gaps.

    times = [datetime.fromisoformat(t) for t in tick_times]
    intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    if intervals:
        median = statistics.median(intervals)
        report.median_interval_s = round(median, 3)
        report.gaps = [
            Gap(tick_times[i], tick_times[i + 1], round(s, 3))
            for i, s in enumerate(intervals)
            if s > GAP_FACTOR * median
        ]
    return report


def format_report(report: FileReport) -> str:
    lines = [f"== {report.path}"]
    lines.append(f"records: {report.records}  bad lines: {report.bad_lines}  ticks: {report.ticks}")
    if report.first:
        lines.append(f"span: {report.first} → {report.last}")
    if report.median_interval_s is not None:
        lines.append(f"median tick interval: {report.median_interval_s}s")
    if report.records_per_tick:
        r = report.records_per_tick
        lines.append(f"records/tick: min {r['min']}  median {r['median']}  max {r['max']}")
    lines.append(f"distinct vessels: {report.vessels}")
    if report.extent:
        e = report.extent
        lines.append(
            f"extent: lat {e['minLat']:.5f}..{e['maxLat']:.5f}  "
            f"lon {e['minLon']:.5f}..{e['maxLon']:.5f}"
        )
    if report.areas:
        lines.append("areas: " + ", ".join(f"{k}={v}" for k, v in sorted(report.areas.items())))
    if report.out_of_order:
        lines.append(f"WARNING: {report.out_of_order} tick(s) out of chronological order")
    if report.gaps:
        lines.append(f"gaps > {GAP_FACTOR:g}x median: {len(report.gaps)}")
        for g in report.gaps[:20]:
            lines.append(f"  {g.seconds:>10.1f}s  {g.after} → {g.before}")
        if len(report.gaps) > 20:
            lines.append(f"  ... {len(report.gaps) - 20} more (use --json for all)")
    else:
        lines.append("gaps: none")
    return "\n".join(lines)


def to_json(reports: list[FileReport]) -> str:
    return json.dumps([asdict(r) for r in reports], indent=2)
