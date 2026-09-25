# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A minimal AIS (Automatic Identification System) poller that connects to the EuRIS WebSocket endpoint, periodically sends a request payload, and appends each returned vessel feature as a JSON line to `data.json` (or `OUTPUT_PATH`). Each record is stamped with a `requested_at` UTC ISO timestamp at receive time.

## Environment

Managed with `uv` (Python 3.13 pinned via `.python-version`). Build backend is `hatchling`; package source lives under `src/ais_poller/`.

- Install / sync deps: `uv sync`
- Run the poller: `uv run ais-poller` (script entry from `pyproject.toml`) or `uv run python -m ais_poller`

Required env vars in `.env` (loaded via `python-dotenv`):
- `EURIS_WSS` — WebSocket URL.
- `REQUEST_PARAMS` — JSON object sent on each tick. Must contain a numeric `id` field; it is incremented in-place each iteration as part of the EuRIS protocol.
- `INTERVAL` — seconds between requests (int).

Optional:
- `OUTPUT_PATH` — NDJSON output path (default `data.json`). Parent dirs are auto-created.
- `LOG_LEVEL` — stdlib logging level (default `INFO`).

## Architecture notes

- `src/ais_poller/__init__.py` holds `main()` plus helpers `_load_config` (env validation, fail-fast SystemExit on missing/bad vars) and `_poll_once` (single send/recv/append cycle).
- Outer loop reconnects on `WebSocketException`/`OSError` with exponential backoff capped at 60s; inner loop reuses a single socket across ticks.
- `request_params["id"]` is mutated in place across ticks — preserve this when refactoring; resetting it would break server-side request tracking.
- Response shape is assumed to be `{"result": {"features": [...]}}`. Malformed JSON is logged and skipped; missing `result.features` yields zero records (no crash).
- Graceful shutdown: SIGINT/SIGTERM flip a module-level `_running` flag, exiting both loops cleanly so the in-progress tick finishes and the socket closes via the context manager.

