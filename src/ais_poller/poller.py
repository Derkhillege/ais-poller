import json
import logging
import signal
import threading
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


def _stop(signum: int, _frame: FrameType | None) -> None:
    _stop_event.set()
    log.info("shutdown requested (signal %s)", signum)


def poll_once(
    websocket: ClientConnection,
    request_params: dict[str, Any],
    output_path: Path,
    area: str | None = None,
) -> int:
    """Send one request, receive the response, and append features to the output file."""
    websocket.send(json.dumps(request_params))
    response = websocket.recv()
    received_at = datetime.now(UTC).isoformat()
    features = json.loads(response).get("result", {}).get("features", [])
    with output_path.open("a") as f:
        for record in features:
            record["requested_at"] = received_at
            if area is not None:
                record["area"] = area
            json.dump(record, f)
            f.write("\n")
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
        except json.JSONDecodeError as e:
            log.error("[%s] malformed response, skipping tick: %s", area.name, e)
            stop.wait(config.interval)

    log.info("[%s] stopped", area.name)


def run(config: Config) -> None:
    """Register signal handlers and poll every area in parallel until SIGINT/SIGTERM."""
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("polling %s for %d area(s)", config.wss_url, len(config.areas))

    threads = [
        threading.Thread(target=poll_area, args=(config, area), name=f"area-{area.name}")
        for area in config.areas
    ]
    for t in threads:
        t.start()
    # Join with a timeout so the main thread stays responsive to signals.
    while any(t.is_alive() for t in threads):
        for t in threads:
            t.join(timeout=0.5)

    log.info("stopped")
