# Network

The network directory contains the original OVS practice topology and the current BMv2/P4 demo path.

## OVS practice topology

- [topology.py](topology.py): create the three-host OVS network.
- All hosts share `10.0.0.0/24`.
- This path is separate from the MANTA P4 gateway demo.

## BMv2/P4 topology

- [bmv2_topology.py](bmv2_topology.py): start the BMv2-oriented Mininet topology.
- Public hosts are `client` (`10.0.0.1`) and `scanner` (`10.0.0.3`).
- Four protected VPS hosts connect to the internal switch in rotation order — `server` (`10.0.1.2`), `vps2` (`10.0.1.3`), `vps3` (`10.0.1.4`), and `vps4` (`10.0.1.5`). Each has its own network namespace, virtual Ethernet interface, and MAC address. The static mapping uses `server`.
- `bmv2_public` is BMv2 port 1.
- `bmv2_internal` is BMv2 port 2.
- P4Runtime listens on `127.0.0.1:9559`.

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
client ip neigh replace 10.0.0.100 lladdr 02:00:00:00:01:01 nud permanent dev client-eth0
client curl --fail --connect-timeout 5 http://10.0.0.100:8080/
```

Restarting BMv2 clears runtime table entries. Reinstall generation 1 after every fresh BMv2 start.

The P4 path is a TCP-only prototype. Address translation and generation rotation are implemented at prototype level; established-flow overlap tracking, multi-service selectors, and production telemetry are not implemented.

## Where to start

1. [Mininet walkthrough](https://mininet.org/walkthrough/): try host connectivity, custom topologies, and connecting a remote controller.
2. [Ryu switching tutorial](https://book.ryu-sdn.org/en/html/switching_hub.html): understand switch events and basic forwarding before adding mappings.
3. [Ryu OpenFlow 1.3 reference](https://ryu.readthedocs.io/en/latest/ofproto_v1_3_ref.html): look up `OFPMatch`, `OFPFlowMod`, and `OFPActionSetField` for packet matching and rewriting.
