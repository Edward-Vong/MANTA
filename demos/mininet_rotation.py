"""Launch the rotation demo from the repository root inside the Linux VM."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4


def run_rotation_demo(net, p4runtime_python, *, debug=False):
    """Start an independent HTTP process in each VPS and verify the handoffs."""
    from network.bmv2_topology import PUBLIC_GATEWAY_MAC, PUBLIC_IDENTITY_IP, VPS_HOSTS

    client = net["client"]
    with tempfile.TemporaryDirectory() as run_dir, ExitStack() as services:
        backends = []
        for name, _, _ in VPS_HOSTS:
            host = net[name]
            web_root = Path(run_dir, name)
            web_root.mkdir()
            marker = f"{name} {uuid4().hex}"
            (web_root / "probe.txt").write_text(marker, encoding="ascii")
            service = host.popen(
                [sys.executable, "-m", "http.server", "80", "--bind", host.IP(),
                 "--directory", str(web_root)], stdout=subprocess.DEVNULL,
                stderr=None if debug else sys.stdout,
            )
            services.callback(service.wait)
            services.callback(service.terminate)
            backends.append(dict(name=name, ip=host.IP(), mac=host.MAC(), marker=marker))
        Path(run_dir, "backends.json").write_text(json.dumps(backends), encoding="ascii")
        result = subprocess.run(
            [p4runtime_python, "-u", "-m", "demos.rotation_scenario", run_dir,
             str(client.pid), client.defaultIntf().name, PUBLIC_GATEWAY_MAC, PUBLIC_IDENTITY_IP],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
                 "MANTA_ROTATION_DEBUG": "1" if debug else "0"},
            timeout=120,
        )
        if result.returncode:
            raise SystemExit(result.returncode if result.returncode > 0 else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--p4runtime-python", metavar="PYTHON", help="P4Runtime environment's Python")
    mode.add_argument("--interactive", action="store_true", help="open the Mininet CLI")
    parser.add_argument("--debug", action="store_true", help="show coordinator and P4Runtime details")
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.exit(1, "Run this command with sudo inside the Linux VM.\n")

    from mininet.log import setLogLevel
    from network.bmv2_topology import run_network

    setLogLevel("info")
    run_network(args.p4runtime_python, debug=args.debug)


if __name__ == "__main__":
    main()
