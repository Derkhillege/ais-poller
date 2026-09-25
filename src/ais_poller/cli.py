import argparse
import logging
import os
import sys
from pathlib import Path

from ais_poller import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ais-poller",
        description="Poll the EuRIS AIS WebSocket to NDJSON and parse the collected results.",
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
    )
    run(config)
    return 0


def _parse(args: argparse.Namespace) -> int:
    from ais_poller.parse import parse_file

    if not args.input.is_file():
        print(f"ais-poller parse: no such file: {args.input}", file=sys.stderr)
        return 2
    if args.output is None:
        n = parse_file(args.input, sys.stdout, args.format)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="") as out:
            n = parse_file(args.input, out, args.format)
    logging.getLogger("ais_poller").info("parsed %d records", n)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    from ais_poller.report import format_report, inspect_file, to_json

    missing = [p for p in args.files if not p.is_file()]
    if missing:
        print(f"ais-poller inspect: no such file: {missing[0]}", file=sys.stderr)
        return 2
    reports = [inspect_file(p) for p in args.files]
    print(to_json(reports) if args.json else "\n\n".join(format_report(r) for r in reports))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `ais-poller`."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.command == "parse":
        return _parse(args)
    if args.command == "inspect":
        return _inspect(args)
    return _poll(args)
