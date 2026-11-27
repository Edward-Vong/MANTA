"""Set MANTA_SITE_TESTS=1 in the Linux VM to exercise the real Mininet links."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


@unittest.skipUnless(os.environ.get("MANTA_SITE_TESTS") == "1", "requires the Mininet VM")
class SiteIsolationTests(unittest.TestCase):
    def test_private_http_isolation_and_origin_service(self):
        from network.bmv2_topology import (
            ORIGIN_SERVICE_IP, PUBLIC_SITES, VPS_HOSTS, build_network, configure_static_smoke_path,
        )

        net = build_network()
        self.addCleanup(net.stop)
        net.start()
        configure_static_smoke_path(net)
        names = [name for name, _ in PUBLIC_SITES] + [name for name, _, _ in VPS_HOSTS] + ["server"]
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        for name in names:
            # A known live listener at every destination prevents false passes from closed ports.
            directory = Path(root.name, name)
            directory.mkdir()
            (directory / "probe").write_text(name)
            proc = net[name].popen(
                [sys.executable, "-m", "http.server", "80", "--bind", net[name].IP(),
                 "--directory", str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.addCleanup(proc.wait)
            self.addCleanup(proc.terminate)

        def probe(source, ip, retry=False):
            args = ["curl", "--disable", "--noproxy", "*", "--silent", "--fail", "--max-time", "1"]
            if retry:
                args += ["--retry", "3", "--retry-connrefused"]
            with net[source].popen(args + [f"http://{ip}/probe"], stdout=subprocess.PIPE) as proc:
                body, _ = proc.communicate(timeout=15)
            return proc.returncode, body.decode()

        for name in names:
            self.assertEqual(probe(name, net[name].IP(), retry=True), (0, name))
        for source in names:
            for dest in names:
                if source != dest:
                    with self.subTest(source=source, dest=dest):
                        code, _ = probe(source, net[dest].IP())
                        self.assertIn(code, (7, 28))  # connection refused/unreachable or timed out
        for name, _, _ in VPS_HOSTS:
            self.assertEqual(probe(name, ORIGIN_SERVICE_IP), (0, "server"))
        for name, _ in PUBLIC_SITES:
            self.assertIn(probe(name, ORIGIN_SERVICE_IP)[0], (7, 28))
