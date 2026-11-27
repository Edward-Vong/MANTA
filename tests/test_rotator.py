"""Run from the project root: python3 -m unittest discover -s tests -v"""

import asyncio
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call

from controller.handoff import HandoffControl, HandoffSession, acknowledge, read_update
from controller.rotator import (
    HandoffPlan,
    IdentityPool,
    JsonStateStore,
    MappingGeneration,
    PublicIdentity,
    RotationConfig,
    RotationCoordinator,
    RotationRequest,
    RotationState,
)


class IdentityPoolTests(unittest.TestCase):
    def test_pool_reserves_and_reuses_addresses(self):
        pool = IdentityPool()
        identities = [pool.allocate(f"web:{number}", "web", "tcp")
                      for number in range(1, 11)]

        self.assertEqual(
            {identity.public_ip for identity in identities},
            {f"10.0.0.{number}" for number in range(100, 110)},
        )
        self.assertEqual(pool.allocate("web:1", "web", "tcp"), identities[0])

        with self.assertRaises(RuntimeError):
            pool.allocate("web:11", "web", "tcp")

        pool.release(identities[0], "web:1")
        self.assertEqual(pool.allocate("web:11", "web", "tcp"), identities[0])

        pool.release(identities[0], "web:1")
        with self.assertRaises(RuntimeError):
            pool.allocate("web:12", "web", "tcp")


class RotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)

        self.old = MappingGeneration(
            "web", 1, PublicIdentity("10.0.0.100", 8080),
            PublicIdentity("10.0.0.2", 80),
        )
        self.pool = IdentityPool()
        self.store = JsonStateStore(Path(temporary.name) / "state.json", [self.old])

        # Stand-ins for the rotator's expected gateway and handoff interfaces.
        self.gateway = Mock()
        for method in ("stage_mapping", "verify_staged", "activate_mapping",
                       "retire_generation", "rollback_to"):
            setattr(self.gateway, method, AsyncMock(return_value=True))
        self.gateway.active_generation_id = AsyncMock(return_value=1)
        self.handoff = Mock(prepare_and_wait=AsyncMock(return_value=True))

        self.calls = Mock()
        self.calls.attach_mock(self.gateway, "gateway")
        self.calls.attach_mock(self.handoff, "handoff")

        self.rotator = RotationCoordinator(
            self.pool, self.gateway, self.handoff, self.store,
            RotationConfig(overlap_ms=0, retire_retry_ms=0),
        )
        self.addAsyncCleanup(self.rotator.close)
        await self.rotator.recover()
        self.calls.reset_mock()

    async def test_successful_rotation_changes_identity(self):
        result = await self.rotator.rotate(RotationRequest("rotate-1", "web"))
        new = self.store.load().active["web"]
        plan = HandoffPlan("rotate-1", "web", 1, 2, new.identity, 0)

        self.assertEqual(result.state, RotationState.COMPLETE)
        self.assertEqual(new.generation_id, 2)
        self.assertEqual(new.identity, PublicIdentity("10.0.0.101", 8080))
        self.assertEqual(new.internal_endpoint, self.old.internal_endpoint)
        self.assertEqual(self.calls.mock_calls, [
            call.gateway.stage_mapping(new),
            call.gateway.verify_staged(new),
            call.handoff.prepare_and_wait(plan),
            call.gateway.activate_mapping(new, self.old),
            call.gateway.retire_generation(self.old),
        ])

        self.assertEqual(self.pool.allocate("probe:1", "probe", "tcp"), self.old.identity)
        with self.assertRaises(ValueError):
            self.pool.reserve(new.identity, "another-owner")

    async def test_missing_client_ack_keeps_old_identity(self):
        self.handoff.prepare_and_wait.return_value = False

        result = await self.rotator.rotate(RotationRequest("rotate-2", "web"))
        staged = self.gateway.stage_mapping.call_args.args[0]

        self.assertEqual(result.state, RotationState.FAILED)
        self.assertEqual(result.error, "handoff_failed")
        self.assertEqual(self.store.load().active["web"], self.old)
        self.gateway.activate_mapping.assert_not_called()
        self.gateway.rollback_to.assert_awaited_once_with(self.old)
        self.gateway.retire_generation.assert_awaited_once_with(staged)

        self.assertEqual(self.pool.allocate("probe:1", "probe", "tcp"), staged.identity)
        with self.assertRaises(ValueError):
            self.pool.reserve(self.old.identity, "another-owner")

    async def test_vps_rotation_retries_same_backend_and_cycles_after_recovery(self):
        first = self.old.internal_endpoint
        second = PublicIdentity("10.0.0.4", 80)
        third = PublicIdentity("10.0.0.5", 80)
        fourth = PublicIdentity("10.0.0.6", 80)
        endpoints = {"web": (first, second, third, fourth)}
        await self.rotator.close()

        async def start_controller():
            coordinator = RotationCoordinator(
                IdentityPool(), self.gateway, self.handoff, self.store,
                RotationConfig(overlap_ms=0), service_backends=endpoints,
            )
            self.addAsyncCleanup(coordinator.close)
            await coordinator.recover()
            return coordinator

        coordinator = await start_controller()
        self.handoff.prepare_and_wait.return_value = False
        failed = await coordinator.rotate(RotationRequest("failed", "web"))
        self.assertEqual(failed.state, RotationState.FAILED)
        self.assertEqual(self.store.load().active["web"], self.old)
        self.assertEqual(self.gateway.stage_mapping.await_args.args[0].internal_endpoint, second)

        self.handoff.prepare_and_wait.return_value = True
        switched = await coordinator.rotate(RotationRequest("switch", "web"))
        self.assertEqual(switched.state, RotationState.COMPLETE)
        active = self.store.load().active["web"]
        self.assertEqual(active.internal_endpoint, second)
        self.gateway.activate_mapping.assert_awaited_with(active, self.old)

        await coordinator.close()
        self.gateway.active_generation_id.return_value = active.generation_id
        restarted = await start_controller()
        for endpoint in (third, fourth, first):
            previous = active
            result = await restarted.rotate(RotationRequest(f"switch-{endpoint.public_ip}", "web"))
            self.assertEqual(result.state, RotationState.COMPLETE)
            active = self.store.load().active["web"]
            self.assertEqual(active.internal_endpoint, endpoint)
            self.gateway.retire_generation.assert_awaited_with(previous)

    async def test_activation_waits_for_authenticated_client_and_rejects_old_ack(self):
        key = secrets.token_bytes(32)
        received, release = asyncio.Event(), asyncio.Event()
        replies = []

        async def client(update):
            read_update(update, key, "web", "client")
            received.set()
            await release.wait()
            if not replies:
                replies.append(acknowledge(update, key, ready=True))
            return replies[0]

        session = HandoffSession("web", "client", key, client)
        self.rotator.handoff = HandoffControl([session], self.rotator.config)
        await self.rotator.request_rotation(RotationRequest("first", "web"))
        await asyncio.wait_for(received.wait(), 1)
        self.gateway.activate_mapping.assert_not_awaited()
        release.set()
        self.assertEqual((await self.rotator.wait("first")).state, RotationState.COMPLETE)
        active = self.store.load().active["web"]
        failed = await self.rotator.rotate(RotationRequest("second", "web"))
        self.assertEqual(failed.error, "handoff_failed")
        self.assertEqual(self.store.load().active["web"], active)
        self.gateway.activate_mapping.assert_awaited_once()
        self.gateway.rollback_to.assert_awaited_once_with(active)


if __name__ == "__main__":
    unittest.main()
