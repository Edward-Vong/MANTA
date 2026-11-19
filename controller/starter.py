"""Initial Rotation Coordinator lifecycle integration."""

import asyncio

from gateway.models import MappingGeneration


class RotationCoordinator:
    """Coordinate service identity rotations."""

    def __init__(self, identity_pool, gateway, handoff, state_store, config):
        self.identity_pool = identity_pool
        self.gateway = gateway
        self.handoff = handoff
        self.state_store = state_store
        self.config = config
        self._active_generations = {}

    async def request_rotation(self, request):
        """Run one supplied rotation plan immediately in this prototype."""

        return await self.rotate(request)

    async def rotate(self, request):
        """Run stage, verify, activate, then retire for one supplied generation."""

        new_generation, previous_generation, previous_id = self._rotation_inputs(request)
        service_id = new_generation.service_id
        if previous_generation is not None:
            self._active_generations.setdefault(service_id, previous_generation)
        previous_id = previous_id if previous_id is not None else self._current_id(service_id)

        staged = await self._gateway_call(
            self.gateway.stage_mapping,
            new_generation,
        )
        if not staged.success:
            cleanup = await self._cleanup_staged(service_id, previous_id)
            return self._outcome(
                False,
                service_id,
                new_generation,
                stage=staged,
                verification=None,
                activation=staged,
                retirement=None,
                cleanup=cleanup,
                phase="stage",
            )

        verified = await self._gateway_call(
            self.gateway.verify_staged,
            service_id,
            new_generation.generation_id,
        )
        if not verified.success:
            cleanup = await self._cleanup_staged(service_id, previous_id)
            return self._outcome(
                False,
                service_id,
                new_generation,
                stage=staged,
                verification=verified,
                activation=verified,
                retirement=None,
                cleanup=cleanup,
                phase="verify",
            )

        activated = await self._gateway_call(
            self.gateway.activate_mapping,
            service_id,
            new_generation.generation_id,
            previous_id,
        )
        if not activated.success:
            cleanup = await self._cleanup_staged(service_id, previous_id)
            return self._outcome(
                False,
                service_id,
                new_generation,
                stage=staged,
                verification=verified,
                activation=activated,
                retirement=None,
                cleanup=cleanup,
                phase="activate",
            )

        self._active_generations[service_id] = new_generation
        retirement = None
        if previous_id is not None and previous_id != new_generation.generation_id:
            retirement = await self.retire_previous(
                {
                    "service_id": service_id,
                    "generation_id": previous_id,
                }
            )
        return self._outcome(
            True,
            service_id,
            new_generation,
            stage=staged,
            verification=verified,
            activation=activated,
            retirement=retirement,
            cleanup=None,
            phase="retire" if retirement is not None else "activate",
        )

    async def retire_previous(self, record):
        """Retire a previous generation after activation has committed."""

        service_id = self._request_value(record, "service_id")
        generation_id = self._request_value(record, "generation_id")
        return await self._gateway_call(
            self.gateway.retire_generation,
            service_id,
            generation_id,
        )

    async def rollback(self, record, error):
        """Rollback only an uncommitted rotation through the gateway boundary."""

        service_id = self._request_value(record, "service_id")
        generation_id = self._request_value(record, "generation_id")
        result = await self._gateway_call(
            self.gateway.rollback_to,
            service_id,
            generation_id,
        )
        return {"rollback": result, "error": error}

    async def recover(self):
        # TODO: Reconcile saved state with gateway state before new rotations.
        raise NotImplementedError

    async def _gateway_call(self, operation, *args):
        """Cross the synchronous GatewayRuleInterface boundary asynchronously."""

        return await asyncio.to_thread(operation, *args)

    async def _cleanup_staged(self, service_id, previous_id):
        if previous_id is None:
            return None
        return await self._gateway_call(
            self.gateway.rollback_to,
            service_id,
            previous_id,
        )

    def _rotation_inputs(self, request):
        if isinstance(request, MappingGeneration):
            return request, None, None

        new_generation = self._request_value(request, "new_generation")
        if new_generation is None:
            new_generation = self._request_value(request, "generation")
        if not isinstance(new_generation, MappingGeneration):
            raise ValueError("rotation request must contain a MappingGeneration")

        previous_generation = self._request_value(request, "previous_generation")
        previous_id = self._request_value(request, "previous_generation_id")
        if previous_id is None and isinstance(previous_generation, MappingGeneration):
            previous_id = previous_generation.generation_id
        return new_generation, previous_generation, previous_id

    def _current_id(self, service_id):
        generation = self._active_generations.get(service_id)
        return generation.generation_id if generation is not None else None

    @staticmethod
    def _request_value(request, name, default=None):
        if isinstance(request, dict):
            return request.get(name, default)
        return getattr(request, name, default)

    @staticmethod
    def _outcome(
        success,
        service_id,
        generation,
        stage,
        verification,
        activation,
        retirement,
        cleanup,
        phase,
    ):
        return {
            "success": success,
            "service_id": service_id,
            "generation_id": generation.generation_id,
            "stage": stage,
            "verification": verification,
            "activation": activation,
            "retirement": retirement,
            "cleanup": cleanup,
            "phase": phase,
        }


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
