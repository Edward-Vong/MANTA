"""A small practice network. Run inside a Linux VM with sudo python3."""

import os

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSBridge


def build_network():
    # OVSBridge learns MAC addresses and forwards like a normal Ethernet switch.
    # No controller or P4 program is needed for this first connectivity demo.
    net = Mininet(controller=None, switch=OVSBridge, build=False)
    try:
        switch = net.addSwitch("s1")

        # All three hosts are on the same subnet, so they need no router.
        client = net.addHost("client", ip="10.0.0.1/24")
        server = net.addHost("server", ip="10.0.0.2/24")
        scanner = net.addHost("scanner", ip="10.0.0.3/24")

        # Each link acts like an Ethernet cable. Fix switch ports for clarity.
        net.addLink(client, switch, port2=1)
        net.addLink(server, switch, port2=2)
        net.addLink(scanner, switch, port2=3)
        net.build()
        return net
    except BaseException:
        net.stop()
        raise


def run_network():
    net = build_network()
    try:
        net.start()
        info("\n*** Checking connectivity between every pair of hosts\n")
        net.pingAll()
        info("\n*** Try: client ping -c 3 server\n")
        info("*** Type exit to stop the network.\n\n")
        CLI(net)
    finally:
        # Remove the virtual hosts, switch, and links even if the demo fails.
        net.stop()


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run inside your Linux VM: sudo python3 network/topology.py")
    setLogLevel("info")
    run_network()
