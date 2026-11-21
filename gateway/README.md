# Gateway

The gateway directory owns the control-plane boundary between the Rotation Coordinator and the P4Runtime data plane.

## Main files

- [models.py](models.py): `PublicIdentity`, `InternalEndpoint`, `MappingGeneration`, lifecycle states, and `GatewayResult`.
- [interface.py](interface.py): synchronous `GatewayRuleInterface`.
- [fake.py](fake.py): deterministic in-memory gateway for lifecycle and failure checks.
- [bmv2.py](bmv2.py): `Bmv2P4RuntimeGateway` and the asynchronous `AsyncBmv2CoordinatorAdapter`.

## BMv2 adapter

`Bmv2P4RuntimeGateway` connects to BMv2 P4Runtime at `127.0.0.1:9559`, loads the generated P4Info and BMv2 JSON, performs master arbitration, and writes generation-aware mapping and selector-table entries.

The coordinator-facing adapter is:

```python
from gateway.bmv2 import (
    Bmv2P4RuntimeGateway,
    AsyncBmv2CoordinatorAdapter,
)

gateway = AsyncBmv2CoordinatorAdapter(
    Bmv2P4RuntimeGateway()
)
```

It provides asynchronous generation-object methods while translating to the synchronous, service-scoped gateway interface:

- `active_generation_id()`;
- `stage_mapping(generation)`;
- `verify_staged(generation)`;
- `activate_mapping(new_generation, previous_generation)`;
- `retire_generation(generation)`;
- `rollback_to(generation)`.

## Runtime workflow

Build and start the network with [network/README.md](../network/README.md), then install generation 1:

```bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
"$HOME/venvs/p4runtime/bin/python" network/install_static_mapping.py
```

The current coordinator rotation check is an inline Python harness; there is no committed rotation runner. The expected lifecycle is:

```text
stage → verify → handoff → activate → retire
```

## Limitations

- TCP-only prototype;
- one protected HTTP service in the demo;
- selector state is controlled by P4Runtime table entries;
- no established-flow overlap tracking;
- no multi-service selector state;
- no production telemetry or scheduler;
- generated files under `network/build/` are artifacts, not gateway source.
