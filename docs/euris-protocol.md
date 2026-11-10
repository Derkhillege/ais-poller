# EuRIS AISTracks WebSocket: observed protocol

There is no published specification for the EuRIS AISTracks WebSocket. Everything this
project does with the socket was inferred from live runs, so this file is the project's
**evidence record**: what we have actually seen on the wire, how often, and when. Treat anything not listed here as unknown
rather than impossible.

Keep it honest: statements below are either **observed** (a catalogue from a real run backs
them) or **assumed** (we infer it, no sample yet). Promote an assumption only by pasting the
evidence.

## How evidence is collected

Every streaming run records a catalogue per area while it polls: each distinct message
*signature* (kind of message, not vessel content), its count, first and last sighting, and one
truncated sample. Catalogues are written to `<output dir>/protocol/<area>.json` after every
connection, so a crash or `SIGKILL` still leaves the evidence behind.

```sh
ais-poller poll --config ais-poller.toml -d 900     # a 15-minute run on a networked machine
ais-poller protocol data/protocol                   # table: counts per message kind
ais-poller protocol data/protocol --markdown        # the section to paste below
```

Local runs belong in `local-runs/<date>/` (gitignored) together with `poll.log`; copy the
`protocol/` directory in as well, since that is the part worth keeping.

## Request side (assumed, from the portal's own client)

* The subscription is JSON-RPC-shaped: `{"jsonrpc": "2.0", "id": <int>, "method": …, "params":
  {"filters": {"boundingBox": {"minLat", "minLon", "maxLat", "maxLon"}, …}}}`.
* `method = "GetFeatures"` answers one request at a time; `method = "StreamFeatures"` subscribes
  once and the server pushes afterwards.
* `id` is incremented per request and per re-subscription. Whether the server tracks it is
  **assumed**, not confirmed — it is kept because the portal's client does it.

## Response side

| Message | Status | Shape |
|---|---|---|
| Snapshot answer to a subscribe or `GetFeatures` | observed | `{"result": {"features": [<GeoJSON Feature>, …]}}` |
| Pushed updates | observed | `{"method": "featureStream", "params": ["<feature JSON string>", …]}`, ~5/s for one area |
| Track left the bounding box | **partially observed** — the method name `outOfBoundingBox` is seen in logs, the params are not yet confirmed | assumed to mirror `featureStream`: feature JSON strings, feature objects, or bare track ids |
| JSON-RPC error | assumed | `{"error": {…}}`, raised as `RuntimeError` by `_check_jsonrpc_error` |

Because the out-of-bounding-box params are unconfirmed, `_pushed_exits` reads them tolerantly
and always keeps the raw payload, and `Sink.write_exits` stores the raw payload in the event
line. A `track_id` of `null` next to a populated `raw` in `events/<area>.ndjson` means the real
shape differs from all three assumptions — paste that line here and tighten the parser.

## Evidence

_No catalogue has been committed yet._ Run the commands above on a networked machine and replace
this section with the output of `ais-poller protocol <paths> --markdown`.
