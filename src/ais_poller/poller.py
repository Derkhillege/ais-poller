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

import json
import logging
import signal
import threading
import time
from datetime import UTC, datetime
from types import FrameType
from typing import Any

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from ais_poller.config import Area, Config
from ais_poller.sink import Sink, open_sink

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


def poll_once(websocket: ClientConnection, request_params: dict[str, Any], sink: Sink) -> int:
    """Send one request, receive the response, and write its features to sink."""
    websocket.send(json.dumps(request_params))
    message = json.loads(websocket.recv())
    _check_jsonrpc_error(message)
    features = _features(message) or []
    return sink.write(features, datetime.now(UTC))


def poll_area(config: Config, area: Area, stop: threading.Event = _stop_event) -> None:
    """Poll one area on its own socket until stop is set, reconnecting on errors."""
    # This area's own request; its "id" is incremented in place across ticks and reconnects.
    request_params = config.request_for(area)
    log.info("[%s] polling every %ds → %s", area.name, config.interval, area.output_path)
    with open_sink(config, area) as sink:
        _poll_loop(config, area, request_params, sink, stop)
    log.info("[%s] stopped", area.name)


def _poll_loop(
    config: Config, area: Area, request_params: dict[str, Any], sink: Sink, stop: threading.Event
) -> None:
    backoff = 1
    while not stop.is_set():
        try:
            with connect(config.wss_url) as websocket:
                backoff = 1
                while not stop.is_set():
                    n = poll_once(websocket, request_params, sink)
                    log.info("[%s] wrote %d records (id=%s)", area.name, n, request_params["id"])
                    request_params["id"] += 1
                    stop.wait(config.interval)
        except (WebSocketException, OSError) as e:
            if stop.is_set():
                break
            log.warning("[%s] connection error: %s; reconnecting in %ds", area.name, e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)


def _drain(
    websocket: ClientConnection, config: Config, area: Area, sink: Sink, stop: threading.Event
) -> None:
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
                records += sink.write(features, datetime.now(UTC))
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
    log.info("[%s] streaming → %s", area.name, area.output_path)
    with open_sink(config, area) as sink:
        _stream_loop(config, area, request_params, sink, stop)
    log.info("[%s] stopped", area.name)


def _stream_loop(
    config: Config, area: Area, request_params: dict[str, Any], sink: Sink, stop: threading.Event
) -> None:
    backoff = 1
    while not stop.is_set():
        try:
            with connect(config.wss_url) as websocket:
                websocket.send(json.dumps(request_params))
                log.info("[%s] subscribed (id=%s)", area.name, request_params["id"])
                request_params["id"] += 1
                backoff = 1
                _drain(websocket, config, area, sink, stop)
        except (WebSocketException, OSError) as e:
            if stop.is_set():
                break
            log.warning("[%s] connection error: %s; reconnecting in %ds", area.name, e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)


def run(config: Config) -> None:
    """Register signal handlers and poll every area in parallel until SIGINT/SIGTERM or duration."""
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    mode = "streaming" if config.streaming else "polling"
    log.info("%s %s for %d area(s) → %s", mode, config.wss_url, len(config.areas), config.format)

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
