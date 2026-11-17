"""Placeholder BMv2/P4Runtime gateway adapter.

This module intentionally contains no P4Runtime client or table-write logic.
It preserves the shared interface so the eventual adapter can be integrated
without changing Rotation Coordinator dependencies.
"""

from .interface import GatewayRuleInterface
from .models import GatewayResult, MappingGeneration


class Bmv2P4RuntimeGateway(GatewayRuleInterface):
    """Unimplemented adapter boundary for a future BMv2/P4Runtime client."""

    def _not_implemented(self, operation: str, generation_id: int | None = None) -> GatewayResult:
        return GatewayResult.failure(
            operation,
            "NOT_IMPLEMENTED",
            "BMv2/P4Runtime adapter is a placeholder",
            generation_id,
        )

    def stage_mapping(self, generation: MappingGeneration) -> GatewayResult:
        return self._not_implemented("stage_mapping", generation.generation_id)

    def verify_staged(self, service_id: str, generation_id: int) -> GatewayResult:
        return self._not_implemented("verify_staged", generation_id)

    def activate_mapping(
        self,
        service_id: str,
        new_generation_id: int,
        previous_generation_id: int | None,
    ) -> GatewayResult:
        return self._not_implemented("activate_mapping", new_generation_id)

    def retire_generation(self, service_id: str, generation_id: int) -> GatewayResult:
        return self._not_implemented("retire_generation", generation_id)

    def rollback_to(self, service_id: str, generation_id: int) -> GatewayResult:
        return self._not_implemented("rollback_to", generation_id)
