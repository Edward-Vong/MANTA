# Network

Use Mininet, Open vSwitch, and Ryu.

- [topology.py](topology.py): create the network.
- [controller.py](controller.py): forward traffic and apply IP/port mappings.

First goal: one fixed mapping works in both directions.
Files contain function stubs and TODOs only. This prototype uses OpenFlow; P4 is a separate later decision.

## Where to start

1. [Mininet walkthrough](https://mininet.org/walkthrough/): try host connectivity, custom topologies, and connecting a remote controller.
2. [Ryu switching tutorial](https://book.ryu-sdn.org/en/html/switching_hub.html): understand switch events and basic forwarding before adding mappings.
3. [Ryu OpenFlow 1.3 reference](https://ryu.readthedocs.io/en/latest/ofproto_v1_3_ref.html): look up `OFPMatch`, `OFPFlowMod`, and `OFPActionSetField` for packet matching and rewriting.
