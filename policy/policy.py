import uuid
import json
from dataclasses import dataclass
from typing import Protocol

from controller.rotator import RotationRequest

"""
This file contains both the Policy Manager and Telemetry Collector classes.

Telemetry functionality is confined in TelemetryCollector and TelemetryObservation.
"""

@dataclass(frozen=True)
class TelemetryObservation:
  """Telemetry result already classified for policy evaluation.

  The collector supplies the decision and explanation. PolicyManager does not
  interpret metrics or invent thresholds in this prototype.
  """

  rotation_requested: bool
  reason: str
  explanation: list

  def __post_init__(self):
    if not isinstance(self.reason, str) or not self.reason.strip():
      raise ValueError("telemetry observations require a non-empty reason")


class TelemetryCollector(Protocol):
  """
  Source boundary for a telemetry collector.
  Defines analysis algorithms for interpreting network statistics and creating summaries.
  """

  # For test purposes, replace with any sample data record from /telemetry_data
  emulation_data = json.loads({
    "source": "synthetic",
    "collected_at": "2026-11-18T21:34:25.393484Z",
    "scenario": "healthy",
    "sample_id": 0,
    "service_id": "web",
    "window_start_seconds": 0,
    "window_seconds": 10,
    "rtt_ms": 3.08,
    "rtt_p95_ms": 3.83,
    "rtt_max_ms": 4.65,
    "packets_sent": 985,
    "packets_received": 983,
    "packets_lost": 2,
    "packet_loss_percent": 0.2,
    "max_loss_burst_packets": 1,
    "handoff_attempts": 1,
    "handoff_failures": 0,
    "handoff_failure_percent": 0.0
  })
  
  def __init__(self, policy_manager, service):
      """Connect this telemetry collector to a policy manager and service."""
      
      self.policy_manager = policy_manager
      self.service = service

  def observe(self, service_id: str) -> TelemetryObservation:
      """Return one already-classified observation for a service."""

      # Collect and return stats from service here
      # Should be the same JSON format as the telemetry emulator

      observation = TelemetryObservation()

      # Currently just returns static emulation data
      # MUST REPLACE WITH SERVICE INTEGRATION
      service_network_stats = self.emulation_data

      # Pass in empty observation, gets populated with reason for change and explanation list
      return self.analyze_network(observation, service_network_stats)
  
  def analyze_network(observation, stats):
      issues_identified = []

      reason = "Network conditions good"

      latency_issue = False
      packet_issue = False
      handoff_issue = False

      for key, value in stats.items():
          if key == "rtt_ms" and value > 80:
              issues_identified.append("Average RTT over 90 ms")
              latency_issue = True
          elif key == "rtt_p95_ms" and value > 60:
              issues_identified.append("95th percentile RTT over 60 ms")
              latency_issue = True
          elif key == "rtt_max_ms" and value > 300:
              issues_identified.append("Max RTT over 300 ms")
              latency_issue = True
          elif key == "packet_loss_percent" and value > 5.0:
              issues_identified.append("Packet loss greater than 5%")
              packet_issue = True
          elif key == "max_loss_burst_packets" and value > 5:
              issues_identified.append("Max burst packet loss greater than 5")
              packet_issue = True
          elif key == "handoff_failure_percent" and value > 0.0:
              issues_identified.append("Handoff failure detected")
              handoff_issue = True

      if (latency_issue or packet_issue or handoff_issue):
          reason = "Poor network conditions, check explanation"
      else:
          issues_identified.append("No network issues identified")

      observation.reason = reason
      observation.explanation = issues_identified
      observation.rotation_requested = True

      return observation

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
      raise TypeError("Policy changes requires a TelemetryObservation")
    if not observation.rotation_requested:
      return {
        "triggered": False,
        "service_id": service_id,
        "reason": observation.reason,
        "explanation": observation.explanation,
      }
    return await self._trigger_rotation(service_id, observation.reason, "telemetry", observation.explanation)

  async def run_with_telemetry(self, service_id, collector: TelemetryCollector):
    """Fetch and evaluate one observation from a TelemetryCollector."""

    observation = collector.observe(service_id)
    return await self.evaluate_observation(service_id, observation)

  async def tick(self, service_id, elapsed, collector: TelemetryCollector | None = None):
    """Run one deterministic scheduler tick.

    With no collector, this is the interval scheduler and delegates to the
    existing ``run_once`` fallback. When a collector is supplied, its explicit
    observation is evaluated instead; no telemetry thresholds are applied by
    the scheduler.
    """

    if collector is not None:
      return await self.run_with_telemetry(service_id, collector)
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
    return await self._trigger_rotation(service_id, "configured_interval_elapsed", "interval", ["No telemetry involved"])

  async def _trigger_rotation(self, service_id, reason, policy_id, telemetry_explanation):
    request_id = f"policy-{uuid.uuid4().hex}"
    while request_id in self._issued_request_ids:
      request_id = f"policy-{uuid.uuid4().hex}"
    self._issued_request_ids.add(request_id)
    request = RotationRequest(
      request_id=request_id,
      service_id=service_id,
      policy_id=policy_id,
      reason=reason,
      telemetry_explanation=telemetry_explanation
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