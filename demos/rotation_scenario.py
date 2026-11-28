"""Check each VPS backend from the demo's P4Runtime environment."""

import asyncio
from email.parser import Parser
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from gateway.bmv2 import AsyncBmv2CoordinatorAdapter, Bmv2P4RuntimeGateway

from controller.handoff import HandoffControl, HandoffSession, acknowledge, read_update
from controller.rotator import (
    IdentityPool, JsonStateStore, MappingGeneration, PublicIdentity,
    RotationConfig, RotationCoordinator, RotationRequest, RotationState,
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
    """Prepare a route through the site's router and probe from the client namespace."""

    def __init__(self, pid, interface, gateway_ip, service_id, key_path):
        self.namespace = ["nsenter", "--target", str(pid), "--net", "--"]
        self.interface = interface
        self.gateway_ip = gateway_ip
        self.service_id = service_id
        self.key_path = key_path

    async def exchange(self, update):
        process = await asyncio.create_subprocess_exec(
            *self.namespace, sys.executable, "-m", "demos.rotation_scenario", "--client-handoff",
            self.service_id, self.interface, self.gateway_ip, str(self.key_path),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        try:
            reply, _ = await process.communicate(json.dumps(update).encode())
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode:
            raise OSError("The Mininet client could not return a handoff acknowledgment")
        return json.loads(reply)

    def probe(self, identity, backend, origin_marker, *, retired=False):
        address = f"{identity.public_ip}:{identity.public_port}"
        url = f"http://{address}/probe.txt"
        args = ["curl", "--disable", "--noproxy", "*", "--include", "--silent", "--show-error",
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
            name = backend["name"]
            if result.returncode:
                raise RuntimeError(f"The client could not reach {name} at {address}. "
                                   f"curl reported {result.stderr.strip()}")
            headers, _, body = result.stdout.partition("\n\n")
            fields = Parser().parsestr(headers.partition("\n")[2])
            if fields.get("X-Manta-VPS") != backend["marker"] or body != origin_marker:
                raise RuntimeError(f"{address} did not return the origin's probe through {name}.")
            log.info("Confirmed. The origin answered through %s at %s.", name, address)


def client_handoff(service_id, interface, gateway_ip, key_path):
    """Handle one private pipe exchange inside the client's network namespace."""
    key = Path(key_path).read_bytes()
    update = json.load(sys.stdin)
    payload = read_update(update, key, service_id, "client")
    identity = PublicIdentity(**payload["next_identity"])
    command = ["ip", "route", "replace", f"{identity.public_ip}/32", "via", gateway_ip,
               "dev", interface]
    prepared = subprocess.run(command, timeout=1, check=False).returncode == 0
    if prepared:
        log.info("The gateway has checked the new route. The client is ready to use %s:%s.",
                 identity.public_ip, identity.public_port)
    json.dump(acknowledge(update, key, ready=prepared), sys.stdout)


async def run(run_dir, client_pid, interface, gateway_ip, public_ip, *, debug=False):
    run_dir = Path(run_dir)
    backends = json.loads((run_dir / "backends.json").read_text(encoding="ascii"))
    origin_marker = (run_dir / "origin" / "probe.txt").read_text(encoding="ascii")
    endpoints = [PublicIdentity(host["ip"], 80) for host in backends]
    initial = MappingGeneration("manta-http", 1, PublicIdentity(public_ip, 8080),
                                endpoints[0])
    adapter = AsyncBmv2CoordinatorAdapter(Bmv2P4RuntimeGateway(trace_writes=debug))
    store = JsonStateStore(run_dir / "journal.json", [initial])
    key = os.urandom(32)
    with tempfile.NamedTemporaryFile(dir=run_dir, delete=False) as key_file:
        key_file.write(key)
    key_path = Path(key_file.name)
    client = MininetClient(client_pid, interface, gateway_ip, initial.service_id, key_path)
    config = RotationConfig(handoff_attempts=2, handoff_ack_timeout_ms=2000)
    session = HandoffSession(initial.service_id, "client", key, client.exchange)
    handoff = HandoffControl([session], config)
    coordinator = RotationCoordinator(IdentityPool(), adapter, handoff, store, config,
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
        client.probe(current.identity, backends[0], origin_marker)
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
            client.probe(current.identity, expected, origin_marker)
            serving = name
            client.probe(previous.identity, None, None, retired=True)
        log.info("PASS. All %d VPS hosts forwarded to the origin in order. The final request used %s.",
                 len(backends), serving)
    finally:
        try:
            await coordinator.close()
        finally:
            key_path.unlink(missing_ok=True)


if __name__ == "__main__":
    debug = os.environ.get("MANTA_ROTATION_DEBUG") == "1"
    logging.basicConfig(level=logging.INFO if debug else logging.ERROR,
                        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    log.setLevel(logging.INFO)
    try:
        if sys.argv[1:2] == ["--client-handoff"]:
            client_handoff(*sys.argv[2:])
        else:
            asyncio.run(run(*sys.argv[1:], debug=debug))
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        log.error("FAIL. %s", error, exc_info=debug)
        sys.exit(1)
