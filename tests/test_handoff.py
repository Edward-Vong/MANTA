import asyncio
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from controller.handoff import HandoffControl, HandoffPlan, HandoffSession, acknowledge, read_update
from controller.rotator import PublicIdentity, RotationConfig
from demos.rotation_scenario import client_handoff


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = secrets.token_bytes(32)
        self.plan = HandoffPlan("req", "web", 1, 2, PublicIdentity("10.0.0.101", 8080), 2000)
        self.config = RotationConfig(handoff_attempts=2, handoff_ack_timeout_ms=100)

    def controller(self, exchange):
        session = HandoffSession("web", "alice", self.key, exchange)
        return HandoffControl([session], self.config)

    async def test_only_matching_service_clients_must_confirm(self):
        clients = []
        for service, name, ready in [("web", "alice", True), ("web", "bob", False),
                                     ("other", "eve", True)]:
            key = secrets.token_bytes(32)

            async def exchange(update, service=service, name=name, key=key, ready=ready):
                payload = read_update(update, key, service, name)
                self.assertEqual(payload["next_identity"]["public_ip"], "10.0.0.101")
                return acknowledge(update, key, ready=ready)

            clients.append(HandoffSession(service, name, key, AsyncMock(side_effect=exchange)))
        control = HandoffControl(clients, self.config)
        self.assertFalse(await control.prepare_and_wait(self.plan))
        for client in clients[:2]:
            client.exchange.assert_awaited_once()
        clients[2].exchange.assert_not_awaited()
        self.assertFalse(await control.prepare_and_wait(replace(self.plan, service_id="absent")))

    async def test_retry_after_lost_ack_then_reject_replay(self):
        updates = []
        replies = []

        async def exchange(update):
            updates.append(update)
            if len(updates) == 1:
                replies.append(acknowledge(update, self.key, ready=True))
                raise ConnectionError("reply lost")
            return replies[0]

        control = self.controller(exchange)
        self.assertTrue(await control.prepare_and_wait(self.plan))
        self.assertEqual(updates[0], updates[1])
        self.assertFalse(control.validate_ack(replies[0], self.plan, "alice"))
        self.assertFalse(await control.prepare_and_wait(self.plan))
        self.assertNotEqual(updates[1]["tag"], updates[2]["tag"])

    async def test_reject_wrong_key_changed_update_and_invalid_acks(self):
        async def exchange(update):
            changed = copy.deepcopy(update)
            changed["payload"]["next_identity"]["public_ip"] = "10.0.0.109"
            for message, client in [(changed, "alice"), (update, "bob")]:
                with self.assertRaises(ValueError):
                    read_update(message, self.key, "web", client)
            good = acknowledge(update, self.key, ready=True)
            for ack in [None, {}, {"payload": {}, "tag": "é" * 64},
                        acknowledge(update, b"x" * 32, ready=True),
                        acknowledge(update, self.key, ready=1),
                        acknowledge(update, self.key, ready=False)]:
                self.assertFalse(control.validate_ack(ack, self.plan, "alice"))
            self.assertFalse(control.validate_ack(good, replace(self.plan, new_generation_id=3), "alice"))
            self.assertFalse(control.validate_ack(good, self.plan, "bob"))
            return good

        control = self.controller(exchange)
        self.assertTrue(await control.prepare_and_wait(self.plan))

    async def test_timeout_exhaustion_and_cancellation_clear_pending_ack(self):
        entered = asyncio.Event()
        updates = []

        async def silent(update):
            updates.append(update)
            entered.set()
            await asyncio.Event().wait()

        self.config = replace(self.config, handoff_ack_timeout_ms=10)
        control = self.controller(silent)
        self.assertFalse(await control.prepare_and_wait(self.plan))
        self.assertEqual(len(updates), 2)
        entered.clear()
        work = asyncio.create_task(control.prepare_and_wait(self.plan))
        await entered.wait()
        work.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await work
        self.assertFalse(control.validate_ack(acknowledge(updates[-1], self.key, ready=True),
                                              self.plan, "alice"))

    async def test_demo_client_ack_follows_local_route_result(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory, "key")
            key_path.write_bytes(self.key)

            def receive(update):
                output = io.StringIO()
                with patch("sys.stdin", io.StringIO(json.dumps(update))), patch("sys.stdout", output):
                    client_handoff("web", "client-eth0", "10.10.1.1", key_path)
                return json.loads(output.getvalue())

            async def exchange(update):
                return receive(update)

            session = HandoffSession("web", "client", self.key, exchange)
            control = HandoffControl([session], self.config)
            with patch("demos.rotation_scenario.subprocess.run") as route:
                route.return_value.returncode = 0
                self.assertTrue(await control.prepare_and_wait(self.plan))
                route.assert_called_once_with(
                    ["ip", "route", "replace", "10.0.0.101/32", "via", "10.10.1.1",
                     "dev", "client-eth0"], timeout=1, check=False)
                route.return_value.returncode = 1
                self.assertFalse(await control.prepare_and_wait(self.plan))
                route.reset_mock()
                with self.assertRaises(ValueError):
                    receive({"payload": {"next_identity": {}}, "tag": "0" * 64})
                route.assert_not_called()
