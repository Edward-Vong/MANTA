# MANTA
MANTA: Moving-target Architecture for Network Threat Avoidance

## Architecture

The current prototype follows this control and data path:

```text
Policy Manager → Rotation Coordinator → AsyncBmv2CoordinatorAdapter
→ Bmv2P4RuntimeGateway → BMv2/P4 pipeline → protected HTTP service
```

- The Policy Manager owns timing and rotation decisions.
- The Rotation Coordinator owns allocation, orchestration, lifecycle, persistence, and recovery.
- `AsyncBmv2CoordinatorAdapter` adapts the coordinator's asynchronous generation-object API.
- `Bmv2P4RuntimeGateway` performs P4Runtime table writes.
- BMv2 runs the compiled P4 pipeline and forwards traffic to the protected service.
- The P4/data-plane work owns `network/`, `network/p4/`, and `gateway/`.
- The Rotation Coordinator owns `controller/`.
- The Policy Manager owns `policy/`.
- Policy code must not manipulate P4Runtime or gateway tables directly.

See the [Network](network/README.md), [Gateway](gateway/README.md), and [Policy](policy/README.md) READMEs for component details.

## VM prerequisites

Run the P4 demo in the Ubuntu VM with:

- `p4c`;
- BMv2 `simple_switch_grpc`;
- Mininet;
- the P4Runtime Python virtual environment at `$HOME/venvs/p4runtime`;
- Python P4Runtime bindings and gRPC dependencies.

When required by the installed protobuf bindings, set:

```bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
```

## Python cache files

Python creates `__pycache__` folders when it imports modules during runs and tests. Git ignores these folders and compiled `.pyc` and `.pyo` files throughout the repository. You can leave them in place.

To clear existing caches, first preview the paths from the repository root.

```bash
git clean -ndX -- ':(glob)**/__pycache__' ':(glob)**/*.pyc' ':(glob)**/*.pyo'
```

After reviewing the preview, replace `-ndX` with `-fdX` to remove those paths. To keep new caches out of the checkout during a VM demo, add `PYTHONDONTWRITEBYTECODE=1` immediately after `sudo` in the launch command. The demo's Python subprocesses inherit that setting.

## Clean-start demo

From the repository root in the VM:

```bash
bash network/build_p4.sh
sudo BMV2_SWITCH_BIN=/home/manta/opt/p4/bin/simple_switch_grpc \
  python3 network/bmv2_topology.py
```

In the Mininet CLI, start the protected HTTP service and configure the client neighbor:

```text
server python3 -m http.server 80 >/tmp/manta-http.log 2>&1 &
client ip neigh replace 10.0.0.100 lladdr 02:00:00:00:01:01 nud permanent dev client-eth0
```

In a second VM terminal, install generation 1:

```bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
"$HOME/venvs/p4runtime/bin/python" network/install_static_mapping.py
```

Test the mapping from the Mininet CLI:

```text
client curl --fail --connect-timeout 5 http://10.0.0.100:8080/
```

The BMv2 JSON and P4Info configure the pipeline. The mapping installer and gateway adapter install runtime table entries. Restarting BMv2 clears runtime table entries, so generation 1 must be installed again after every fresh BMv2 start.

## Rotation Coordinator integration

The coordinator uses the gateway adapter as follows:

```python
from gateway.bmv2 import (
    Bmv2P4RuntimeGateway,
    AsyncBmv2CoordinatorAdapter,
)

gateway = AsyncBmv2CoordinatorAdapter(
    Bmv2P4RuntimeGateway()
)
```

The adapter exposes the coordinator-facing asynchronous operations:

- `active_generation_id()`;
- `stage_mapping(generation)`;
- `verify_staged(generation)`;
- `activate_mapping(new_generation, previous_generation)`;
- `retire_generation(generation)`;
- `rollback_to(generation)`.

It translates them to the synchronous, service-scoped `GatewayRuleInterface` implemented by `Bmv2P4RuntimeGateway`.

### Watch a rotation in the VM

Open two terminals in the Ubuntu VM. In the first, start watching the rotator log.

```bash
touch "$HOME/manta-rotator.log"
tail -n 0 -F "$HOME/manta-rotator.log"
```

In the second, run the following from the repository root with the interactive topology stopped.

```bash
bash network/build_p4.sh
sudo BMV2_SWITCH_BIN=/home/manta/opt/p4/bin/simple_switch_grpc \
  python3 -m demos.mininet_rotation --p4runtime-python "$HOME/venvs/p4runtime/bin/python" \
  2>>"$HOME/manta-rotator.log"
```

This starts an HTTP process in each of four protected Mininet hosts. The coordinator rotates through the hosts in round-robin order. Each generation selects a public address and a backend. BMv2 forwards to the selected backend's IP and MAC through rules installed using `AsyncBmv2CoordinatorAdapter`.

| Generation | Client connects to | VPS that must respond |
|---|---|---|
| 1 | `10.0.0.100:8080` | `server` at `10.0.1.2:80` |
| 2 | `10.0.0.101:8080` | `vps2` at `10.0.1.3:80` |
| 3 | `10.0.0.100:8080` | `vps3` at `10.0.1.4:80` |
| 4 | `10.0.0.101:8080` | `vps4` at `10.0.1.5:80` |
| 5 | `10.0.0.100:8080` | `server` at `10.0.1.2:80` |

Each VPS serves a distinct per-run marker. In the watch terminal, a switch looks like this (timestamps omitted).

```text
Switch 1 of 4. Moving traffic from server to vps2.
The gateway has checked the new route. The client is ready to use 10.0.0.101:8080.
Confirmed. vps2 answered the client at 10.0.0.101:8080.
The old address 10.0.0.100:8080 timed out as expected.
```

The confirmation appears when the client receives the expected VPS's marker. The timeout is a successful check that the previous public address has stopped responding. The final `PASS` appears after all four switches and their checks. If a check fails, the demo prints the reason and exits with an error. For troubleshooting, add `--debug` to the demo command to include request IDs and coordinator state in the log and raw P4Runtime writes in the launch terminal.

The demo prepares the new neighbor entry inside the client's namespace. Authenticated client notifications are still pending. The topology shuts down after the checks and the log remains available.

Expected rotation results are:

```text
stage succeeds → verification succeeds → handoff completes
→ activation succeeds → retirement succeeds
→ new identity works → retired identity fails
```

An unmapped public identity must also fail.

## Current limitations

- TCP-only prototype;
- one protected HTTP service in the demo;
- no established-flow overlap tracking;
- no production telemetry collector;
- no production scheduler;
- no multi-service selector state;
- selector state is controlled through P4Runtime table entries;
- `network/build/` contains generated artifacts, not source files, and should not be committed unless explicitly required.
