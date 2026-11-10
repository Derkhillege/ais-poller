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

"""Deduce vessel handoffs between adjacent areas from exit events and pings.

An area's subscription only reports tracks inside its bounding box, so a vessel
crossing from one area into the next shows up as an `outOfBoundingBox`
notification in the first area (recorded by `Sink.write_exits`) followed by a
ping of the same track in the second. `find_handoffs` pairs those two sides.

Memory stays proportional to the number of exits, not the number of records:
exits are small and loaded up front, then every dataset is streamed once.
"""

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ais_poller.report import FileReport, _rows

#: How long after an exit a ping in another area still counts as the same crossing.
WINDOW_S = 900
#: How long before an exit a ping in another area still counts (overlapping boxes, clock skew).
TOLERANCE_S = 60


@dataclass(frozen=True)
class Handoff:
    """One vessel leaving `from_area` and appearing in `to_area`."""

    track_id: Any
    mmsi: Any
    from_area: str
    to_area: str
    exit_at: str
    enter_at: str
    gap_s: float


def load_exits(path: Path) -> list[dict[str, Any]]:
    """Read exit events from an `events/` directory or a single NDJSON file."""
    files = sorted(path.glob("*.ndjson")) if path.is_dir() else [path]
    exits: list[dict[str, Any]] = []
    for file in files:
        with file.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "exit" and event.get("area"):
                    exits.append(event)
    return exits


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def find_handoffs(
    exits: list[dict[str, Any]],
    data_paths: list[Path],
    window_s: int = WINDOW_S,
    tolerance_s: int = TOLERANCE_S,
) -> list[Handoff]:
    """Pair each exit with the earliest ping of the same track in a different area."""
    identities: dict[str, list[int]] = defaultdict(list)
    times: list[datetime | None] = []
    for i, event in enumerate(exits):
        times.append(_when(event.get("observed_at")))
        for ident in (event.get("track_id"), event.get("mmsi")):
            if ident not in (None, ""):
                identities[str(ident)].append(i)

    window, tolerance = timedelta(seconds=window_s), timedelta(seconds=tolerance_s)
    earliest: dict[tuple[int, str], datetime] = {}
    for path in data_paths:
        for timestamp, area, vessel, _lat, _lon in _rows(path, FileReport(str(path))):
            if vessel is None or area is None:
                continue
            candidates = identities.get(str(vessel))
            if not candidates:
                continue
            seen = _when(timestamp)
            if seen is None:
                continue
            for i in candidates:
                left_at = times[i]
                if left_at is None or area == exits[i]["area"]:
                    continue
                if left_at - tolerance <= seen <= left_at + window:
                    key = (i, area)
                    if key not in earliest or seen < earliest[key]:
                        earliest[key] = seen

    handoffs = []
    for i, event in enumerate(exits):
        arrivals = {area: when for (index, area), when in earliest.items() if index == i}
        if not arrivals:
            continue
        to_area, enter_at = min(arrivals.items(), key=lambda item: item[1])
        left_at = times[i]
        assert left_at is not None  # an exit without a time has no arrivals
        handoffs.append(
            Handoff(
                track_id=event.get("track_id"),
                mmsi=event.get("mmsi"),
                from_area=event["area"],
                to_area=to_area,
                exit_at=left_at.isoformat(),
                enter_at=enter_at.isoformat(),
                gap_s=round((enter_at - left_at).total_seconds(), 3),
            )
        )
    return sorted(handoffs, key=lambda h: h.exit_at)


def discover(root: Path) -> tuple[Path, list[Path]]:
    """Split an output directory into its `events/` directory and its area datasets."""
    events = root / "events"
    data = sorted(p for p in root.iterdir() if p.name != "events") if root.is_dir() else []
    return events, data


def format_handoffs(handoffs: list[Handoff], exits: int) -> str:
    """A readable table, or a line saying nothing was paired."""
    head = f"exits: {exits}  handoffs: {len(handoffs)}"
    if not handoffs:
        return f"{head}\nno exit was followed by a ping in another area"
    lines = [head, f"{'track':>12}  {'from':<12} {'to':<12} {'gap_s':>8}  exit_at"]
    lines += [
        f"{str(h.track_id or h.mmsi):>12}  {h.from_area:<12} {h.to_area:<12} "
        f"{h.gap_s:>8.1f}  {h.exit_at}"
        for h in handoffs
    ]
    return "\n".join(lines)


def to_json(handoffs: list[Handoff]) -> str:
    return json.dumps([asdict(h) for h in handoffs], indent=2)
