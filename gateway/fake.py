"""Deterministic gateway test double for Rotation Coordinator tests."""

from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Tuple

from .interface import GatewayRuleInterface
from .models import GatewayResult, GatewayStatus, MappingGeneration


class FakeGateway(GatewayRuleInterface):
    """In-memory lifecycle model; it performs no packet processing."""

    def __init__(self, fail_operations: Optional[Iterable[str]] = None):
        self.generations: Dict[Tuple[str, int], MappingGeneration] = {}
        self.active_generation_ids: Dict[str, int] = {}
        self.previous_generation_ids: Dict[str, int | None] = {}
        self.verified_generations: set[Tuple[str, int]] = set()
        self.completed_rollbacks: set[Tuple[str, int]] = set()
        self.fail_operations = set(fail_operations or ())

    def _failure(self, operation: str, generation_id: Optional[int] = None) -> Optional[GatewayResult]:
        if operation in self.fail_operations:
            return GatewayResult.failure(
                operation,
                "INJECTED_FAILURE",
                f"fake failure injected for {operation}",
                generation_id,
            )
        return None

    def stage_mapping(self, generation: MappingGeneration) -> GatewayResult:
        if (result := self._failure("stage_mapping", generation.generation_id)):
            return result
        key = (generation.service_id, generation.generation_id)
        existing = self.generations.get(key)
        if existing is not None:
            same_mapping = (
                existing.identity == generation.identity
                and existing.internal_endpoint == generation.internal_endpoint
                and existing.protocol == generation.protocol
            )
            if same_mapping:
                return GatewayResult.ok("stage_mapping", "generation already present", generation.generation_id)
            return GatewayResult.failure(
                "stage_mapping",
                "GENERATION_CONFLICT",
                "generation ID already exists for a different mapping",
                generation.generation_id,
            )
        generation.status = GatewayStatus.STAGED
        self.generations[key] = generation
        return GatewayResult.ok("stage_mapping", generation_id=generation.generation_id)

    def verify_staged(self, service_id: str, generation_id: int) -> GatewayResult:
        if (result := self._failure("verify_staged", generation_id)):
            return result
        key = (service_id, generation_id)
        generation = self.generations.get(key)
        if generation is None:
            return GatewayResult.failure("verify_staged", "NOT_FOUND", "generation not found", generation_id)
        if key in self.verified_generations:
            return GatewayResult.ok("verify_staged", "generation already verified", generation_id)
        if generation.status is not GatewayStatus.STAGED:
            return GatewayResult.failure(
                "verify_staged",
                "NOT_STAGED",
                f"generation is {generation.status.value}, not staged",
                generation_id,
            )
        self.verified_generations.add(key)
        return GatewayResult.ok("verify_staged", generation_id=generation_id)

    def activate_mapping(
        self,
        service_id: str,
        new_generation_id: int,
        previous_generation_id: int | None,
    ) -> GatewayResult:
        if (result := self._failure("activate_mapping", new_generation_id)):
            return result
        new_key = (service_id, new_generation_id)
        new_generation = self.generations.get(new_key)
        if new_generation is None:
            return GatewayResult.failure("activate_mapping", "NOT_FOUND", "new generation not found", new_generation_id)
        if self.active_generation_ids.get(service_id) == new_generation_id:
            if self.previous_generation_ids.get(service_id) != previous_generation_id:
                return GatewayResult.failure(
                    "activate_mapping",
                    "PREVIOUS_MISMATCH",
                    "repeated activation does not match its original predecessor",
                    new_generation_id,
                )
            return GatewayResult.ok("activate_mapping", "generation already active", new_generation_id)
        if new_generation.status is not GatewayStatus.STAGED:
            return GatewayResult.failure(
                "activate_mapping",
                "NOT_STAGED",
                "new generation must be staged before activation",
                new_generation_id,
            )
        if new_key not in self.verified_generations:
            return GatewayResult.failure(
                "activate_mapping",
                "NOT_VERIFIED",
                "generation must be successfully verified before activation",
                new_generation_id,
            )
        if previous_generation_id is not None:
            previous_key = (service_id, previous_generation_id)
            previous = self.generations.get(previous_key)
            if previous is None:
                return GatewayResult.failure(
                    "activate_mapping",
                    "PREVIOUS_NOT_FOUND",
                    "previous generation not found",
                    previous_generation_id,
                )
            if self.active_generation_ids.get(service_id) != previous_generation_id:
                return GatewayResult.failure(
                    "activate_mapping",
                    "PREVIOUS_NOT_ACTIVE",
                    "previous generation is not the service's active generation",
                    previous_generation_id,
                )
            previous.status = GatewayStatus.OVERLAP
        new_generation.status = GatewayStatus.ACTIVE
        new_generation.activated_at = datetime.now(timezone.utc)
        self.active_generation_ids[service_id] = new_generation_id
        self.previous_generation_ids[service_id] = previous_generation_id
        return GatewayResult.ok("activate_mapping", generation_id=new_generation_id)

    def retire_generation(self, service_id: str, generation_id: int) -> GatewayResult:
        if (result := self._failure("retire_generation", generation_id)):
            return result
        generation = self.generations.get((service_id, generation_id))
        if generation is None:
            return GatewayResult.failure("retire_generation", "NOT_FOUND", "generation not found", generation_id)
        if generation.status is GatewayStatus.RETIRED:
            return GatewayResult.ok("retire_generation", "generation already retired", generation_id)
        if generation.status is not GatewayStatus.OVERLAP:
            return GatewayResult.failure(
                "retire_generation",
                "NOT_OVERLAP",
                "only an overlapping generation may be retired",
                generation_id,
            )
        generation.status = GatewayStatus.RETIRED
        generation.retired_at = datetime.now(timezone.utc)
        return GatewayResult.ok("retire_generation", generation_id=generation_id)

    def rollback_to(self, service_id: str, generation_id: int) -> GatewayResult:
        if (result := self._failure("rollback_to", generation_id)):
            return result
        target_key = (service_id, generation_id)
        target = self.generations.get(target_key)
        if target is None:
            return GatewayResult.failure("rollback_to", "NOT_FOUND", "rollback generation not found", generation_id)
        if target_key in self.completed_rollbacks:
            return GatewayResult.ok("rollback_to", "rollback already completed", generation_id)
        if target.status is not GatewayStatus.ACTIVE:
            return GatewayResult.failure(
                "rollback_to",
                "COMMITTED_OR_INVALID_TARGET",
                "rollback is limited to the currently active pre-commit generation",
                generation_id,
            )
        staged_keys = [
            key for key, generation in self.generations.items()
            if key[0] == service_id and generation.status is GatewayStatus.STAGED
        ]
        if not staged_keys:
            return GatewayResult.failure(
                "rollback_to",
                "COMMITTED_OR_INVALID_TARGET",
                "no uncommitted staged generation is available to roll back",
                generation_id,
            )
        for key in staged_keys:
            del self.generations[key]
            self.verified_generations.discard(key)
        self.completed_rollbacks.add(target_key)
        return GatewayResult.ok("rollback_to", generation_id=generation_id)
