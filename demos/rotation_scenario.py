"""Check each VPS backend from the demo's P4Runtime environment."""

import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from gateway.bmv2 import AsyncBmv2CoordinatorAdapter, Bmv2P4RuntimeGateway

from controller.rotator import (
    IdentityPool, JsonStateStore, MappingGeneration, PublicIdentity,
    RotationCoordinator, RotationRequest, RotationState,
)


log = logging.getLogger("demos.mininet_rotation")

FAILURE_REASONS = {
    "stage_failed": "the gateway could not prepare the new route",
    "verify_failed": "the gateway could not confirm the new route was ready",
    "handoff_failed": "the client did not confirm it was ready to switch",
    "activation_failed": "the gateway could not switch traffic to the new VPS",
    "retirement_failed": "the gateway could not remove the old route",
    "rollback_failed": "the gateway could not restore the previous route",
    "journal_write_failed": "the rotator could not save its progress",
    "TimeoutError": "a rotation step took too long",
}


class MininetClient:
    """Prepare the demo client's neighbor entry and probe from its network namespace."""

    def __init__(self, pid, interface, gateway_mac):
        self.namespace = ["nsenter", "--target", str(pid), "--net", "--"]
        self.interface = interface
        self.gateway_mac = gateway_mac

    async def prepare_and_wait(self, plan):
        ip = plan.next_identity.public_ip
        await asyncio.to_thread(
            subprocess.run, self.namespace + [
                "ip", "neigh", "replace", ip, "lladdr", self.gateway_mac,
                "nud", "permanent", "dev", self.interface,
            ], check=True, timeout=3,
        )
        log.info("The gateway has checked the new route. The client is ready to use %s:%s.",
                 ip, plan.next_identity.public_port)
        return True

    def probe(self, identity, marker, *, retired=False):
        address = f"{identity.public_ip}:{identity.public_port}"
        url = f"http://{address}/probe.txt"
        args = ["curl", "--disable", "--noproxy", "*", "--silent", "--show-error",
                "--fail", "--max-time", "3"]
        if not retired:
            args.extend(["--retry", "2", "--retry-connrefused"])
        result = subprocess.run(
            self.namespace + args + [url], capture_output=True, text=True, timeout=15,
        )
        if retired:
            if result.returncode == 0:
                raise RuntimeError(f"The old address {address} still answered after the switch.")
            if result.returncode != 28:
                raise RuntimeError(f"The client could not confirm that {address} timed out. "
                                   f"curl reported {result.stderr.strip()}")
            log.info("The old address %s timed out as expected.", address)
        else:
            name = marker.split()[0]
            if result.returncode:
                raise RuntimeError(f"The client could not reach {name} at {address}. "
                                   f"curl reported {result.stderr.strip()}")
            if result.stdout != marker:
                raise RuntimeError(f"{address} answered, but its response did not match {name}.")
            log.info("Confirmed. %s answered the client at %s.", name, address)


async def run(run_dir, client_pid, interface, gateway_mac, public_ip, *, debug=False):
    run_dir = Path(run_dir)
    backends = json.loads((run_dir / "backends.json").read_text(encoding="ascii"))
    endpoints = [PublicIdentity(host["ip"], 80) for host in backends]
    initial = MappingGeneration("manta-http", 1, PublicIdentity(public_ip, 8080),
                                endpoints[0])
    adapter = AsyncBmv2CoordinatorAdapter(Bmv2P4RuntimeGateway(
        backend_macs={host["ip"]: host["mac"] for host in backends}, trace_writes=debug))
    client = MininetClient(client_pid, interface, gateway_mac)
    store = JsonStateStore(run_dir / "journal.json", [initial])
    coordinator = RotationCoordinator(IdentityPool(), adapter, client, store,
                                      service_backends={initial.service_id: endpoints})
    try:
        serving = backends[0]["name"]
        log.info("Starting with %s. The client will check each of the %d VPS hosts, then return to %s.",
                 serving, len(backends), serving)
        if not await adapter.stage_mapping(initial):
            raise RuntimeError(f"The gateway could not prepare the first route to {serving}.")
        if not await adapter.verify_staged(initial):
            raise RuntimeError(f"The gateway could not verify the first route to {serving}.")
        if not await adapter.activate_mapping(initial, None):
            raise RuntimeError(f"The gateway could not enable the first route to {serving}.")
        await coordinator.recover()
        current = initial
        client.probe(current.identity, backends[0]["marker"])
        for number, expected in enumerate(backends[1:] + backends[:1], start=1):
            previous = current
            name = expected["name"]
            log.info("Switch %d of %d. Moving traffic from %s to %s.",
                     number, len(backends), serving, name)
            result = await coordinator.rotate(RotationRequest(uuid4().hex, initial.service_id))
            if result.state != RotationState.COMPLETE:
                reason = FAILURE_REASONS.get(result.error, result.error or result.state.value)
                raise RuntimeError(f"The switch from {serving} to {name} did not finish because {reason}.")
            current = store.load().active[initial.service_id]
            if current.internal_endpoint.public_ip != expected["ip"]:
                raise RuntimeError(f"The rotator selected {current.internal_endpoint.public_ip}. "
                                   f"This switch should have selected {name} at {expected['ip']}.")
            client.probe(current.identity, expected["marker"])
            serving = name
            client.probe(previous.identity, None, retired=True)
        log.info("PASS. All %d VPS hosts answered in order. The final request reached %s.",
                 len(backends), serving)
    finally:
        await coordinator.close()


if __name__ == "__main__":
    debug = os.environ.get("MANTA_ROTATION_DEBUG") == "1"
    logging.basicConfig(level=logging.INFO if debug else logging.ERROR,
                        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    log.setLevel(logging.INFO)
    try:
        asyncio.run(run(*sys.argv[1:], debug=debug))
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        log.error("FAIL. %s", error, exc_info=debug)
        sys.exit(1)
