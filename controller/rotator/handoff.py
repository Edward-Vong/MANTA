"""Client handoff plans and acknowledgement handling."""

from dataclasses import dataclass

from .identities import PublicIdentity


@dataclass(frozen=True)
class HandoffPlan:
    request_id: str
    service_id: str
    old_generation_id: int
    new_generation_id: int
    next_identity: PublicIdentity
    overlap_ms: int


class HandoffControl:
    """Handle client updates and acknowledgements."""

    def __init__(self, sessions, config):
        self.sessions = sessions
        self.config = config

    async def prepare_and_wait(self, plan):
        # TODO: Send an authenticated update and await an ACK with bounded retries.
        raise NotImplementedError

    def validate_ack(self, ack, plan, client_id):
        # TODO: Verify authentication, ready status, and matching pending IDs.
        raise NotImplementedError
