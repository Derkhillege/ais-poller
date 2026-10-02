import json
import logging
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from ais_poller.config import Area, Config

log = logging.getLogger("ais_poller")

# Set by SIGINT/SIGTERM; every area thread checks it and wakes from its sleep immediately.
_stop_event = threading.Event()
# Streaming areas wake at least this often (seconds) to check the stop event.
_RECV_TIMEOUT = 1.0
# Non-feature messages logged at INFO per connection; the rest go to DEBUG.
_UNKNOWN_SAMPLES = 5


def _stop(signum: int, _frame: FrameType | None) -> None:
    _stop_event.set()
    log.info("shutdown requested (signal %s)", signum)


def _check_jsonrpc_error(message: Any) -> None:
    """Fail fast if the server responded with a JSON-RPC error."""
    if isinstance(message, dict) and "error" in message:
        raise RuntimeError(f"JSON-RPC error from server: {message['error']}")


def _features(message: Any) -> list[Any] | None:
    """Return message["result"]["features"], or None if the message isn't a feature message."""
    result = message.get("result") if isinstance(message, dict) else None
    features = result.get("features") if isinstance(result, dict) else None
    return features if isinstance(features, list) else None


def _pushed_features(message: Any) -> list[Any] | None:
    """Return the features of a featureStream notification, or None for any other message.

    EuRIS pushes updates as {"method": "featureStream", "params": ["<feature JSON>", ...]},
    each feature encoded as a JSON string.
    """
    if not isinstance(message, dict) or message.get("method") != "featureStream":
        return None
    params = message.get("params")
    if not isinstance(params, list):
        return None
    return [json.loads(p) if isinstance(p, str) else p for p in params]


def _append_features(features: list[Any], output_path: Path, area: str | None) -> None:
    """Stamp features with the receive time (and area) and append them as NDJSON."""
    received_at = datetime.now(UTC).isoformat()
    with output_path.open("a", encoding="utf-8") as f:
        for record in features:
            record["requested_at"] = received_at
            if area is not None:
                record["area"] = area
            json.dump(record, f)
            f.write("\n")


def poll_once(
    websocket: ClientConnection,
    request_params: dict[str, Any],
    output_path: Path,
    area: str | None = None,
) -> int:
    """Send one request, receive the response, and append features to the output file."""
    websocket.send(json.dumps(request_params))
    message = json.loads(websocket.recv())
    _check_jsonrpc_error(message)
    features = _features(message) or []
    _append_features(features, output_path, area)
    return len(features)


def poll_area(config: Config, area: Area, stop: threading.Event = _stop_event) -> None:
    """Poll one area on its own socket until stop is set, reconnecting on errors."""
    # This area's own request; its "id" is incremented in place across ticks and reconnects.
    request_params = config.request_for(area)
    area.output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("[%s] polling every %ds → %s", area.name, config.interval, area.output_path)

    backoff = 1
    while not stop.is_set():
        try:
            with connect(config.wss_url) as websocket:
                backoff = 1
                while not stop.is_set():
                    n = poll_once(websocket, request_params, area.output_path, area.name)
                    log.info("[%s] wrote %d records (id=%s)", area.name, n, request_params["id"])
                    request_params["id"] += 1
                    stop.wait(config.interval)
        except (WebSocketException, OSError) as e:
            if stop.is_set():
                break
            log.warning("[%s] connection error: %s; reconnecting in %ds", area.name, e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    log.info("[%s] stopped", area.name)


def _drain(websocket: ClientConnection, config: Config, area: Area, stop: threading.Event) -> None:
    """Read every pushed message until stop is set, logging a summary every interval seconds.

    The socket must never sit unread: once websockets' receive queue fills it stops reading
    the network, pongs go unseen and the keepalive ping times out.
    """
    messages = records = unknown = 0
    last_report = time.monotonic()
    while not stop.is_set():
        try:
            raw = websocket.recv(timeout=_RECV_TIMEOUT)
        except TimeoutError:
            raw = None
        if raw is not None:
            messages += 1
            message = json.loads(raw)
            _check_jsonrpc_error(message)
            # Snapshot answer to the subscribe first, then one notification per update.
            features = _features(message)
            if features is None:
                features = _pushed_features(message)
            if features is not None:
                _append_features(features, area.output_path, area.name)
                records += len(features)
            else:
                # Unexpected message type; show the first few of each connection.
                unknown += 1
                level = logging.INFO if unknown <= _UNKNOWN_SAMPLES else logging.DEBUG
                log.log(level, "[%s] non-feature message: %.500s", area.name, raw)
        elapsed = time.monotonic() - last_report
        if elapsed >= config.interval:
            log.info(
                "[%s] %d messages, %d records in the last %.0fs",
                area.name,
                messages,
                records,
                elapsed,
            )
            messages = records = 0
            last_report += elapsed


def stream_area(config: Config, area: Area, stop: threading.Event = _stop_event) -> None:
    """Subscribe once per connection and write every pushed feature until stop is set."""
    # As in poll_area, "id" is incremented in place: once per subscription, across reconnects.
    request_params = config.request_for(area)
    area.output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("[%s] streaming → %s", area.name, area.output_path)

    backoff = 1
    while not stop.is_set():
        try:
            with connect(config.wss_url) as websocket:
                websocket.send(json.dumps(request_params))
                log.info("[%s] subscribed (id=%s)", area.name, request_params["id"])
                request_params["id"] += 1
                backoff = 1
                _drain(websocket, config, area, stop)
        except (WebSocketException, OSError) as e:
            if stop.is_set():
                break
            log.warning("[%s] connection error: %s; reconnecting in %ds", area.name, e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    log.info("[%s] stopped", area.name)


def run(config: Config) -> None:
    """Register signal handlers and poll every area in parallel until SIGINT/SIGTERM or duration."""
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    mode = "streaming" if config.streaming else "polling"
    log.info("%s %s for %d area(s)", mode, config.wss_url, len(config.areas))

    target = stream_area if config.streaming else poll_area
    threads = [
        threading.Thread(target=target, args=(config, area), name=f"area-{area.name}")
        for area in config.areas
    ]
    for t in threads:
        t.start()

    deadline = time.monotonic() + config.duration if config.duration is not None else None

    # Join with a timeout so the main thread stays responsive to signals,
    # duration, and thread crashes.
    while not _stop_event.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            log.info("duration of %gs reached, stopping", config.duration)
            _stop_event.set()
            break
        dead = [t for t in threads if not t.is_alive()]
        if dead:
            _stop_event.set()
            raise RuntimeError(f"worker thread {dead[0].name} terminated unexpectedly")
        for t in threads:
            t.join(timeout=0.1)

    for t in threads:
        t.join()

    log.info("stopped")
