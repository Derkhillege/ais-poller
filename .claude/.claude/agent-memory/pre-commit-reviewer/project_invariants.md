---
name: AIS Poller — core project invariants
description: Documented invariants and conventions that must be preserved across all changes
type: project
---

`request_params["id"]` is mutated in place each tick (`+= 1`) to satisfy the EuRIS server-side request tracking protocol. Never reset or reassign the dict between ticks.

**Why:** EuRIS protocol requires monotonically incrementing id per connection.

**How to apply:** Flag any refactor that replaces the dict reference, resets id to a constant, or delays the increment past the tick boundary.

---

Reconnection uses exponential backoff starting at 1s, doubling, capped at 60s, on `WebSocketException` or `OSError` only. `json.JSONDecodeError` is handled separately in the outer loop — it does NOT trigger reconnect, only a sleep and skip.

**Why:** Malformed JSON is a data issue, not a transport issue; reconnecting would be incorrect.

**How to apply:** Any change to the except-clause ordering or exception types needs close scrutiny.

---

Graceful shutdown: SIGINT/SIGTERM set module-level `_running = False`. Both the outer reconnect loop and the inner tick loop check `_running` before each iteration. The in-progress tick is allowed to finish before the socket is closed via the `with connect(...)` context manager.

**Why:** Prevents partial NDJSON records and ensures socket teardown is clean.

**How to apply:** Flag any blocking call (e.g. long `time.sleep`) inside a loop that does not check `_running` afterward, or any change that removes the flag check.

---

Output is NDJSON appended to `OUTPUT_PATH` (default `data.json`). Each record gets a `requested_at` field stamped with `datetime.now(timezone.utc).isoformat()` at receive time, not send time. The field name is `requested_at` (not `received_at`, not `timestamp`).

**Why:** The field name is part of the downstream schema consumers depend on.

**How to apply:** Flag any rename or removal of `requested_at`, or any use of a non-UTC clock.

---

`_load_config` must fail fast with `SystemExit` on missing or malformed env vars. It uses `os.environ[key]` (KeyError) not `os.getenv()` for required vars, so absence raises immediately rather than propagating `None` into downstream code.

**Why:** Silent `None` propagation causes confusing errors later (e.g. `json.loads(None)`).

**How to apply:** Flag any switch from `os.environ[]` to `os.getenv()` for required vars, or any swallowed KeyError.

---

Tests for `_load_config` must patch `ais_poller.load_dotenv` to a no-op (e.g. `patch("ais_poller.load_dotenv", lambda **_kw: None)` as an `autouse=True` fixture). Without this, `load_dotenv()` reads the real `.env` file and overwrites monkeypatched env vars, making missing-var tests spuriously pass.

**Why:** `_load_config` calls `load_dotenv()` unconditionally at the top, before reading `os.environ`.

**How to apply:** Any new test file for `_load_config` (or any function that calls `_load_config`) needs this fixture. See `tests/test_load_config.py::no_dotenv`.
