"""Receive policy feedback over HTTP and apply it to the rotation interval."""

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from threading import Lock, Thread


INTERVAL_PATH = "/rotation/interval"
CONFIG_PATH = Path(__file__).resolve().with_name("rotation_config.json")
_INTERVAL_STEPS = {"good": -1, "poor": 1}
_MAX_BODY_BYTES = 256


class RotationInterval:
    """Share a positive rotation interval between the scheduler and the HTTP thread."""

    def __init__(self, seconds: int = 10):
        if type(seconds) is not int or seconds < 1:
            raise ValueError("The rotation interval must be a positive integer")
        self._seconds = seconds
        self._lock = Lock()

    @property
    def seconds(self) -> int:
        with self._lock:
            return self._seconds

    def adjust(self, reason: str) -> int:
        """Apply good or poor feedback and return the updated interval in seconds."""
        step = _INTERVAL_STEPS[reason.lower()]
        with self._lock:
            self._seconds = max(1, self._seconds + step)
            return self._seconds


def _configured_port() -> int:
    """Read the listener port from the controller config file."""
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not read {CONFIG_PATH}: {error}") from error

    port = config.get("port") if isinstance(config, dict) else None
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"{CONFIG_PATH}: port must be an integer from 1 to 65535")
    return port


def _create_server(adjust_interval: Callable[[str], int], port: int | None = None) -> HTTPServer:
    """Bind a loopback HTTP server to a caller-supplied interval update function."""

    class RotationHandler(BaseHTTPRequestHandler):
        timeout = 3

        def do_POST(self):
            if self.path != INTERVAL_PATH:
                return self._reply(404, {"error": "Unknown endpoint"})
            if self.headers.get_content_type() != "application/json":
                return self._reply(415, {"error": "Expected application/json"})

            lengths = self.headers.get_all("Content-Length", [])
            try:
                length = int(lengths[0]) if len(lengths) == 1 else 0
            except ValueError:
                length = 0
            if "Transfer-Encoding" in self.headers or length <= 0:
                return self._reply(400, {"error": "Expected a JSON body with Content-Length"})
            if length > _MAX_BODY_BYTES:
                return self._reply(413, {"error": "Request body is too large"})

            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    return self._reply(400, {"error": "Incomplete request body"})
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                return self._reply(400, {"error": "Invalid JSON"})

            reason = payload.get("reason") if isinstance(payload, dict) else None
            if not isinstance(reason, str) or reason.lower() not in _INTERVAL_STEPS:
                return self._reply(400, {"error": "Expected reason to be Good or Poor"})

            self._reply(200, {"interval_seconds": adjust_interval(reason.lower())})

        def _reply(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return HTTPServer(("127.0.0.1", _configured_port() if port is None else port), RotationHandler)


@contextmanager
def serve_rotation_api(adjust_interval: Callable[[str], int], *, port: int | None = None):
    """Serve the API on a background thread for the lifetime of the caller's context."""
    with _create_server(adjust_interval, port) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def main():
    """Run a standalone listener with a demo interval for trying the HTTP contract."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.parse_args()

    interval = RotationInterval()
    try:
        with _create_server(interval.adjust) as server:
            print(f"Listening on http://127.0.0.1:{server.server_port}{INTERVAL_PATH}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not start rotation API: {error}\n")


if __name__ == "__main__":
    main()
