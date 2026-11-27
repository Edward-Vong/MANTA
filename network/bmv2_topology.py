"""Routed Mininet sites connected through the MANTA BMv2 gateway."""

import argparse
from ipaddress import ip_interface
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import Switch
from mininet.topo import Topo


PUBLIC_GATEWAY_PORT = 1
INTERNAL_GATEWAY_PORT = 2
PUBLIC_GATEWAY_INTF = "bmv2_public"
INTERNAL_GATEWAY_INTF = "bmv2_internal"
PUBLIC_GATEWAY_IP = "172.30.0.2"
INTERNAL_GATEWAY_IP = "172.31.0.2"
PUBLIC_IDENTITY_IP = "10.0.0.100"
PUBLIC_GATEWAY_MAC = "02:00:00:00:01:01"
INTERNAL_GATEWAY_MAC = "02:00:00:00:01:02"
# These are the Ethernet next hops expected by the existing two-port P4 pipeline.
PUBLIC_TRANSIT_MAC = "02:00:00:00:00:01"
PRIVATE_TRANSIT_MAC = "02:00:00:00:00:02"
PUBLIC_SITES = (("client", "10.10.1.2/24"), ("scanner", "10.10.2.2/24"))
VPS_HOSTS = (
    ("vps1", "10.0.1.2/24", "02:00:00:00:02:01"),
    ("vps2", "10.0.2.2/24", "02:00:00:00:02:02"),
    ("vps3", "10.0.3.2/24", "02:00:00:00:02:03"),
    ("vps4", "10.0.4.2/24", "02:00:00:00:02:04"),
)
ORIGIN_CIDR = "10.0.5.2/24"
ORIGIN_SERVICE_IP = "192.0.2.80"
CLIENT_ROUTER_IP = str(ip_interface(PUBLIC_SITES[0][1]).network[1])
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
        "--log-console",
        "--log-level",
        "debug",
        "--log-flush",
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


class Bmv2Ports(Switch):
    """BMv2 owns these interfaces. Linux must not bridge the two transit links."""

    def start(self, controllers):
        for intf in self.intfList():
            if intf.name != "lo":
                intf.ifconfig("up")


class Bmv2MantaTopo(Topo):
    def build(self):
        gw = self.addSwitch("bmv2gw", cls=Bmv2Ports, dpid="0000000000000001")
        public = self.addHost("pubedge", ip="172.30.0.1/30", mac=PUBLIC_TRANSIT_MAC)
        private = self.addHost("privedge", ip="172.31.0.1/30", mac=PRIVATE_TRANSIT_MAC)
        self.addLink(public, gw, intfName1="pub-wan", intfName2=PUBLIC_GATEWAY_INTF,
                     port2=PUBLIC_GATEWAY_PORT)
        self.addLink(private, gw, intfName1="priv-wan", intfName2=INTERNAL_GATEWAY_INTF,
                     port2=INTERNAL_GATEWAY_PORT)
        for name, cidr in PUBLIC_SITES:
            self.add_site(public, name, cidr)
        for name, cidr, mac in VPS_HOSTS:
            self.add_site(private, name, cidr, mac)
        self.add_site(private, "server", ORIGIN_CIDR)

    def add_site(self, router, name, cidr, mac=None):
        gateway = ip_interface(cidr).network[1]
        host = self.addHost(name, ip=cidr, mac=mac, defaultRoute=f"via {gateway}")
        self.addLink(host, router, intfName2=f"to-{name}",
                     params2={"ip": f"{gateway}/{ip_interface(cidr).network.prefixlen}"})


def build_network():
    return Mininet(topo=Bmv2MantaTopo(), controller=None, build=True)


