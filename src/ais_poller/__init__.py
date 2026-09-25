import json
import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from dotenv import load_dotenv
from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

log = logging.getLogger("ais_poller")

_running = True


def _stop(signum: int, _frame: FrameType | None) -> None:
    global _running
    _running = False
    log.info("shutdown requested (signal %s)", signum)


def _load_config() -> tuple[str, dict[str, Any], int, Path]:
    """Load and validate required configuration from environment variables."""
    load_dotenv()
    try:
        wss_url = os.environ["EURIS_WSS"]
        request_params = json.loads(os.environ["REQUEST_PARAMS"])
        interval = int(os.environ["INTERVAL"])
    except KeyError as e:
        raise SystemExit(f"missing required env var: {e.args[0]}") from None
    except (ValueError, json.JSONDecodeError) as e:
        raise SystemExit(f"invalid env var: {e}") from e

    if not isinstance(request_params, dict) or "id" not in request_params:
        raise SystemExit("REQUEST_PARAMS must be a JSON object containing an 'id' field")

    output_path = Path(os.getenv("OUTPUT_PATH", "data.json"))
    return wss_url, request_params, interval, output_path


def _poll_once(
    websocket: ClientConnection,
    request_params: dict[str, Any],
    output_path: Path,
) -> int:
    """Send one request, receive the response, and append features to the output file."""
    websocket.send(json.dumps(request_params))
    response = websocket.recv()
    received_at = datetime.now(UTC).isoformat()
    features = json.loads(response).get("result", {}).get("features", [])
    with output_path.open("a") as f:
        for record in features:
            record["requested_at"] = received_at
            json.dump(record, f)
            f.write("\n")
    return len(features)


def main() -> None:
    """Entry point: configure logging, register signals, and run the polling loop."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    wss_url, request_params, interval, output_path = _load_config()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("polling %s every %ds → %s", wss_url, interval, output_path)

    backoff = 1
    while _running:
        try:
            with connect(wss_url) as websocket:
                backoff = 1
                while _running:
                    n = _poll_once(websocket, request_params, output_path)
                    log.info("wrote %d records (id=%s)", n, request_params["id"])
                    request_params["id"] += 1
                    time.sleep(interval)
        except (WebSocketException, OSError) as e:
            if not _running:
                break
            log.warning("connection error: %s; reconnecting in %ds", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except json.JSONDecodeError as e:
            log.error("malformed response, skipping tick: %s", e)
            time.sleep(interval)

    log.info("stopped")

