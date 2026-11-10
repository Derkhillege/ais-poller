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

import argparse
import logging
import os
import sys
from pathlib import Path

from ais_poller import __version__
from ais_poller.handoff import TOLERANCE_S, WINDOW_S
from ais_poller.license import print_startup_notice
from ais_poller.metadata import project_metadata

_META = project_metadata()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_META.name,
        description=_META.summary,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="logging level (default: $LOG_LEVEL or INFO)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    poll = sub.add_parser(
        "poll", help="poll the WebSocket and write NDJSON or GeoParquet (default command)"
    )
    poll.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("ais-poller.toml"),
        help="TOML config listing the areas to poll in parallel (default: ais-poller.toml)",
    )
    poll.add_argument("--url", help="WebSocket URL (overrides url)")
    poll.add_argument(
        "--interval",
        type=int,
        help="seconds between requests, or between summary logs when streaming "
        "(overrides interval)",
    )
    poll.add_argument(
        "-d",
        "--duration",
        type=float,
        default=None,
        help="seconds to run before shutting down gracefully "
        "(overrides duration, default: run until SIGINT/SIGTERM)",
    )
    poll.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output directory for the area files (overrides output_path)",
    )
    poll.add_argument(
        "--format",
        choices=["ndjson", "parquet"],
        help="ndjson: raw features, uncompressed; parquet: parsed records as zstd GeoParquet "
        "(overrides format, default ndjson)",
    )
    poll.add_argument(
        "--rotate", type=int, help="seconds per Parquet file (overrides rotate, default 3600)"
    )

    parse = sub.add_parser("parse", help="convert poller NDJSON into flat vessel records")
    parse.add_argument("input", type=Path, help="NDJSON file written by `ais-poller poll`")
    parse.add_argument("-o", "--output", type=Path, help="output file (default: stdout)")
    parse.add_argument("--format", choices=["csv", "ndjson"], default="csv", help="default: csv")

    inspect = sub.add_parser("inspect", help="summarise poller output (ticks, gaps, areas, extent)")
    inspect.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="NDJSON or Parquet files, or Parquet dataset directories, written by `poll`",
    )
    inspect.add_argument("--json", action="store_true", help="machine-readable output")

    hand = sub.add_parser(
        "handoffs",
        help="pair out-of-bounding-box exits with pings in neighbouring areas",
    )
    hand.add_argument("root", type=Path, help="poller output directory (holds events/ and areas)")
    hand.add_argument(
        "--window", type=int, default=WINDOW_S, help=f"seconds after an exit (default {WINDOW_S})"
    )
    hand.add_argument(
        "--tolerance",
        type=int,
        default=TOLERANCE_S,
        help=f"seconds before an exit (default {TOLERANCE_S})",
    )
    hand.add_argument("--json", action="store_true", help="machine-readable output")

    proto = sub.add_parser(
        "protocol",
        help="summarise the recorded EuRIS message kinds (evidence for docs/euris-protocol.md)",
    )
    proto.add_argument(
        "paths", nargs="+", type=Path, help="protocol/ directory or <area>.json catalogue(s)"
    )
    proto.add_argument(
        "--markdown", action="store_true", help="emit the docs/euris-protocol.md evidence section"
    )

    check = sub.add_parser(
        "check",
        help="verify Parquet output against the ais-shader GeoParquet specification",
    )
    check.add_argument(
        "paths", nargs="+", type=Path, help="Parquet file(s) or dataset directory(ies)"
    )

    lic = sub.add_parser("license", help="show this program's GPL licensing notice")
    details = lic.add_mutually_exclusive_group()
    details.add_argument(
        "--warranty", action="store_true", help="show the warranty disclaimer (GPL sections 15-17)"
    )
    details.add_argument(
        "--conditions", action="store_true", help="show the redistribution conditions"
    )
    return parser


def _poll(args: argparse.Namespace) -> int:
    from ais_poller.config import load_config
    from ais_poller.poller import run

    config = load_config(
        getattr(args, "config", Path("ais-poller.toml")),
        wss_url=getattr(args, "url", None),
        interval=getattr(args, "interval", None),
        output_path=getattr(args, "output", None),
        duration=getattr(args, "duration", None),
        fmt=getattr(args, "format", None),
        rotate=getattr(args, "rotate", None),
    )
    run(config)
    return 0


#: Exit code for a missing input file, as argparse uses for a usage error.
MISSING_FILE = 2


def _no_such_file(path: object) -> int:
    """Report a missing input file the way a CLI should: a message, not a traceback."""
    print(f"no such file: {path}", file=sys.stderr)
    return MISSING_FILE


def _parse(args: argparse.Namespace) -> int:
    from ais_poller.parse import parse_file

    if not args.input.is_file():  # checked up front, so no partial output is written
        return _no_such_file(args.input)
    try:
        if args.output is None:
            n = parse_file(args.input, sys.stdout, args.format)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w", newline="", encoding="utf-8") as out:
                n = parse_file(args.input, out, args.format)
    except FileNotFoundError as e:  # lost between the check and the read
        return _no_such_file(e.filename or args.input)
    logging.getLogger("ais_poller").info("parsed %d records", n)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    from ais_poller.report import format_report, inspect_file, to_json

    try:
        reports = [inspect_file(p) for p in args.files]
    except FileNotFoundError as e:
        return _no_such_file(e.filename or args.files[0])
    print(to_json(reports) if args.json else "\n\n".join(format_report(r) for r in reports))
    return 0


def _handoffs(args: argparse.Namespace) -> int:
    from ais_poller.handoff import (
        discover,
        find_handoffs,
        format_handoffs,
        load_exits,
        to_json,
    )

    events, data = discover(args.root)
    if not events.exists():
        return _no_such_file(events)
    exits = load_exits(events)
    handoffs = find_handoffs(exits, data, window_s=args.window, tolerance_s=args.tolerance)
    print(to_json(handoffs) if args.json else format_handoffs(handoffs, len(exits)))
    return 0


def _protocol(args: argparse.Namespace) -> int:
    from ais_poller.protocol import format_catalog, load, merge, to_markdown

    catalogs = load(args.paths)
    if not catalogs:
        return _no_such_file(args.paths[0])
    catalog = merge(catalogs)
    print(to_markdown(catalog) if args.markdown else format_catalog(catalog))
    return 0


def _check(args: argparse.Namespace) -> int:
    from ais_poller.conformance import check_dataset, format_result

    results = [check_dataset(p) for p in args.paths]
    print("\n\n".join(format_result(r) for r in results))
    return 0 if all(r.ok for r in results) else 1


def _license(args: argparse.Namespace) -> int:
    from ais_poller.license import CONDITIONS, WARRANTY, startup_notice

    if args.warranty:
        print(WARRANTY)
    elif args.conditions:
        print(CONDITIONS)
    else:
        print(startup_notice())
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `ais-poller`."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.command == "license":
        return _license(args)
    # The GPL asks interactive programs to show a short notice at startup.
    print_startup_notice()
    if args.command == "parse":
        return _parse(args)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "check":
        return _check(args)
    if args.command == "handoffs":
        return _handoffs(args)
    if args.command == "protocol":
        return _protocol(args)
    return _poll(args)
