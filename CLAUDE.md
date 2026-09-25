# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A minimal AIS (Automatic Identification System) CLI. `ais-poller poll` connects to the EuRIS WebSocket endpoint, periodically sends a request payload, and appends each returned vessel feature as a JSON line to one NDJSON file per configured area. Each record is stamped with a `requested_at` UTC ISO timestamp at receive time. `ais-poller parse` streams such a file into flat vessel records (CSV or NDJSON).

## Environment

Managed with `uv` (Python 3.13 pinned via `.python-version`). Build backend is `hatchling`; package source lives under `src/ais_poller/`.

- Install / sync deps: `uv sync`
- Run the poller: `uv run ais-poller [poll] [--config ais-poller.toml]` (script entry `ais_poller.cli:main`) or `uv run python -m ais_poller`
- Parse results: `uv run ais-poller parse data.json -o out.csv [--format csv|ndjson]`
- License headers: `python3 scripts/add_license_headers.py [--check] [files...]`; the `.githooks/pre-commit` hook runs it on staged files (enable with `git config core.hooksPath .githooks`). Every source file needs the GPL-3.0-or-later notice at the top (after any shebang); `tests/test_license.py` fails if one is missing or stale. The notice is generated from `pyproject.toml`, never hardcoded — change the name, description, author or `[tool.ais-poller] copyright-year` and re-run the script to rewrite every header.
- Tests: `uv run pytest` (fast); `uv run pytest -m slow` runs against the full `data.json` test set (gitignored, 47,794 records; `data.raw.json` is the untrimmed original)

Configuration is a TOML file, `ais-poller.toml` by default (gitignored; start from `ais-poller.example.toml`): `url`, `interval` (seconds between requests; with `StreamFeatures`, between summary log lines), optional `output_path` (the output directory), a `[request]` template whose numeric `id` is optional, and one or more `[[areas]]` with `name` + `bbox = [minLat, minLon, maxLat, maxLon]` (optional `output`). Each area is polled on its own socket in parallel. `--url`, `--interval`, `-o` (output dir) override the file; `--log-level` / `$LOG_LEVEL` set logging (default `INFO`). There is no `.env` / env-var mode.

## Live testing

The devcontainer has no network access to EuRIS; `poll` can only be exercised live on the user's local machine. Local runs are brought back under `local-runs/<date>/` (gitignored: `poll.log`, `inspect.txt`, config, NDJSON or samples). Analyse them with `uv run ais-poller inspect ...` and by reading the logs — don't try to reach EuRIS from here. In the devcontainer, verify poller changes with the fake-socket tests in `tests/test_poller.py`.

## Architecture notes

- `cli.py` — argparse entry point `main(argv) -> int`; subcommands `poll` (default), `parse` and `inspect`. CLI flags override config values.
- `config.py` — `load_config(path)` → `Config` (url, interval, request template, tuple of `Area`s) from TOML. Fail-fast SystemExit on missing/bad values. `Config.request_for(area)` deep-copies the template and fills `params.filters.boundingBox`.
- `poller.py` — `poll_once` (single send/recv/append cycle, tags `area`), `poll_area` (request/response reconnect loop for one area), `stream_area` (subscribe once per connection, then `_drain` every pushed message) and `run(config)` (one thread per area, joins until shutdown). `Config.streaming` (request `method == "StreamFeatures"`) picks `stream_area`.
- Streaming must keep reading the socket: websockets stops reading the network once its 16-frame receive queue is full, so unread pushes starve pongs and the keepalive ping times out (the ~51 s reconnect cycle in `local-runs/2026-09-26`). The subscribe is answered with a `result.features` snapshot; updates then arrive as JSON-RPC notifications `{"method": "featureStream", "params": ["<feature JSON string>", ...]}` (~5/s for one area) and are written like snapshot features. Any other message is logged (first 5 per connection at INFO).
- `report.py` — `inspect_file` (single streaming pass: ticks, gaps > 3× median interval, areas, extent, vessels) behind `ais-poller inspect`.
- `parse.py` — `parse_euris_feature` (feature → flat record), `iter_records` (streaming NDJSON reader), `parse_file`. Both poll and parse must stay streaming (no loading whole files) so long runs don't grow memory.
- `__init__.py` only exposes `__version__`; `__main__.py` is a shim.
- Outer loop reconnects on `WebSocketException`/`OSError` with exponential backoff capped at 60s; inner loop reuses a single socket across ticks.
- Each area's request dict (from `request_for`) has its `id` mutated in place across ticks and reconnects (streaming: once per subscription) — preserve this when refactoring; resetting it would break server-side request tracking. Areas never share a request dict or output file.
- Response shape is assumed to be `{"result": {"features": [...]}}`. Malformed JSON is logged and skipped; missing `result.features` yields zero records (no crash).
- Graceful shutdown: SIGINT/SIGTERM set the module-level `_stop_event` (`threading.Event`). Area threads sleep via `stop.wait(...)` so they wake immediately, finish the in-progress tick and close their socket via the context manager; the main thread joins with a timeout to stay responsive to signals.

