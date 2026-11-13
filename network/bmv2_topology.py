"""MANTA's BMv2-oriented Mininet topology scaffold.

This entry point starts the minimal BMv2 pipeline but does not implement
address translation or MANTA gateway behavior.  The ``bmv2gw`` switch is an
interface placeholder: its two named interfaces identify the BMv2 public and
internal ports.
"""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.nodelib import LinuxBridge
from mininet.topo import Topo


PUBLIC_GATEWAY_PORT = 1
INTERNAL_GATEWAY_PORT = 2
PUBLIC_GATEWAY_INTF = "bmv2_public"
INTERNAL_GATEWAY_INTF = "bmv2_internal"
PUBLIC_GATEWAY_IP = "10.0.0.254"
INTERNAL_GATEWAY_IP = "10.0.1.254"
PUBLIC_GATEWAY_MAC = "02:00:00:00:01:01"
INTERNAL_GATEWAY_MAC = "02:00:00:00:01:02"
CLIENT_MAC = "02:00:00:00:00:01"
SCANNER_MAC = "02:00:00:00:00:03"
SERVER_MAC = "02:00:00:00:00:02"
BMV2_GRPC_ADDRESS = "127.0.0.1:9559"
BMV2_CPU_PORT = "3"
BMV2_PID_FILE = Path("/tmp/manta-simple-switch-grpc.pid")
BMV2_LOG_FILE = Path("/tmp/manta-simple-switch-grpc.log")
P4_BUILD_DIR = Path(__file__).resolve().parent / "build"
BMV2_JSON = P4_BUILD_DIR / "manta_minimal.json"
P4INFO = P4_BUILD_DIR / "manta_minimal.p4info.txt"


def resolve_bmv2_executable():
    """Resolve the BMv2 executable without searching arbitrary directories."""

    configured = os.environ.get("BMV2_SWITCH_BIN")
    if configured:
        return configured

    executable = shutil.which("simple_switch_grpc")
    if executable is not None:
        return executable

    raise RuntimeError("simple_switch_grpc was not found on PATH")


def bmv2_command(executable="simple_switch_grpc"):
    """Return the minimal BMv2/P4Runtime startup command."""

    return [
        executable,
        "-i",
        f"{PUBLIC_GATEWAY_PORT}@{PUBLIC_GATEWAY_INTF}",
        "-i",
        f"{INTERNAL_GATEWAY_PORT}@{INTERNAL_GATEWAY_INTF}",
        str(BMV2_JSON),
        "--",
        "--grpc-server-addr",
        BMV2_GRPC_ADDRESS,
        "--cpu-port",
        BMV2_CPU_PORT,
    ]


def _pid_is_bmv2(pid):
    """Check a PID file target before terminating it."""

    try:
        command_line = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ")
    except (FileNotFoundError, PermissionError):
        return False
    return b"simple_switch_grpc" in command_line and BMV2_GRPC_ADDRESS.encode() in command_line


def _terminate_bmv2_pid(pid):
    """Terminate a verified BMv2 process group."""

    if not _pid_is_bmv2(pid):
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


