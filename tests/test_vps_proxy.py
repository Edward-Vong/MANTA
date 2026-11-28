import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from demos.rotation_scenario import MininetClient
from demos.vps_proxy import RelayHandler
from controller.rotator import PublicIdentity


class RelayTests(unittest.TestCase):
    def test_relay_fetches_origin_and_identifies_the_forwarder(self):
        handler = Mock(path="/probe.txt", wfile=io.BytesIO(),
                       server=SimpleNamespace(origin="192.0.2.80", marker="vps3 run-token"))
        with patch("demos.vps_proxy.HTTPConnection") as connect:
            upstream = connect.return_value
            upstream.getresponse.return_value = Mock(status=200, read=lambda: b"origin-token")
            RelayHandler.do_GET(handler)
            connect.assert_called_once_with("192.0.2.80", timeout=3)
            upstream.request.assert_called_once_with("GET", "/probe.txt")
            upstream.close.assert_called_once()
        self.assertEqual(handler.wfile.getvalue(), b"origin-token")
        handler.send_header.assert_any_call("X-Manta-VPS", "vps3 run-token")

    def test_missing_origin_returns_gateway_error(self):
        handler = Mock(path="/probe.txt", wfile=io.BytesIO())
        with patch("demos.vps_proxy.HTTPConnection") as connect:
            connect.return_value.request.side_effect = ConnectionRefusedError()
            RelayHandler.do_GET(handler)
        handler.send_error.assert_called_once_with(502, "Origin unavailable")
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_demo_requires_both_the_expected_relay_and_origin(self):
        client = MininetClient(123, "client-eth0", "10.10.1.1", "web", None)
        address = PublicIdentity("10.0.0.101", 8080)
        with patch("demos.rotation_scenario.subprocess.run") as curl:
            curl.return_value.returncode = 0
            for marker, body, valid in [("vps2 token", "origin", True),
                                        ("vps1 token", "origin", False),
                                        ("vps2 token", "wrong", False)]:
                with self.subTest(marker=marker, body=body):
                    curl.return_value.stdout = f"HTTP/1.0 200 OK\nX-Manta-VPS: {marker}\n\n{body}"
                    if valid:
                        client.probe(address, {"name": "vps2", "marker": "vps2 token"}, "origin")
                    else:
                        with self.assertRaises(RuntimeError):
                            client.probe(address, {"name": "vps2", "marker": "vps2 token"}, "origin")
