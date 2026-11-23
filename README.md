# MANTA
MANTA: Moving-target Adaptive Network Transformation Architecture

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

There is no committed coordinator rotation runner. The current runtime rotation verification uses an inline Python harness with the adapter. A coordinator teammate should run that harness in a second VM terminal after starting BMv2, then test the new identity and confirm the retired identity times out from the Mininet CLI.

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
