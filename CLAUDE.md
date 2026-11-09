# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A minimal AIS (Automatic Identification System) CLI. `ais-poller poll` connects to the EuRIS WebSocket endpoint, sends a request payload, and writes each returned vessel feature per configured area either as a raw JSON line to an NDJSON file (`format = "ndjson"`, default; stamped with `timestamp` at receive time) or parsed on arrival into zstd GeoParquet (`format = "parquet"`). `ais-poller parse` streams an NDJSON file into flat vessel records (CSV or NDJSON).

## Environment

Managed with `uv` (Python 3.13 pinned via `.python-version`). Build backend is `hatchling`; package source lives under `src/ais_poller/`.

- Install / sync deps: `uv sync`
- Run the poller: `uv run ais-poller [poll] [--config ais-poller.toml]` (script entry `ais_poller.cli:main`) or `uv run python -m ais_poller`
- Parse results: `uv run ais-poller parse data.json -o out.csv [--format csv|ndjson]`
- License headers: `python3 scripts/add_license_headers.py [--check] [files...]`; the `.githooks/pre-commit` hook runs it on staged files (enable with `git config core.hooksPath .githooks`). Every source file needs the GPL-3.0-or-later notice at the top (after any shebang); `tests/test_license.py` fails if one is missing or stale. The notice is generated from `pyproject.toml`, never hardcoded — change the name, description, author or `[tool.ais-poller] copyright-year` and re-run the script to rewrite every header.
- Tests: `uv run pytest` (fast); `uv run pytest -m slow` runs against the full `data.json` test set (gitignored, 47,794 records; `data.raw.json` is the untrimmed original)

Configuration is a TOML file, `ais-poller.toml` by default (gitignored; start from `ais-poller.example.toml`): `url`, `interval` (seconds between requests; with `StreamFeatures`, between summary log lines), optional `output_path` (the output directory), `format` (`ndjson`|`parquet`), `rotate` (seconds per Parquet file, default 3600), a `[request]` template whose numeric `id` is optional, and one or more `[[areas]]` with `name` + `bbox = [minLat, minLon, maxLat, maxLon]` (optional `output`). Each area is polled on its own socket in parallel. `--url`, `--interval`, `-o` (output dir), `--format`, `--rotate` override the file; `--log-level` / `$LOG_LEVEL` set logging (default `INFO`). There is no `.env` / env-var mode.

## Live testing

The devcontainer has no network access to EuRIS; `poll` can only be exercised live on the user's local machine. Local runs are brought back under `local-runs/<date>/` (gitignored: `poll.log`, `inspect.txt`, config, NDJSON or samples). Analyse them with `uv run ais-poller inspect ...` and by reading the logs — don't try to reach EuRIS from here. In the devcontainer, verify poller changes with the fake-socket tests in `tests/test_poller.py`.

## Architecture notes

- `cli.py` — argparse entry point `main(argv) -> int`; subcommands `poll` (default), `parse` and `inspect`. CLI flags override config values.
- `config.py` — `load_config(path)` → `Config` (url, interval, request template, tuple of `Area`s) from TOML. Fail-fast SystemExit on missing/bad values. `Config.request_for(area)` deep-copies the template and fills `params.filters.boundingBox`.
- `poller.py` — `poll_once` (single send/recv cycle, writes to the area's sink), `poll_area` (request/response reconnect loop for one area), `stream_area` (subscribe once per connection, then `_drain` every pushed message) and `run(config)` (one thread per area, joins until shutdown). `Config.streaming` (request `method == "StreamFeatures"`) picks `stream_area`.
- Streaming must keep reading the socket: websockets stops reading the network once its 16-frame receive queue is full, so unread pushes starve pongs and the keepalive ping times out (the ~51 s reconnect cycle in `local-runs/2026-09-26`). The subscribe is answered with a `result.features` snapshot; updates then arrive as JSON-RPC notifications `{"method": "featureStream", "params": ["<feature JSON string>", ...]}` (~5/s for one area) and are written like snapshot features. Any other message is logged (first 5 per connection at INFO).
- `sink.py` — `open_sink(config, area)` → `NdjsonSink` (raw lines, append per message) or `ParquetSink` (parse with `parse_euris_feature`, buffer ≤10k rows per row group, zstd). Parquet layout is Hive `<output_path>/area=<name>/year=YYYY/month=MM/day=DD/part-<start>Z.parquet` (the layout `ais-shader`'s `detect_hive_partitioning()` expects); windows align to UTC multiples of `rotate` and also roll at midnight; rotation is checked on write, so a quiet area keeps its file open until the next record or shutdown. The open file is a hidden `.part-*.parquet.tmp` (no footer yet, so unreadable) renamed on rotation/close; a SIGKILL loses the open window. Each area thread owns its sink for the whole reconnect loop (`with open_sink(...)` in `poll_area`/`stream_area`), so nothing is shared between threads.
- `conformance.py` — `check_dataset(path)` → `Result(files, rows, partition_keys, problems)` behind `ais-poller check`, transcribing the ais-shader GeoParquet spec (TUDelft-CITG/ais-colab `docs/geoparquet_specification.md`): required/optional columns with their accepted aliases and types, `geo` metadata (WKB Point, WGS84), Hive `year=/month=/day=` partitioning and the documented value ranges. Keep `sink.py` and this file in step; `tests/test_conformance.py` checks a dataset written by the real sink.
- `report.py` — `inspect_file` (single streaming pass: ticks, gaps > 3× median interval, areas, extent, vessels) behind `ais-poller inspect`; takes NDJSON files, `.parquet` files or Parquet dataset directories (area from the `area=` path segment).
- `parse.py` — `parse_euris_feature` (feature → flat record), `iter_records` (streaming NDJSON reader), `parse_file`. Both poll and parse must stay streaming (no loading whole files) so long runs don't grow memory.
- `__init__.py` only exposes `__version__`; `__main__.py` is a shim.
- Outer loop reconnects on `WebSocketException`/`OSError` with exponential backoff capped at 60s; inner loop reuses a single socket across ticks.
- Each area's request dict (from `request_for`) has its `id` mutated in place across ticks and reconnects (streaming: once per subscription) — preserve this when refactoring; resetting it would break server-side request tracking. Areas never share a request dict or output file.
- Response shape is assumed to be `{"result": {"features": [...]}}`. Malformed JSON is logged and skipped; missing `result.features` yields zero records (no crash).
- Graceful shutdown: SIGINT/SIGTERM set the module-level `_stop_event` (`threading.Event`). Area threads sleep via `stop.wait(...)` so they wake immediately, finish the in-progress tick and close their socket via the context manager; the main thread joins with a timeout to stay responsive to signals.

