---
name: Project invariants and architecture
description: Core contracts and architecture facts for AIS-Poller that must be preserved across refactors
type: project
---

Key invariants verified as of 2026-05-01 (initial restructure commit):

- `request_params["id"]` is mutated in-place in `main()` after each successful `_poll_once` call (line 75). Do NOT reset or reassign — server-side request tracking depends on monotonically increasing id.
- Outer loop catches `WebSocketException | OSError`; backoff starts at 1s, doubles, caps at 60s. Backoff resets to 1 on successful connection.
- `_running` is a module-level bool flipped by SIGINT/SIGTERM via `_stop`. Both loops check it; the inner loop also falls through to allow the outer `connect` context manager to close cleanly.
- `received_at` timestamp uses `datetime.now(timezone.utc).isoformat()` — timezone-aware UTC. Old `main.py` used naive local time; the new code is correct.
- Each record is written individually via `json.dump` + `\n` (NDJSON append). File opened with `output_path.open("a")` inside `_poll_once`.
- `json.JSONDecodeError` is caught in the outer try/except in `main()`, NOT inside `_poll_once`. This means a malformed response skips the tick but does NOT reconnect; `request_params["id"]` is also NOT incremented on a malformed tick (id is incremented only after `_poll_once` returns successfully).
- `_load_config` validates: `EURIS_WSS` present, `REQUEST_PARAMS` valid JSON dict with numeric `id` field, `INTERVAL` parseable as int. Fails fast with SystemExit.
- `OUTPUT_PATH` parent dirs auto-created with `mkdir(parents=True, exist_ok=True)` before the loop.
- Entry points: `uv run ais-poller` (script) and `uv run python -m ais_poller` (via `__main__.py`).

**Why:** These invariants are load-bearing for EuRIS protocol compatibility and data integrity.
**How to apply:** Flag any change that resets `id`, removes backoff cap, swallows exceptions without logging, or uses naive timestamps.
