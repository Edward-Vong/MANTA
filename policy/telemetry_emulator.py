"""Generate timestamped test data: python3 -m policy.telemetry_emulator"""

import argparse
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path


SCENARIOS = (
    "healthy", "latency", "latency_spike", "loss", "burst_loss",
    "handoff_failure", "degraded", "outage", "recovery", "idle", "no_data",
)
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "policy" / "telemetry_data"

# Lab assumptions, not measured MANTA performance or collector thresholds.
WINDOW_SECONDS = 10
BASE_RTT_MS = 3
BASE_LOSS_PROBABILITY = 0.001
HANDOFF_INTERVAL_SECONDS = 30
BURST_LOSS = {"enter_bad": 0.01, "exit_bad": 0.1, "loss_in_bad": 0.9}
REFERENCES = (
    "https://github.com/iproute2/iproute2/blob/main/man/man8/tc-netem.8",
    "https://www.rfc-editor.org/rfc/rfc3357.html",
    "https://www.rfc-editor.org/rfc/rfc7567.html",
)


def utc_timestamp(moment):
    """Format a date and time as a timestamp string, using Z for UTC."""
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def percentage(count, total):
    """Calculate a percentage to two decimal places, or return None for missing data or a zero total."""
    return round(100 * count / total, 2) if count is not None and total else None


class TelemetrySimulator:
    """Generate fake network measurements for different conditions without accessing a real network."""

    def __init__(self, samples_per_scenario=3, seed=7, scenarios=SCENARIOS):
        """Check the settings and initialize the scenarios, random generator, and sample counter."""
        if type(samples_per_scenario) is not int or samples_per_scenario < 1:
            raise ValueError("samples_per_scenario must be a positive integer")

        self.scenarios = tuple(scenarios)
        if not self.scenarios or any(name not in SCENARIOS for name in self.scenarios):
            raise ValueError("Unknown or empty scenario list")

        self.samples_per_scenario = samples_per_scenario
        self.random = random.Random(seed)
        self.index = 0

    def _packet_window(self, scenario, progress):
        """Simulate one packet window and return sent and lost counts, the longest loss streak, and reply times."""
        if scenario == "no_data":
            return None, None, None, []
        if scenario == "idle":
            return 0, 0, 0, []

        queue_ms = 0
        loss_probability = BASE_LOSS_PROBABILITY
        if scenario == "latency":
            queue_ms = 80
        elif scenario == "loss":
            loss_probability = 0.08
        elif scenario in ("degraded", "recovery"):
            severity = progress if scenario == "degraded" else 1 - progress
            queue_ms = 120 * severity
            loss_probability += (0.10 - BASE_LOSS_PROBABILITY) * severity
        elif scenario == "outage":
            loss_probability = 1

        # Smooth changes in offered traffic between neighboring windows.
        self.packet_load = 0.7 * self.packet_load + 0.3 * self.random.gauss(1000, 200)
        sent = max(1, round(self.packet_load))
        lost = loss_run = longest_run = 0
        rtts = []

        for _ in range(sent):
            if scenario == "burst_loss":
                # Gilbert-Elliott: bad periods persist across probes/windows.
                if self.bad_loss_state:
                    if self.random.random() < BURST_LOSS["exit_bad"]:
                        self.bad_loss_state = False
                elif self.random.random() < BURST_LOSS["enter_bad"]:
                    self.bad_loss_state = True
                loss_probability = (BURST_LOSS["loss_in_bad"] if self.bad_loss_state
                                    else BASE_LOSS_PROBABILITY)

            # Correlated Gaussian variation; occasional Pareto tails for spikes.
            self.delay_noise = (0.75 * self.delay_noise
                                + math.sqrt(1 - 0.75 ** 2) * self.random.gauss(0, 1))
            rtt = max(0.1, BASE_RTT_MS + queue_ms + (0.5 + queue_ms * 0.1) * self.delay_noise)
            if scenario == "latency_spike" and self.random.random() < 0.08:
                rtt += min(500, 50 * self.random.paretovariate(2))

            if self.random.random() < loss_probability:
                lost += 1
                loss_run += 1
                longest_run = max(longest_run, loss_run)
            else:
                loss_run = 0
                rtts.append(rtt)

        return sent, lost, longest_run, rtts

    def fetch_network_parameters(self):
        """Return the next sample of timing, packet loss, and handoff statistics, or raise StopIteration when finished."""
        scenario_index, offset = divmod(self.index, self.samples_per_scenario)
        if scenario_index >= len(self.scenarios):
            raise StopIteration

        scenario = self.scenarios[scenario_index]
        if offset == 0:
            self.bad_loss_state = False
            self.delay_noise = 0
            self.packet_load = 1000

        progress = offset / (self.samples_per_scenario - 1) if self.samples_per_scenario > 1 else 1
        sent, lost, longest_run, rtts = self._packet_window(scenario, progress)
        received = sent - lost if sent is not None else None

        # Schedule a rotation at each segment's start, then every 30 seconds.
        attempts = int(offset * WINDOW_SECONDS % HANDOFF_INTERVAL_SECONDS == 0)
        failures = int(scenario in ("handoff_failure", "degraded", "outage"))
        failures *= attempts

        if scenario == "idle":
            attempts = failures = 0
        elif scenario == "no_data":
            attempts = failures = None

        rtts.sort()

        snapshot = {
            "source": "synthetic",
            "collected_at": utc_timestamp(datetime.now(timezone.utc)),
            "scenario": scenario,
            "sample_id": self.index,
            "service_id": "web",
            "window_start_seconds": self.index * WINDOW_SECONDS,
            "window_seconds": WINDOW_SECONDS,
            "rtt_ms": round(sum(rtts) / len(rtts), 2) if rtts else None,
            "rtt_p95_ms": round(rtts[math.ceil(0.95 * len(rtts)) - 1], 2) if rtts else None,
            "rtt_max_ms": round(rtts[-1], 2) if rtts else None,
            "packets_sent": sent,
            "packets_received": received,
            "packets_lost": lost,
            "packet_loss_percent": percentage(lost, sent),
            "max_loss_burst_packets": longest_run,
            "handoff_attempts": attempts,
            "handoff_failures": failures,
            "handoff_failure_percent": percentage(failures, attempts),
        }
        self.index += 1

        return snapshot


