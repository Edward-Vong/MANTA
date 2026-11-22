import json

class TelemetryCollector:
    """Defines analysis algorithms for interpreting network statistics and creating summaries."""

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
    
    def __init__(self, policy_manager, network_interface):
        """Initializes this rotation coordinator with a policy manager connection and network interface."""
        
        self.policy_manager = policy_manager
        self.network = network_interface

    def run_telemetry_analysis(self):
        stats_json = self.fetch_network_stats()
        current_status = self.analyze_network(stats_json)
        return current_status


    def fetch_network_stats(self):
        # Receive stats from network here
        # Should be the same JSON format as the telemetry emulator
        # Currently just returns static emulation data
        return self.emulation_data
    
    def analyze_network(stats):
        issues_identified = []

        status = 1

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
            status = -1
        else:
            issues_identified.append("No network issues identified")

        return (status, issues_identified)