def _checked(node, args, *, stdin=None):
    with node.popen(args, stdin=subprocess.PIPE if stdin is not None else None,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        _, err = proc.communicate(stdin.encode() if stdin is not None else None)
        if proc.returncode:
            raise RuntimeError(f"{node.name}: {args[0]} failed: {err.decode().strip()}")


def _forwarding_rules(allowed):
    # Each edge lives in its own namespace. Apply its policy before enabling routing.
    return "\n".join(["*filter", ":INPUT DROP [0:0]", ":FORWARD DROP [0:0]",
                      ":OUTPUT ACCEPT [0:0]",
                      "-A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
                      *allowed, "-A FORWARD -j REJECT", "COMMIT", ""])


def configure_static_smoke_path(net):
    public, private = net["pubedge"], net["privedge"]
    _checked(public, ["ip", "neigh", "replace", PUBLIC_GATEWAY_IP, "lladdr",
                      PUBLIC_GATEWAY_MAC, "nud", "permanent", "dev", "pub-wan"])
    _checked(public, ["ip", "route", "replace", "10.0.0.0/24", "via", PUBLIC_GATEWAY_IP])
    _checked(private, ["ip", "neigh", "replace", INTERNAL_GATEWAY_IP, "lladdr",
                       INTERNAL_GATEWAY_MAC, "nud", "permanent", "dev", "priv-wan"])
    _checked(private, ["ip", "route", "replace", "10.10.1.0/24", "via", INTERNAL_GATEWAY_IP])

    client_ip = str(ip_interface(PUBLIC_SITES[0][1]).ip)
    _checked(public, ["iptables-restore"], stdin=_forwarding_rules([
        f"-A FORWARD -i to-client -o pub-wan -s {client_ip} -d 10.0.0.0/24 "
        "-p tcp --dport 8080 -j ACCEPT",
    ]))
    origin = ip_interface(ORIGIN_CIDR).ip
    allowed, nat = [], ["*nat", ":PREROUTING ACCEPT [0:0]", ":POSTROUTING ACCEPT [0:0]"]
    for name, cidr, _ in VPS_HOSTS:
        ip = ip_interface(cidr).ip
        # BMv2 translates the active public identity before this edge receives the request.
        allowed.append(f"-A FORWARD -i priv-wan -o to-{name} -s {client_ip} -d {ip} "
                       "-p tcp --dport 80 -j ACCEPT")
        nat.append(f"-A POSTROUTING -o to-{name} -s {client_ip} -d {ip} "
                   "-p tcp --dport 80 -j MASQUERADE")
        # Relays reach the origin only through its service address, on HTTP.
        nat.append(f"-A PREROUTING -i to-{name} -s {ip} -d {ORIGIN_SERVICE_IP} "
                   f"-p tcp --dport 80 -j DNAT --to-destination {origin}")
        allowed.append(f"-A FORWARD -i to-{name} -o to-server -s {ip} -d {origin} "
                       f"-p tcp --dport 80 -m conntrack --ctorigdst {ORIGIN_SERVICE_IP} -j ACCEPT")
    nat.append(f"-A POSTROUTING -o to-server -d {origin} -p tcp --dport 80 -j MASQUERADE")
    _checked(private, ["iptables-restore"],
             stdin=_forwarding_rules(allowed) + "\n".join([*nat, "COMMIT", ""]))
    for edge in (public, private):
        _checked(edge, ["sysctl", "-qw", "net.ipv4.ip_forward=1"])


def start_network(net):
    """Start Mininet, then BMv2, and return the BMv2 process."""

    net.start()
    configure_static_smoke_path(net)
    return start_bmv2()


def stop_network(net, bmv2_process=None):
    """Stop the scaffold and remove its Mininet namespaces and links."""

    stop_bmv2(bmv2_process)
    net.stop()


def run_rotation_demo(net, p4runtime_python, *, debug=False):
    """Forward the original VM command to the standalone demo."""
    from demos.mininet_rotation import run_rotation_demo as run_demo

    run_demo(net, p4runtime_python, debug=debug)


def run_network(p4runtime_python=None, *, debug=False):
    """Run the interactive topology or the HTTP rotation demo."""

    net = build_network()
    bmv2_process = None
    try:
        bmv2_process = start_network(net)
        if p4runtime_python is not None:
            try:
                run_rotation_demo(net, p4runtime_python, debug=debug)
            except subprocess.TimeoutExpired as error:
                raise SystemExit(f"FAIL. The demo did not finish within {error.timeout} seconds.") from None
            return
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
    parser = argparse.ArgumentParser(description=run_network.__doc__)
    parser.add_argument(
        "--rotation-demo", metavar="P4RUNTIME_PYTHON",
        help="rotate the HTTP service through the VPS hosts using this Python for P4Runtime",
    )
    parser.add_argument("--debug", action="store_true",
                        help="include coordinator state and P4Runtime writes during the demo")
    args = parser.parse_args()
    if args.rotation_demo and not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    run_network(args.rotation_demo, debug=args.debug)
