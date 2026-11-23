import uuid
from dataclasses import dataclass
from typing import Protocol

from controller.rotator import RotationRequest


@dataclass(frozen=True)
class TelemetryObservation:
  """Telemetry result already classified for policy evaluation.

  The provider supplies the decision and explanation. PolicyManager does not
  interpret metrics or invent thresholds in this prototype.
  """

  rotation_requested: bool
  reason: str

  def __post_init__(self):
    if not isinstance(self.reason, str) or not self.reason.strip():
      raise ValueError("telemetry observations require a non-empty reason")


class TelemetryProvider(Protocol):
  """Source boundary for a future telemetry collector."""

  def observe(self, service_id: str) -> TelemetryObservation:
    """Return one already-classified observation for a service."""


class PolicyManager:
  """Defines rotation interval algorithms and telemetry feedback loop."""

  def __init__(self, rotation_coordinator, telemetry_collector, initial_interval):
    """Initializes this policy manager with a rotation coordinator, telemetry collector, and initial interval."""
    
    self.rotation_coordinator = rotation_coordinator
    self.telemetry_collector = telemetry_collector
    self.interval = initial_interval
    self._elapsed = {}
    self._in_flight = set()
    self._issued_request_ids = set()

  def request_interval_update(self, requested_interval):
    """Update policy timing without sending an interval to the coordinator."""

    self.interval = requested_interval
    return "Interval changed"

  async def request_rotation(self, decision: RotationRequest | None):
    """Hand an explicit rotation request to the coordinator.

    ``None`` represents a policy decision not to rotate and therefore does
    not call the coordinator or gateway.  The coordinator remains responsible
    for allocating the next MappingGeneration and running its lifecycle.
    """

    if decision is None:
      return None

    accepted = await self.rotation_coordinator.request_rotation(decision)
    return await self.rotation_coordinator.wait(decision.request_id) if accepted.accepted else accepted

  async def evaluate_observation(self, service_id, observation):
    """Turn one telemetry observation into a rotation decision."""

    if not isinstance(observation, TelemetryObservation):
      raise TypeError("policy evaluation requires a TelemetryObservation")
    if not observation.rotation_requested:
      return {
        "triggered": False,
        "service_id": service_id,
        "reason": observation.reason,
      }
    return await self._trigger_rotation(service_id, observation.reason, "telemetry")

  async def run_with_telemetry(self, service_id, provider: TelemetryProvider):
    """Fetch and evaluate one observation from an injected provider."""

    observation = provider.observe(service_id)
    return await self.evaluate_observation(service_id, observation)

  async def tick(self, service_id, elapsed, provider: TelemetryProvider | None = None):
    """Run one deterministic scheduler tick.

    With no provider, this is the interval scheduler and delegates to the
    existing ``run_once`` fallback. When a provider is supplied, its explicit
    observation is evaluated instead; no telemetry thresholds are applied by
    the scheduler.
    """

    if provider is not None:
      return await self.run_with_telemetry(service_id, provider)
    return await self.run_once(service_id, elapsed)

  def reset_schedule(self, service_id=None):
    """Reset accumulated interval state after all work for a service ends."""

    if service_id is None:
      if self._in_flight:
        raise RuntimeError("cannot reset schedules while rotations are in flight")
      self._elapsed.clear()
      return

    if service_id in self._in_flight:
      raise RuntimeError(f"cannot reset an in-flight schedule: {service_id}")
    self._elapsed.pop(service_id, None)

  async def run_once(self, service_id, elapsed):
    """Run one testable policy tick for a service.

    ``elapsed`` is measured in the same units as the configured interval. No
    scheduler or background task is created; the caller invokes this method
    periodically. The coordinator remains responsible for creating the next
    MappingGeneration and executing its lifecycle.
    """

    if not isinstance(service_id, str) or not service_id.strip():
      raise ValueError("service_id must be a non-empty string")
    if elapsed < 0:
      raise ValueError("elapsed must not be negative")

    if service_id in self._in_flight:
      return {
        "triggered": False,
        "reason": "rotation_in_flight",
        "service_id": service_id,
      }

    accumulated = self._elapsed.get(service_id, 0) + elapsed
    if accumulated < self.interval:
      self._elapsed[service_id] = accumulated
      return {
        "triggered": False,
        "reason": "interval_not_elapsed",
        "service_id": service_id,
        "elapsed": accumulated,
        "interval": self.interval,
      }

    self._elapsed[service_id] = 0
    return await self._trigger_rotation(service_id, "configured_interval_elapsed", "interval")

  async def _trigger_rotation(self, service_id, reason, policy_id):
    request_id = f"policy-{uuid.uuid4().hex}"
    while request_id in self._issued_request_ids:
      request_id = f"policy-{uuid.uuid4().hex}"
    self._issued_request_ids.add(request_id)
    request = RotationRequest(
      request_id=request_id,
      service_id=service_id,
      policy_id=policy_id,
      reason=reason,
    )

    self._in_flight.add(service_id)
    try:
      result = await self.request_rotation(request)
      return {
        "triggered": True,
        "service_id": service_id,
        "request_id": request_id,
        "result": result,
      }
    finally:
      self._in_flight.remove(service_id)

  def rotation_update(self):
    """Fetches network conditions from telemetry, updates interval whether GOOD/BAD/NEUTRAL."""
    
    current_network_status = self.get_network_status()

    if current_network_status == 1:
      self.interval = self.interval - 1
      print("Good network conditions, reducing interval")
    elif current_network_status == -1:
      self.interval = self.interval + 1
      print("Poor network conditions, increasing interval")
    elif current_network_status == 0:
      print("Network conditions unclear, try next time")

    return self.request_interval_update(self.interval)

  def get_network_status(self):
    """Fetches network conditions/status message from the telemetry collector."""
    
    return self.telemetry_collector.get_status()