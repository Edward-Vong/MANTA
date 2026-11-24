"""Public API for the rotation controller."""

from .coordinator import OperationFailed, RotationCoordinator
from .handoff import HandoffControl, HandoffPlan
from .identities import IdentityPool, PublicIdentity
from .journal import ControllerState, JsonStateStore, PersistenceError
from .models import (
    MappingGeneration,
    RotationConfig,
    RotationRecord,
    RotationRequest,
    RotationResult,
    RotationState,
)

__all__ = [
    "ControllerState",
    "HandoffControl",
    "HandoffPlan",
    "IdentityPool",
    "JsonStateStore",
    "MappingGeneration",
    "OperationFailed",
    "PersistenceError",
    "PublicIdentity",
    "RotationConfig",
    "RotationCoordinator",
    "RotationRecord",
    "RotationRequest",
    "RotationResult",
    "RotationState",
]
