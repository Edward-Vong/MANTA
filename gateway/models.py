"""Shared gateway models for the Rotation Coordinator boundary.

These models describe control-plane state only. They do not implement packet
parsing, translation, or a P4Runtime wire format.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class GatewayStatus(str, Enum):
    """Lifecycle states required by the gateway specification."""

    STAGED = "staged"
    ACTIVE = "active"
    OVERLAP = "overlap"
    RETIRED = "retired"


@dataclass(frozen=True)
class PublicIdentity:
    """Externally visible service identity allocated by the Identity Pool."""

    public_ip: str
    public_port: int
    protocol: str


@dataclass(frozen=True)
class InternalEndpoint:
    """Protected service endpoint behind the gateway."""

    internal_ip: str
    internal_port: int


@dataclass
class MappingGeneration:
    """One public-to-internal mapping generation."""

    service_id: str
    generation_id: int
    identity: PublicIdentity
    internal_endpoint: InternalEndpoint
    protocol: str
    status: GatewayStatus = GatewayStatus.STAGED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activated_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.identity.protocol != self.protocol:
            raise ValueError("mapping protocol must match the public identity protocol")


@dataclass(frozen=True)
class GatewayResult:
    """Explicit outcome returned by every gateway control operation."""

    success: bool
    operation: str
    code: str = "OK"
    message: str = ""
    generation_id: Optional[int] = None

    @classmethod
    def ok(
        cls,
        operation: str,
        message: str = "",
        generation_id: Optional[int] = None,
    ) -> "GatewayResult":
        return cls(True, operation, "OK", message, generation_id)

    @classmethod
    def failure(
        cls,
        operation: str,
        code: str,
        message: str,
        generation_id: Optional[int] = None,
    ) -> "GatewayResult":
        return cls(False, operation, code, message, generation_id)
