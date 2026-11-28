"""Client handoff plans and acknowledgement handling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
import hmac
import json
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .rotator.identities import PublicIdentity


@dataclass(frozen=True)
class HandoffPlan:
    request_id: str
    service_id: str
    old_generation_id: int
    new_generation_id: int
    next_identity: PublicIdentity
    overlap_ms: int


@dataclass(frozen=True)
class HandoffSession:
    """One enrolled client's key and cancellable request/reply transport."""

    service_id: str
    client_id: str
    key: bytes = field(repr=False)
    exchange: Callable[[dict], Awaitable[dict]]

    def __post_init__(self):
        if not self.service_id or not self.client_id:
            raise ValueError("A session requires a service and client ID")
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise ValueError("Use a client-specific key of at least 32 bytes")


def _tag(key, payload):
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hmac.digest(key, data.encode("utf-8"), "sha256").hex()


def _signed(key, payload):
    return {"payload": payload, "tag": _tag(key, payload)}


def _authenticated(message, key):
    if not isinstance(message, dict) or not isinstance(message.get("payload"), dict):
        return False
    tag = message.get("tag")
    if not isinstance(tag, str) or len(tag) != 64 or not tag.isascii():
        return False
    try:
        return hmac.compare_digest(tag, _tag(key, message["payload"]))
    except (TypeError, ValueError):
        return False


def read_update(update, key, service_id, client_id):
    """Authenticate the update and its intended recipient before preparing a route."""
    if _authenticated(update, key):
        payload = update["payload"]
        if (payload.get("kind") == "prepare" and payload.get("client_id") == client_id
                and payload.get("service_id") == service_id):
            return payload
    raise ValueError("The handoff update did not authenticate for this client")


def acknowledge(update, key, *, ready):
    """Bind the client's decision to the signed contents of this specific update."""
    return _signed(key, {"kind": "ack", "update_tag": update["tag"], "ready": ready})


class HandoffControl:
    """Wait for every enrolled client of the service to prepare the new identity."""

    def __init__(self, sessions, config):
        self.sessions = {}
        for session in sessions:
            address = (session.service_id, session.client_id)
            if address in self.sessions:
                raise ValueError(f"Duplicate handoff session for {address}")
            self.sessions[address] = session
        self.config = config
        self._pending = {}

    async def prepare_and_wait(self, plan):
        clients = [s for s in self.sessions.values() if s.service_id == plan.service_id]
        if not clients:
            return False
        tasks = [asyncio.create_task(self._prepare(s, plan)) for s in clients]
        try:
            return all(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _prepare(self, session, plan):
        address = (plan.service_id, session.client_id, plan.request_id)
        if address in self._pending:
            return False
        payload = dict(asdict(plan), kind="prepare", client_id=session.client_id,
                       nonce=secrets.token_hex(16))
        update = _signed(session.key, payload)
        self._pending[address] = (plan, update["tag"])
        try:
            for _ in range(self.config.handoff_attempts):
                try:
                    reply = await asyncio.wait_for(session.exchange(update),
                                                   self.config.handoff_ack_timeout_ms / 1000)
                except (OSError, asyncio.TimeoutError):
                    continue
                return self.validate_ack(reply, plan, session.client_id)
            return False
        finally:
            self._pending.pop(address)

    def validate_ack(self, ack, plan, client_id):
        pending = self._pending.get((plan.service_id, client_id, plan.request_id))
        if pending is None or pending[0] != plan:
            return False
        session = self.sessions[(plan.service_id, client_id)]
        if not _authenticated(ack, session.key):
            return False
        payload = ack["payload"]
        return (payload.get("kind") == "ack" and payload.get("ready") is True
                and payload.get("update_tag") == pending[1])
