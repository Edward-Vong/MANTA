# Network

The network directory contains the original OVS practice topology and the current BMv2/P4 demo path.

## OVS practice topology

- [topology.py](topology.py): create the three-host OVS network.
- All hosts share `10.0.0.0/24`.
- This path is separate from the MANTA P4 gateway demo.

## BMv2/P4 topology

- [bmv2_topology.py](bmv2_topology.py): start the BMv2-oriented Mininet topology.
Each endpoint has a point-to-point link to a router in a separate namespace. No endpoint shares an Ethernet segment with another endpoint. The public and private edge routers connect to BMv2 on ports 1 and 2. P4Runtime listens on `127.0.0.1:9559` in the VM.

| Site | Private address | Permitted new connections |
|---|---|---|
| Client | `10.10.1.2` | Active public service on TCP 8080 |
| Scanner | `10.10.2.2` | No forwarding to other sites |
| VPS 1–4 | `10.0.1.2`, `10.0.2.2`, `10.0.3.2`, `10.0.4.2` | Origin service address `192.0.2.80:80` |
| Origin (`server`) | `10.0.5.2` | Replies to forwarded requests |

The routers enforce these paths with namespace-local IPv4 firewall rules. Source NAT hides the client's private address from the VPS and each VPS's private address from the origin. The origin service address is internal to this emulation. For a live isolation check, stop any running topology and run the following from the repository root in the VM:

```bash
sudo env MANTA_SITE_TESTS=1 python3 -m unittest discover -s tests -p test_site_isolation.py -v
```

This checks live HTTP listeners locally, then checks all 42 directions between the seven sites and verifies that each VPS can reach the origin service address. It uses Mininet, `iptables-restore`, and `curl`. The rotation demo separately checks the client-to-origin path through BMv2 and all four relays.

Build and start the demo in the Ubuntu VM:

```bash
bash network/build_p4.sh
sudo BMV2_SWITCH_BIN=/home/manta/opt/p4/bin/simple_switch_grpc \
  python3 network/bmv2_topology.py
```

The topology starts BMv2 after its Mininet interfaces exist and cleans up the BMv2 process during shutdown.

Use the [verbose rotation demo](../README.md#watch-a-rotation-in-the-vm) to exercise the protected HTTP service from the Mininet client and follow the coordinator in a second terminal.

## P4 artifacts and static mapping

- [build_p4.sh](build_p4.sh): compile `p4/manta_minimal.p4` into BMv2 JSON and P4Info.
- [install_static_mapping.py](install_static_mapping.py): install generation 1 through `Bmv2P4RuntimeGateway`.
- [p4/manta_minimal.p4](p4/manta_minimal.p4): minimal generation-aware forwarding and translation pipeline.

After starting BMv2, run:

```bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
"$HOME/venvs/p4runtime/bin/python" network/install_static_mapping.py
```

From the Mininet CLI:

```text
server python3 -m http.server 80 >/tmp/manta-http.log 2>&1 &
vps1 python3 -m demos.vps_proxy 10.0.1.2 192.0.2.80 vps1 >/tmp/manta-vps1.log 2>&1 &
client curl --fail --connect-timeout 5 http://10.0.0.100:8080/
```

Restarting BMv2 clears runtime table entries. Reinstall generation 1 after every fresh BMv2 start.

The P4 path is a TCP-only prototype. Address translation and generation rotation are implemented at prototype level; established-flow overlap tracking, multi-service selectors, and production telemetry are not implemented.

## Where to start

1. [Mininet walkthrough](https://mininet.org/walkthrough/): try host connectivity, custom topologies, and connecting a remote controller.
2. [Ryu switching tutorial](https://book.ryu-sdn.org/en/html/switching_hub.html): understand switch events and basic forwarding before adding mappings.
3. [Ryu OpenFlow 1.3 reference](https://ryu.readthedocs.io/en/latest/ofproto_v1_3_ref.html): look up `OFPMatch`, `OFPFlowMod`, and `OFPActionSetField` for packet matching and rewriting.