def save_samples(samples, seed, output_dir=None, complete=True):
    """Save the samples and simulation settings in a timestamped JSON file and return its path."""
    collected_at = datetime.now(timezone.utc)
    output_dir = OUTPUT_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"telemetry_{collected_at:%Y-%m-%d_%H-%M-%S_%fZ}.json"
    report = {
        "source": "synthetic",
        "schema_version": 2,
        "collected_at": utc_timestamp(collected_at),
        "seed": seed,
        "complete": complete,
        "sample_count": len(samples),
        "model": {
            "calibrated_to_real_measurements": False,
            "measurement": "Synthetic round-trip probes; loss means a missing reply, not TCP retransmissions.",
            "rtt_summary": "Mean, nearest-rank p95, and maximum of successful replies only.",
            "loss_bursts": "Consecutive missing replies, counted within each window.",
            "scenario_timing": "Independent segments; rotations start at segment time zero.",
            "window_seconds": WINDOW_SECONDS,
            "baseline_rtt_ms": BASE_RTT_MS,
            "baseline_loss_probability": BASE_LOSS_PROBABILITY,
            "handoff_interval_seconds": HANDOFF_INTERVAL_SECONDS,
            "burst_loss_probabilities": BURST_LOSS,
            "references": REFERENCES,
        },
        "samples": samples,
    }

    with path.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")

    return path


def main(argv=None):
    """Read command-line options, generate samples, and save the results even if interrupted with Ctrl+C."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3, help="windows per scenario")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    parser.add_argument("--interval", type=float, default=0,
                        help="seconds between samples; default generates immediately")
    args = parser.parse_args(argv)

    if args.samples < 1 or not math.isfinite(args.interval) or args.interval < 0:
        parser.error("samples must be positive and interval must be finite and nonnegative")

    scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
    simulator = TelemetrySimulator(args.samples, args.seed, scenarios)
    total = args.samples * len(scenarios)
    samples = []
    complete = True

    try:
        for index in range(total):
            if index:
                time.sleep(args.interval)
            samples.append(simulator.fetch_network_parameters())
    except KeyboardInterrupt:
        complete = False

    path = save_samples(samples, args.seed, complete=complete)
    print(f"Saved {len(samples)} synthetic samples to {path}")
    if not complete:
        print("Collection interrupted; the file contains the samples collected so far.")

    return 0 if complete else 130


if __name__ == "__main__":
    # Run main when this file is executed as a program and use its return value as the exit code.
    raise SystemExit(main())
