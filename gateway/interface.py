"""Gateway Rule Interface consumed by the Rotation Coordinator."""

from abc import ABC, abstractmethod

from .models import GatewayResult, MappingGeneration


class GatewayRuleInterface(ABC):
    """Control-plane contract independent of the P4Runtime implementation."""

    @abstractmethod
    def stage_mapping(self, generation: MappingGeneration) -> GatewayResult:
        """Install a generation without making it active for normal traffic."""

    @abstractmethod
    def verify_staged(self, service_id: str, generation_id: int) -> GatewayResult:
        """Confirm that a staged generation is ready for activation."""

    @abstractmethod
    def activate_mapping(
        self,
        service_id: str,
        new_generation_id: int,
        previous_generation_id: int | None,
    ) -> GatewayResult:
        """Activate the new generation and place the previous one in overlap."""

    @abstractmethod
    def retire_generation(self, service_id: str, generation_id: int) -> GatewayResult:
        """Remove a retired generation from forwarding behavior."""

    @abstractmethod
    def rollback_to(self, service_id: str, generation_id: int) -> GatewayResult:
        """Restore a known generation after an unsuccessful update."""
