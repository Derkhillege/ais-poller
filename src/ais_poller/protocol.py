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

"""Record what the EuRIS WebSocket actually sends, so the protocol stops being guesswork.

EuRIS publishes no specification of its AISTracks socket, so every message shape in this
project was inferred from live runs. A poll run therefore keeps a catalogue: each distinct
message *signature* with how often it arrived, when it was first and last seen, and one
truncated sample. `ais-poller protocol` turns those catalogues into a table or into Markdown
for `docs/euris-protocol.md`, which is how a shape graduates from assumption to documented
evidence.

The catalogue records shapes, not vessel positions: one sample per signature, truncated.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Longest sample kept per signature; enough to show the shape, not a position archive.
SAMPLE_CHARS = 2000


def message_signature(message: Any) -> str:
    """A stable name for a message's shape, e.g. `notification:featureStream(params[str])`.

    Two messages share a signature when they are the same kind of message, so a catalogue
    stays small however long a run lasts.
    """
    if not isinstance(message, dict):
        return f"non-object:{type(message).__name__}"
    if "error" in message:
        return "error"
    method = message.get("method")
    if isinstance(method, str):
        params = message.get("params")
        if isinstance(params, list):
            kinds = sorted({type(p).__name__ for p in params[:50]})
            inner = f"params[{','.join(kinds) or 'empty'}]"
        elif isinstance(params, dict):
            inner = f"params{{{','.join(sorted(params)[:10])}}}"
        else:
            inner = f"params:{type(params).__name__}"
        return f"notification:{method}({inner})"
    if "result" in message:
        result = message["result"]
        if isinstance(result, dict):
            return f"result{{{','.join(sorted(result)[:10])}}}"
        return f"result:{type(result).__name__}"
    return f"object{{{','.join(sorted(message)[:10])}}}"


@dataclass
class MessageKind:
    """One observed message shape."""

    signature: str
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    sample: str = ""
    truncated: bool = False


@dataclass
class Catalog:
    """Every message shape one area has seen, with counts and one sample each."""

    area: str = ""
    url: str = ""
    kinds: dict[str, MessageKind] = field(default_factory=dict)

    def record(self, raw: str, message: Any, now: datetime | None = None) -> str:
        """Count one received message and keep it as the sample if it is the first of its kind."""
        signature = message_signature(message)
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        kind = self.kinds.get(signature)
        if kind is None:
            kind = MessageKind(signature, first_seen=stamp)
            kind.sample = raw[:SAMPLE_CHARS]
            kind.truncated = len(raw) > SAMPLE_CHARS
            self.kinds[signature] = kind
        kind.count += 1
        kind.last_seen = stamp
        return signature

    @property
    def messages(self) -> int:
        return sum(k.count for k in self.kinds.values())

    def write(self, path: Path) -> None:
        """Write the catalogue as JSON, replacing any previous one for this area."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "area": self.area,
            "url": self.url,
            "messages": self.messages,
            "kinds": [asdict(k) for k in sorted(self.kinds.values(), key=lambda k: -k.count)],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n")

    @classmethod
    def read(cls, path: Path) -> "Catalog":
        data = json.loads(path.read_text())
        catalog = cls(area=data.get("area", ""), url=data.get("url", ""))
        for raw in data.get("kinds", []):
            kind = MessageKind(**{k: v for k, v in raw.items() if k in MessageKind.__annotations__})
            catalog.kinds[kind.signature] = kind
        return catalog


def catalog_path_for(output_path: Path, area: str) -> Path:
    """Where an area's catalogue goes: `<output dir>/protocol/<area>.json`.

    As for the event files, an area's `output_path` is a file for NDJSON and a dataset
    directory for Parquet, so its parent is the output directory in both cases.
    """
    return output_path.parent / "protocol" / f"{area}.json"


def merge(catalogs: list[Catalog]) -> Catalog:
    """Combine per-area catalogues into one view of the protocol."""
    merged = Catalog(area=", ".join(sorted({c.area for c in catalogs if c.area})))
    merged.url = next((c.url for c in catalogs if c.url), "")
    for catalog in catalogs:
        for signature, kind in catalog.kinds.items():
            into = merged.kinds.get(signature)
            if into is None:
                merged.kinds[signature] = MessageKind(**asdict(kind))
                continue
            into.count += kind.count
            into.first_seen = min(into.first_seen, kind.first_seen)
            into.last_seen = max(into.last_seen, kind.last_seen)
    return merged


def load(paths: list[Path]) -> list[Catalog]:
    """Read every catalogue in the given files or `protocol/` directories."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            files.append(path)
    return [Catalog.read(f) for f in files]


def format_catalog(catalog: Catalog) -> str:
    """A readable table of observed message kinds, most frequent first."""
    kinds = sorted(catalog.kinds.values(), key=lambda k: -k.count)
    head = f"areas: {catalog.area or '(unknown)'}  messages: {catalog.messages}"
    if not kinds:
        return f"{head}\nno messages recorded"
    rows = [
        f"{'count':>9}  {'first seen':<25}  signature",
        *(f"{k.count:>9}  {k.first_seen:<25}  {k.signature}" for k in kinds),
    ]
    return "\n".join([head, *rows])


def to_markdown(catalog: Catalog) -> str:
    """The evidence section for `docs/euris-protocol.md`, with one sample per kind."""
    kinds = sorted(catalog.kinds.values(), key=lambda k: -k.count)
    lines = [
        "<!-- generated by `ais-poller protocol <paths> --markdown`; evidence from live runs -->",
        "",
        f"Areas: {catalog.area or '(unknown)'}. Messages recorded: {catalog.messages}.",
        "",
        "| Messages | First seen | Last seen | Signature |",
        "|---|---|---|---|",
    ]
    lines += [f"| {k.count} | {k.first_seen} | {k.last_seen} | `{k.signature}` |" for k in kinds]
    for kind in kinds:
        lines += [
            "",
            f"### `{kind.signature}`",
            "",
            f"Seen {kind.count}×, first at {kind.first_seen}"
            + (", sample truncated" if kind.truncated else "")
            + ".",
            "",
            "```json",
            kind.sample,
            "```",
        ]
    return "\n".join(lines)
