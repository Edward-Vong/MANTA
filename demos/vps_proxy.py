"""HTTP relay for a Mininet VPS. The upstream is the origin's service address."""

from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys


class RelayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        upstream = HTTPConnection(self.server.origin, timeout=3)
        try:
            upstream.request("GET", self.path)
            reply = upstream.getresponse()
            body = reply.read()
        except (OSError, HTTPException):
            self.send_error(502, "Origin unavailable")
            return
        finally:
            upstream.close()
        self.send_response(reply.status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Manta-VPS", self.server.marker)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    bind_ip, origin, marker = sys.argv[1:]
    with ThreadingHTTPServer((bind_ip, 80), RelayHandler) as relay:
        relay.origin = origin
        relay.marker = marker
        relay.serve_forever()
