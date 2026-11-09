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

import csv
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

log = logging.getLogger("ais_poller")

# AIS navigational status codes (ITU-R M.1371).
STATUS_LABELS: dict[int, str] = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuverability",
    4: "Constrained by her draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way sailing",
    9: "Reserved for HSC",
    10: "Reserved for WIG",
    11: "Power-driven vessel towing astern",
    12: "Power-driven vessel pushing ahead or towing alongside",
    13: "Reserved",
    14: "AIS-SART active",
    15: "Undefined",
}

FIELDS = [
    "mmsi",
    "base_date_time",
    "latitude",
    "longitude",
    "sog",
    "cog",
    "heading",
    "length",
    "beam",
    "shiptypeAIS",
    "status",
    "status_label",
    "vessel_name",
]


def parse_euris_feature(feat: dict, now_iso: str) -> dict | None:
    """
    Parse a single GeoJSON Feature returned by the EURIS AIS WebSocket into
    a normalized flat record matching ais-shader requirements.
    """
    props = feat.get("properties", {})
    geom = feat.get("geometry", {})
    coords = geom.get("coordinates", [None, None])

    lon = props.get("Lon", coords[0])
    lat = props.get("Lat", coords[1])
    if lon is None or lat is None:
        return None

    mmsi = str(props.get("MMSI") or "")
    if not mmsi or mmsi == "0":
        mmsi = str(props.get("TrackID") or feat.get("id") or "")
    if not mmsi:
        return None

    dim_a = float(props.get("DimA") or 0.0)
    dim_b = float(props.get("DimB") or 0.0)
    dim_c = float(props.get("DimC") or 0.0)
    dim_d = float(props.get("DimD") or 0.0)
    length = dim_a + dim_b
    beam = dim_c + dim_d

    sog = float(props.get("SOG") or 0.0)
    cog = float(props.get("COG") or 0.0)
    th = float(props.get("TH") or 0.0)
    heading = th if th > 0 else cog
    # Explicit None check: ST=0 is a valid status and must not fall through to 15.
    st = int(props["ST"]) if props.get("ST") is not None else 15

    return {
        "mmsi": mmsi,
        "base_date_time": now_iso,
        "latitude": float(lat),
        "longitude": float(lon),
        "sog": sog,
        "cog": cog,
        "heading": heading,
        "length": length if length > 0 else None,
        "beam": beam if beam > 0 else None,
        "shiptypeAIS": int(props.get("VT") or props.get("VG") or 0),
        "status": st,
        "status_label": STATUS_LABELS.get(st, "Undefined"),
        "vessel_name": str(props.get("Name") or f"Track {mmsi}"),
    }


def iter_records(path: Path) -> Iterator[dict]:
    """Stream parsed records from a poller NDJSON file, one line at a time."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            feat = json.loads(line)
            yield parse_euris_feature(feat, feat["timestamp"])


def parse_file(input_path: Path, out: TextIO, fmt: str = "csv") -> int:
    """Write parsed records from input_path to out as CSV or NDJSON; return the record count."""
    n = 0
    if fmt == "csv":
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        for record in iter_records(input_path):
            writer.writerow(record)
            n += 1
    elif fmt == "ndjson":
        for record in iter_records(input_path):
            out.write(json.dumps(record) + "\n")
            n += 1
    else:
        raise ValueError(f"unknown format: {fmt}")
    return n