def cleanup_stale_bmv2():
    """Remove a stale process left by an interrupted topology run."""

    if not BMV2_PID_FILE.exists():
        return
    try:
        pid = int(BMV2_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None:
        _terminate_bmv2_pid(pid)
    BMV2_PID_FILE.unlink(missing_ok=True)


def verify_bmv2_interfaces():
    """Require the Mininet switch ports to be visible in the root namespace."""

    for interface in (PUBLIC_GATEWAY_INTF, INTERNAL_GATEWAY_INTF):
        subprocess.run(
            ["ip", "link", "show", "dev", interface],
            check=True,
            stdout=subprocess.DEVNULL,
        )


def start_bmv2():
    """Start the compiled pipeline after Mininet has created its interfaces."""

    cleanup_stale_bmv2()
    executable = resolve_bmv2_executable()
    missing_artifacts = [
        artifact for artifact in (BMV2_JSON, P4INFO) if not artifact.is_file()
    ]
    if missing_artifacts:
        raise RuntimeError(
            "BMv2 pipeline artifacts must be regular files; missing: "
            + ", ".join(str(artifact) for artifact in missing_artifacts)
            + "; run bash network/build_p4.sh first"
        )
    verify_bmv2_interfaces()
    log = BMV2_LOG_FILE.open("ab")
    try:
        process = subprocess.Popen(
            bmv2_command(executable),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()
    BMV2_PID_FILE.write_text(str(process.pid))
    time.sleep(0.5)
    if process.poll() is not None:
        cleanup_stale_bmv2()
        raise RuntimeError(f"simple_switch_grpc exited; see {BMV2_LOG_FILE}")
    return process


def stop_bmv2(process=None):
    """Stop the current or stale simple_switch_grpc process."""

    if process is not None:
        _terminate_bmv2_pid(process.pid)
    else:
        cleanup_stale_bmv2()
    BMV2_PID_FILE.unlink(missing_ok=True)


class Bmv2MantaTopo(Topo):
    """Two-segment topology with an explicit BMv2 interface placeholder."""

    def build(self):
        pubside = self.addSwitch("pubside", dpid="0000000000000002")
        intside = self.addSwitch("intside", dpid="0000000000000003")

        client = self.addHost("client", ip="10.0.0.1/24", mac=CLIENT_MAC)
        scanner = self.addHost("scanner", ip="10.0.0.3/24", mac=SCANNER_MAC)
        server = self.addHost("server", ip="10.0.1.2/24", mac=SERVER_MAC)
        bmv2gw = self.addSwitch(
            "bmv2gw",
            cls=LinuxBridge,
            dpid="0000000000000001",
        )

        # Public-side hosts share 10.0.0.0/24 with the future BMv2 port 1.
        self.addLink(client, pubside, port2=1)
        self.addLink(scanner, pubside, port2=2)
        self.addLink(
            bmv2gw,
            pubside,
            port1=PUBLIC_GATEWAY_PORT,
            port2=3,
            intfName1=PUBLIC_GATEWAY_INTF,
        )

        # The protected server is isolated on 10.0.1.0/24 with future BMv2 port 2.
        self.addLink(
            bmv2gw,
            intside,
            port1=INTERNAL_GATEWAY_PORT,
            port2=1,
            intfName1=INTERNAL_GATEWAY_INTF,
        )
        self.addLink(server, intside, port2=2)


def build_network():
    """Build, but do not start, the BMv2-oriented Mininet network."""

    return Mininet(
        topo=Bmv2MantaTopo(),
        controller=None,
        switch=LinuxBridge,
        build=True,
    )


def configure_static_smoke_path(net):
    """Install the prototype routes and static neighbour entries in hosts."""

    client = net["client"]
    server = net["server"]
    client_intf = client.defaultIntf().name
    server_intf = server.defaultIntf().name

    commands = (
        (
            client,
            f"ip route replace 10.0.1.0/24 via {PUBLIC_GATEWAY_IP} "
            f"dev {client_intf}",
        ),
        (
            client,
            f"ip neigh replace {PUBLIC_GATEWAY_IP} lladdr {PUBLIC_GATEWAY_MAC} "
            f"nud permanent dev {client_intf}",
        ),
        (
            server,
            f"ip route replace 10.0.0.0/24 via {INTERNAL_GATEWAY_IP} "
            f"dev {server_intf}",
        ),
        (
            server,
            f"ip neigh replace {INTERNAL_GATEWAY_IP} lladdr {INTERNAL_GATEWAY_MAC} "
            f"nud permanent dev {server_intf}",
        ),
    )
    for host, command in commands:
        output = host.cmd(command)
        if output.strip():
            raise RuntimeError(f"failed to configure {host.name}: {output.strip()}")


def start_network(net):
    """Start Mininet, then BMv2, and return the BMv2 process."""

    net.start()
    configure_static_smoke_path(net)
    return start_bmv2()


def stop_network(net, bmv2_process=None):
    """Stop the scaffold and remove its Mininet namespaces and links."""

    stop_bmv2(bmv2_process)
    net.stop()


def run_network():
    """Start the scaffold, expose the CLI, and clean up on exit."""

    net = build_network()
    bmv2_process = None
    try:
        bmv2_process = start_network(net)
        info("\n*** BMv2 simple_switch_grpc is running with the minimal P4 pipeline.\n")
        info(
            "*** BMv2 ports: "
            f"{PUBLIC_GATEWAY_PORT}@{PUBLIC_GATEWAY_INTF} public, "
            f"{INTERNAL_GATEWAY_PORT}@{INTERNAL_GATEWAY_INTF} internal\n"
        )
        info("*** Type exit to stop the scaffold.\n\n")
        CLI(net)
    finally:
        stop_network(net, bmv2_process)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run inside the Linux VM: sudo python3 network/bmv2_topology.py")
    setLogLevel("info")
    run_network()
