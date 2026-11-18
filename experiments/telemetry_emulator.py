"""Generate timestamped test data: python3 -m experiments.telemetry_demo"""

import argparse
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path


SCENARIOS = (
    "healthy", "latency", "loss", "handoff_failure", "degraded",
    "outage", "idle", "no_data", "recovery",
)
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "policy" / "telemetry_data"


def utc_timestamp(moment):
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def percentage(count, total):
    return round(100 * count / total, 2) if count is not None and total else None


class TelemetrySimulator:
    """Finite, repeatable ten-second measurement windows; no network access."""

    def __init__(self, samples_per_scenario=3, seed=7, scenarios=SCENARIOS):
        if type(samples_per_scenario) is not int or samples_per_scenario < 1:
            raise ValueError("samples_per_scenario must be a positive integer")

        self.scenarios = tuple(scenarios)
        if not self.scenarios or any(name not in SCENARIOS for name in self.scenarios):
            raise ValueError("Unknown or empty scenario list")

        self.samples_per_scenario = samples_per_scenario
        self.random = random.Random(seed)
        self.index = 0

    def fetch_network_parameters(self):
        """Return the next snapshot, or raise StopIteration when the demo ends."""
        scenario_index = self.index // self.samples_per_scenario
        if scenario_index >= len(self.scenarios):
            raise StopIteration

        scenario = self.scenarios[scenario_index]
        sent = self.random.randint(800, 1200)
        loss_fraction = self.random.uniform(0.05, 0.10) if scenario in ("loss", "degraded") else 0
        received = sent - round(sent * loss_fraction)
        rtt = self.random.uniform(80, 120) if scenario in ("latency", "degraded") else self.random.uniform(2, 5)
        attempts = 1
        failures = int(scenario in ("handoff_failure", "degraded", "outage"))

        if scenario == "outage":
            received = 0
            rtt = None
        elif scenario == "idle":
            sent = received = attempts = failures = 0
            rtt = None
        elif scenario == "no_data":
            sent = received = attempts = failures = rtt = None

        lost = sent - received if sent is not None else None

        snapshot = {
            "source": "synthetic",
            "collected_at": utc_timestamp(datetime.now(timezone.utc)),
            "scenario": scenario,
            "sample_id": self.index,
            "service_id": "web",
            "window_start_seconds": self.index * 10,
            "window_seconds": 10,
            "rtt_ms": round(rtt, 2) if rtt is not None else None,
            "packets_sent": sent,
            "packets_received": received,
            "packets_lost": lost,
            "packet_loss_percent": percentage(lost, sent),
            "handoff_attempts": attempts,
            "handoff_failures": failures,
            "handoff_failure_percent": percentage(failures, attempts),
        }
        self.index += 1

        return snapshot


def save_samples(samples, seed, output_dir=None, complete=True):
    collected_at = datetime.now(timezone.utc)
    output_dir = OUTPUT_DIR if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"telemetry_{collected_at:%Y-%m-%d_%H-%M-%S_%fZ}.json"
    report = {
        "source": "synthetic",
        "collected_at": utc_timestamp(collected_at),
        "seed": seed,
        "complete": complete,
        "sample_count": len(samples),
        "samples": samples,
    }

    with path.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")

    return path


def main(argv=None):
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
    raise SystemExit(main())
