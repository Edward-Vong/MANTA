"""Rotation requests, configuration, and lifecycle data."""

from dataclasses import dataclass, field
from enum import Enum
from time import time

from .identities import PublicIdentity


@dataclass(frozen=True)
class MappingGeneration:
    service_id: str
    generation_id: int
    identity: PublicIdentity
    internal_endpoint: PublicIdentity

    def __post_init__(self):
        if not self.service_id.strip() or self.generation_id < 1:
            raise ValueError("A service and positive generation ID are required")

        if self.identity.protocol != self.internal_endpoint.protocol:
            raise ValueError("Public and internal protocols must match")

    @property
    def lease_id(self):
        return f"{self.service_id}:{self.generation_id}"


@dataclass(frozen=True)
class RotationRequest:
    request_id: str
    service_id: str
    policy_id: str = "manual"
    reason: str = "manual"
    requested_at: float = field(default_factory=time)

    def __post_init__(self):
        if not all(value.strip() for value in
                   (self.request_id, self.service_id, self.policy_id, self.reason)):
            raise ValueError("Request fields cannot be empty")


class RotationState(str, Enum):
    ACCEPTED = "accepted"
    STAGING = "staging"
    NOTIFYING = "notifying"
    ACTIVATING = "activating"
    RETIRING = "retiring"
    ROLLBACK = "rollback"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETE = "complete"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class RotationRecord:
    request: RotationRequest
    current_generation: MappingGeneration
    generation_id: int
    next_generation: MappingGeneration | None = None
    state: RotationState = RotationState.ACCEPTED
    committed: bool = False
    cleanup_confirmed: bool = False
    overlap_until: float = 0
    error: str | None = None
    gateway_staged: bool = False

    @property
    def finished(self):
        return self.state in (RotationState.COMPLETE, RotationState.FAILED)


@dataclass(frozen=True)
class RotationResult:
    request_id: str
    accepted: bool
    state: RotationState
    error: str | None = None


@dataclass(frozen=True)
class RotationConfig:
    overlap_ms: int = 2000
    retire_retry_ms: int = 500
    cleanup_attempts: int = 3
    operation_timeout_ms: int = 5000
    require_client_ack: bool = True
    handoff_attempts: int = 3
    handoff_ack_timeout_ms: int = 1000

    def __post_init__(self):
        if self.overlap_ms < 0 or self.retire_retry_ms < 0:
            raise ValueError("Delays cannot be negative")

        if self.cleanup_attempts < 1 or self.operation_timeout_ms <= 0:
            raise ValueError("Attempts and operation timeout must be positive")
        if self.handoff_attempts < 1 or self.handoff_ack_timeout_ms <= 0:
            raise ValueError("Handoff attempts and acknowledgment timeout must be positive")
