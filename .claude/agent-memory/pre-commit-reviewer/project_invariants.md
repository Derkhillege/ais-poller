---
name: Project invariants and architecture
description: Core contracts and architecture facts for AIS-Poller that must be preserved across refactors
type: project
---

Key invariants, updated 2026-09-26 after the CLI / multi-area restructure (see CLAUDE.md for layout):

- Each area gets its own request dict from `Config.request_for(area)` (deep copy of the template, bbox filled in). `poll_area` increments that dict's `id` in place after each successful `poll_once`. Do NOT reset, reassign, or share it between areas — server-side request tracking depends on a monotonically increasing id.
- `poll_area` catches `WebSocketException | OSError`; backoff starts at 1s, doubles, caps at 60s, resets to 1 on successful connect.
- `json.JSONDecodeError` is caught outside the `with connect(...)` block, so a malformed response closes the socket, waits `interval`, and reconnects; `id` is not incremented for that tick.
- Shutdown: SIGINT/SIGTERM set the module-level `threading.Event` `_stop_event`. All waits use `stop.wait(...)`, never `time.sleep`, so threads wake immediately. `run()` joins area threads with a timeout so the main thread stays responsive to signals.
- Records are appended one line at a time (NDJSON) with `requested_at = datetime.now(UTC).isoformat()` at receive time, plus `area`. Each area writes its own file; config validation rejects two areas sharing an output path.
- Poll, parse and inspect must all stream — flag anything that loads a whole NDJSON file into memory.
- Config is TOML only (`ais-poller.toml` by default; env-var/`.env` mode was removed 2026-09-26). It fails fast with `SystemExit`: url, positive interval (also when overridden by `--interval`), `[request]` with integer id, ≥1 area with safe unique name and valid bbox.
- The devcontainer cannot reach EuRIS; live behaviour is only verified by the user locally (results in gitignored `local-runs/`).

**Why:** These invariants are load-bearing for EuRIS protocol compatibility and data integrity.
**How to apply:** Flag any change that resets or shares `id`, removes the backoff cap, reintroduces `time.sleep` in a poll loop, swallows exceptions without logging, uses naive timestamps, or breaks streaming.
