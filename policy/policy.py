class PolicyManager:
  """Defines rotation interval algorithms and telemetry feedback loop."""

  def __init__(self, rotation_coordinator, telemetry_collector, initial_interval):
    """Initializes this policy manager with a rotation coordinator, telemetry collector, and initial interval."""
    
    self.rotation_coordinator = rotation_coordinator
    self.telemetry_collector = telemetry_collector
    self.interval = initial_interval

  def request_interval_update(rotation_coordinator, requested_interval):
    """Should be called continuously, updates rotation interval over time."""
    
    result = rotation_coordinator.request_rotation(requested_interval)
    if result == "Interval changed":
      print("Interval update successful")
      # Continue feedback loop
    elif result == "Unable to change interval":
      print("Unable to update rotation interval at rotation coordinator")
      # Error reporting

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

    self.request_interval_update(self.rotation_coordinator, self.interval)

  def get_network_status(self):
    """Fetches network conditions/status message from the telemetry collector."""
    
    return self.telemetry_collector.get_status()
