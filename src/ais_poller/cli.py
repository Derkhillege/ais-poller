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

    poll = sub.add_parser("poll", help="poll the WebSocket and append features as NDJSON (default)")
    source = poll.add_mutually_exclusive_group()
    source.add_argument(
        "-c", "--config", type=Path, help="TOML config with one or more areas, polled in parallel"
    )
    source.add_argument(
        "--env-file", type=Path, help="load env vars from this file instead of .env"
    )
    poll.add_argument("--url", help="WebSocket URL (overrides url / $EURIS_WSS)")
    poll.add_argument(
        "--interval",
        type=int,
        help="seconds between requests, or between summary logs when streaming "
        "(overrides interval / $INTERVAL)",
    )
    poll.add_argument(
        "-d",
        "--duration",
        type=float,
        default=None,
        help="duration in seconds to run before stopping (default: indefinite)",
    )
    poll.add_argument(
        "-o",
        "--output",
        type=Path,
        help="with --config: output directory (overrides output_dir); "
        "otherwise: NDJSON output file (overrides $OUTPUT_PATH)",
    )

    parse = sub.add_parser("parse", help="convert poller NDJSON into flat vessel records")
    parse.add_argument("input", type=Path, help="NDJSON file written by `ais-poller poll`")
    parse.add_argument("-o", "--output", type=Path, help="output file (default: stdout)")
    parse.add_argument("--format", choices=["csv", "ndjson"], default="csv", help="default: csv")

    inspect = sub.add_parser(
        "inspect", help="summarise poller NDJSON files (ticks, gaps, areas, extent)"
    )
    inspect.add_argument("files", nargs="+", type=Path, help="NDJSON files written by `poll`")
    inspect.add_argument("--json", action="store_true", help="machine-readable output")

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
        getattr(args, "env_file", None),
        config_file=getattr(args, "config", None),
        wss_url=getattr(args, "url", None),
        interval=getattr(args, "interval", None),
        output_path=getattr(args, "output", None),
        duration=getattr(args, "duration", None),
    )
    run(config)
    return 0


def _parse(args: argparse.Namespace) -> int:
    from ais_poller.parse import parse_file

    if args.output is None:
        n = parse_file(args.input, sys.stdout, args.format)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as out:
            n = parse_file(args.input, out, args.format)
    logging.getLogger("ais_poller").info("parsed %d records", n)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    from ais_poller.report import format_report, inspect_file, to_json

    reports = [inspect_file(p) for p in args.files]
    print(to_json(reports) if args.json else "\n\n".join(format_report(r) for r in reports))
    return 0


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
    return _poll(args)
