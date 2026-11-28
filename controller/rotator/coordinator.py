"""Rotation orchestration, rollback, and recovery."""

import asyncio
import logging
from collections import Counter
from time import time

from ..handoff import HandoffPlan
from .journal import PersistenceError
from .models import (
    MappingGeneration,
    RotationConfig,
    RotationRecord,
    RotationRequest,
    RotationResult,
    RotationState,
)


class OperationFailed(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class RotationCoordinator:
    """One event loop and journal owner; call recover() before accepting work."""

    def __init__(self, identity_pool, gateway,
                 handoff, state_store,
                 config: RotationConfig | None = None, *, clock=time,
                 sleep=asyncio.sleep, service_backends=None):
        self.identity_pool = identity_pool
        self.gateway = gateway
        self.handoff = handoff
        self.state_store = state_store
        self.service_backends = {
            service: tuple(endpoints) for service, endpoints in (service_backends or {}).items()
        }
        for service, endpoints in self.service_backends.items():
            if not endpoints or len(set(endpoints)) != len(endpoints):
                raise ValueError(f"Expected a nonempty list of distinct backends for {service}")

        self.config = config or RotationConfig()
        self.clock = clock
        self.sleep = sleep

        self.counters = Counter()
        self.log = logging.getLogger(__package__)

        self._state = state_store.load()
        self._tasks = {}
        self._ready = False
        self._recovering = False

    async def request_rotation(self, request: RotationRequest):
        self.counters["requests"] += 1

        if not self._ready:
            return self._reject(request, "recovery_required")

        existing = self._state.records.get(request.request_id)

        if existing:
            previous = existing.request

            if (previous.service_id, previous.policy_id, previous.reason) != (
                    request.service_id, request.policy_id, request.reason):
                return self._reject(request, "request_id_conflict")

            return self._result(existing)

        current = self._state.active.get(request.service_id)

        if current is None:
            return self._reject(request, "unknown_service")

        if any(r.request.service_id == request.service_id and not r.finished
               for r in self._state.records.values()):
            return self._reject(request, "service_busy")

        generation_id = max(current.generation_id,
                            self._state.generation_ids.get(request.service_id, 0)) + 1
        record = RotationRecord(request, current, generation_id)
        self._state.generation_ids[request.service_id] = generation_id
        self._state.records[request.request_id] = record

        self._phase(record, RotationState.ACCEPTED)
        self._tasks[request.request_id] = asyncio.create_task(self._run(record))

        return self._result(record)

    async def rotate(self, request: RotationRequest):
        result = await self.request_rotation(request)

        return await self.wait(request.request_id) if result.accepted else result

    async def wait(self, request_id):
        task = self._tasks.get(request_id)

        if task is not None:
            await asyncio.shield(task)

        return self._result(self._state.records[request_id])

    async def _run(self, record):
        try:
            self._allocate(record)
            new = record.next_generation
            await self._require(self.gateway.stage_mapping(new), "stage_failed")
            record.gateway_staged = True
            self._phase(record, RotationState.STAGING)
            await self._require(self.gateway.verify_staged(new), "verify_failed")
            self.log.info("Verified staged generation %s for %s", new.generation_id, new.service_id)

            self._phase(record, RotationState.NOTIFYING)
            plan = HandoffPlan(
                record.request.request_id, new.service_id,
                record.current_generation.generation_id, new.generation_id,
                new.identity, self.config.overlap_ms)
            ready = await self._call(self.handoff.prepare_and_wait(plan))

            if self.config.require_client_ack and ready is not True:
                raise OperationFailed("handoff_failed")

            self._phase(record, RotationState.ACTIVATING)
            await self._require(self.gateway.activate_mapping(
                new, record.current_generation), "activation_failed")
            self._commit(record)
            await self.retire_previous(record)
        except PersistenceError:
            record.state = RotationState.RECOVERY_REQUIRED
            record.error = "journal_write_failed"
        except asyncio.CancelledError:
            self._ready = False
            raise
        except Exception as error:
            try:
                await self.rollback(record, self._error_code(error))
            except PersistenceError:
                record.state = RotationState.RECOVERY_REQUIRED
                record.error = "journal_write_failed"

    def _allocate(self, record):
        current = record.current_generation
        endpoint = current.internal_endpoint
        backends = self.service_backends.get(current.service_id)
        if backends:
            index = backends.index(endpoint)
            endpoint = backends[(index + 1) % len(backends)]
            if endpoint.protocol != current.identity.protocol:
                raise ValueError("Backend protocol must match the public identity")
        identity = self.identity_pool.allocate(
            f"{current.service_id}:{record.generation_id}",
            current.service_id, current.identity.protocol)

        reserved = {g.identity for g in self._state.active.values()}

        for other in self._state.records.values():

            if other is not record and not other.finished:
                reserved.add(other.current_generation.identity)

                if other.next_generation:
                    reserved.add(other.next_generation.identity)

        if identity in reserved or identity.protocol != current.identity.protocol:
            raise ValueError("Invalid identity allocation")

        record.next_generation = MappingGeneration(
            current.service_id, record.generation_id, identity,
            endpoint)

        self.log.info("Selected backend %s:%s -> %s:%s for %s generation %s",
                      current.internal_endpoint.public_ip, current.internal_endpoint.public_port,
                      endpoint.public_ip, endpoint.public_port, current.service_id, record.generation_id)

        self._phase(record, RotationState.STAGING)

    def _commit(self, record):
        self._state.active[record.request.service_id] = record.next_generation
        record.committed = True
        record.gateway_staged = False
        record.overlap_until = self.clock() + self.config.overlap_ms / 1000
        record.error = None

        self._phase(record, RotationState.RETIRING)

    async def retire_previous(self, record):
        if not record.committed:
            raise ValueError("Cannot retire before commit")

        if record.finished:
            return

        await self.sleep(max(0, record.overlap_until - self.clock()))

        for attempt in range(self.config.cleanup_attempts):
            try:
                if not record.cleanup_confirmed:
                    await self._require(self.gateway.retire_generation(
                        record.current_generation), "retirement_failed")
                    record.cleanup_confirmed = True

                    self._phase(record, RotationState.RETIRING)

                old = record.current_generation
                self.identity_pool.release(old.identity, old.lease_id)
                record.error = None

                self._phase(record, RotationState.COMPLETE)

                return
            except PersistenceError:
                raise
            except Exception as error:
                record.error = self._error_code(error)

                self._phase(record, RotationState.RETIRING)

                if attempt + 1 < self.config.cleanup_attempts:
                    self.counters["retirement_retries"] += 1
                    await self.sleep(self.config.retire_retry_ms / 1000)

    async def rollback(self, record, error):
        if record.committed:
            record.error = error

            self._phase(record, RotationState.RETIRING)

            return

        record.error = error

        try:
            self._phase(record, RotationState.ROLLBACK)

            if record.next_generation is not None:
                if not record.cleanup_confirmed:
                    if record.gateway_staged:
                        await self._require(self.gateway.rollback_to(
                            record.current_generation), "rollback_failed")
                        # rollback_to is authoritative for staged cleanup on
                        # gateways that support it. Keep the legacy cleanup
                        # call for adapters that remove staged entries there.
                        await self._call(self.gateway.retire_generation(
                            record.next_generation))
                        record.gateway_staged = False
                    record.cleanup_confirmed = True

                    self._phase(record, RotationState.ROLLBACK)

                new = record.next_generation
                self.identity_pool.release(new.identity, new.lease_id)

            self._phase(record, RotationState.FAILED)
        except PersistenceError:
            raise
        except Exception as cleanup_error:
            record.error = self._error_code(cleanup_error)

            self._phase(record, RotationState.RECOVERY_REQUIRED)

    async def recover(self):
        if self._recovering or any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("Stop running rotations before recovery")

        self._ready = False
        self._recovering = True

        try:
            self._state = self.state_store.load()
            self._tasks.clear()
            pending = [r for r in self._state.records.values() if not r.finished]
            self.log.info("Recovering coordinator with %d pending rotations", len(pending))

            for generation in self._state.active.values():
                self.identity_pool.reserve(generation.identity, generation.lease_id)

            for record in pending:
                old, new = record.current_generation, record.next_generation

                if not (record.committed and record.cleanup_confirmed):
                    self.identity_pool.reserve(old.identity, old.lease_id)

                if new and not (not record.committed and record.cleanup_confirmed):
                    self.identity_pool.reserve(new.identity, new.lease_id)

            pending_services = {r.request.service_id for r in pending}

            for service_id, generation in self._state.active.items():
                if service_id not in pending_services:
                    await self._restore_if_needed(generation)

            for record in pending:
                await self._recover_record(record)

            self._ready = True
            self.log.info("Coordinator ready for rotation requests")

            return [self._result(record) for record in pending]
        finally:
            self._recovering = False

    async def _recover_record(self, record):
        try:
            if record.committed:
                await self._restore_if_needed(record.next_generation)
                await self.retire_previous(record)

                return

            if record.next_generation is None:
                self._allocate(record)

            if record.cleanup_confirmed:
                await self.rollback(record, "interrupted_cleanup")

                return

            active_id = await self._call(self.gateway.active_generation_id(
                record.request.service_id))

            if (record.state in (RotationState.ACTIVATING,
                                 RotationState.RECOVERY_REQUIRED)
                    and active_id == record.next_generation.generation_id):
                self._commit(record)
                await self.retire_previous(record)
            else:
                await self.rollback(record, "interrupted_before_commit")
        except PersistenceError:
            raise
        except Exception as error:
            record.error = self._error_code(error)

            self._phase(record, RotationState.RECOVERY_REQUIRED)

    async def _restore_if_needed(self, generation):
        active_id = await self._call(self.gateway.active_generation_id(
            generation.service_id))

        if active_id != generation.generation_id:
            await self._require(self.gateway.rollback_to(generation),
                                "restore_committed_failed")

    async def close(self):
        self._ready = False

        for task in self._tasks.values():
            if not task.done():
                task.cancel()

        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _call(self, operation):
        return await asyncio.wait_for(
            operation, timeout=self.config.operation_timeout_ms / 1000)

    async def _require(self, operation, error):
        if await self._call(operation) is not True:
            raise OperationFailed(error)

    @staticmethod
    def _error_code(error):
        return error.code if isinstance(error, OperationFailed) else type(error).__name__

    def _phase(self, record, state):
        record.state = state

        try:
            self.state_store.save(self._state)
        except Exception as error:
            self._ready = False
            self.log.exception("Could not persist %s for request %s", state.value,
                               record.request.request_id)
            raise PersistenceError("journal_write_failed") from error

        self.counters[state.value] += 1
        elapsed_ms = max(0, self.clock() - record.request.requested_at) * 1000
        old = record.current_generation
        new = record.next_generation
        target = (f"{new.identity.public_ip}:{new.identity.public_port}"
                  if new else "pending allocation")
        self.log.log(logging.WARNING if record.error else logging.INFO,
                     "rotation_%s service=%s request=%s generation=%s->%s "
                     "identity=%s:%s->%s staged=%s committed=%s cleanup_confirmed=%s "
                     "elapsed_ms=%.0f error=%s",
                     state.value, record.request.service_id, record.request.request_id,
                     old.generation_id, record.generation_id, old.identity.public_ip,
                     old.identity.public_port, target, record.gateway_staged, record.committed,
                     record.cleanup_confirmed, elapsed_ms, record.error or "-", extra={
            "request_id": record.request.request_id,
            "service_id": record.request.service_id,
            "generation_id": record.generation_id,
            "error_code": record.error,
            "elapsed_ms": elapsed_ms,
        })

    @staticmethod
    def _result(record):
        return RotationResult(record.request.request_id, True,
                              record.state, record.error)

    def _reject(self, request, error):
        self.log.warning("Rotation rejected service=%s request=%s error=%s",
                         request.service_id, request.request_id, error)
        return RotationResult(request.request_id, False,
                              RotationState.REJECTED, error)
