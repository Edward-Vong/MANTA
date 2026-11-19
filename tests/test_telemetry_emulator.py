import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from policy import telemetry_emulator as demo
from policy.telemetry_emulator import SCENARIOS, TelemetrySimulator


class TelemetryEmulatorTests(unittest.TestCase):
    def test_scenarios_and_measurements(self):
        simulator = TelemetrySimulator(samples_per_scenario=1)
        samples = [simulator.fetch_network_parameters() for _ in SCENARIOS]

        self.assertEqual([sample["scenario"] for sample in samples], list(SCENARIOS))
        for index, sample in enumerate(samples):
            self.assertEqual(sample["source"], "synthetic")
            self.assertEqual(sample["window_start_seconds"], index * 10)
            collected_at = datetime.fromisoformat(sample["collected_at"].replace("Z", "+00:00"))
            self.assertEqual(collected_at.tzinfo, timezone.utc)

            if sample["packets_sent"] is not None:
                self.assertGreaterEqual(sample["packets_received"], 0)
                self.assertLessEqual(sample["packets_received"], sample["packets_sent"])
                self.assertEqual(sample["packets_lost"], sample["packets_sent"] - sample["packets_received"])
                self.assertLessEqual(sample["max_loss_burst_packets"], sample["packets_lost"])
                self.assertLessEqual(sample["handoff_failures"], sample["handoff_attempts"])
            if sample["packets_sent"]:
                self.assertEqual(sample["packet_loss_percent"], round(
                    100 * sample["packets_lost"] / sample["packets_sent"], 2))

            if sample["packets_received"]:
                self.assertGreater(sample["rtt_ms"], 0)
                self.assertLessEqual(sample["rtt_ms"], sample["rtt_max_ms"])
                self.assertLessEqual(sample["rtt_p95_ms"], sample["rtt_max_ms"])
            else:
                for field in ("rtt_ms", "rtt_p95_ms", "rtt_max_ms"):
                    self.assertIsNone(sample[field])

        by_scenario = {sample["scenario"]: sample for sample in samples}
        healthy = by_scenario["healthy"]
        latency = by_scenario["latency"]
        loss = by_scenario["loss"]
        handoff = by_scenario["handoff_failure"]
        degraded = by_scenario["degraded"]
        outage = by_scenario["outage"]
        idle = by_scenario["idle"]
        no_data = by_scenario["no_data"]
        recovery = by_scenario["recovery"]

        self.assertLessEqual(healthy["rtt_ms"], 5)
        self.assertGreaterEqual(latency["rtt_ms"], 80)
        self.assertGreater(loss["packets_sent"] - loss["packets_received"], 0)
        self.assertEqual(handoff["handoff_failures"], 1)
        self.assertEqual(handoff["handoff_failure_percent"], 100)
        self.assertGreaterEqual(degraded["rtt_ms"], 80)
        self.assertGreater(degraded["packet_loss_percent"], 0)
        self.assertEqual(degraded["handoff_failures"], 1)
        self.assertEqual(outage["packet_loss_percent"], 100)
        self.assertEqual(outage["max_loss_burst_packets"], outage["packets_sent"])
        self.assertIsNone(outage["rtt_ms"])
        self.assertEqual(idle["packets_sent"], 0)
        self.assertEqual(idle["handoff_attempts"], 0)
        self.assertIsNone(idle["packet_loss_percent"])
        self.assertIsNone(idle["handoff_failure_percent"])
        self.assertIsNone(no_data["rtt_ms"])
        self.assertIsNone(no_data["packets_sent"])
        self.assertIsNone(no_data["handoff_attempts"])
        self.assertIsNone(no_data["packet_loss_percent"])
        self.assertIsNone(no_data["max_loss_burst_packets"])
        self.assertLessEqual(recovery["rtt_ms"], 5)
        self.assertEqual(recovery["handoff_failures"], 0)

        with self.assertRaises(StopIteration):
            simulator.fetch_network_parameters()

    def test_burst_loss_differs_from_random_loss(self):
        # Similar overall loss rates should still reveal different loss patterns.
        results = {}
        for scenario in ("loss", "burst_loss"):
            simulator = TelemetrySimulator(30, seed=42, scenarios=(scenario,))
            samples = [simulator.fetch_network_parameters() for _ in range(30)]
            rate = sum(s["packets_lost"] for s in samples) / sum(s["packets_sent"] for s in samples)
            mean_burst = sum(s["max_loss_burst_packets"] for s in samples) / len(samples)
            results[scenario] = rate, mean_burst

        random_rate, random_burst = results["loss"]
        burst_rate, burst_length = results["burst_loss"]
        self.assertAlmostEqual(random_rate, burst_rate, delta=0.02)
        self.assertGreater(burst_length, random_burst * 3)

    def test_latency_tails_and_gradual_recovery(self):
        groups = {}
        for scenario in ("healthy", "latency_spike", "degraded", "recovery"):
            simulator = TelemetrySimulator(8, seed=42, scenarios=(scenario,))
            groups[scenario] = [simulator.fetch_network_parameters() for _ in range(8)]

        for sample in groups["latency_spike"]:
            self.assertGreater(sample["rtt_p95_ms"], sample["rtt_ms"] * 2)
        for sample in groups["healthy"]:
            self.assertLess(sample["rtt_p95_ms"], sample["rtt_ms"] * 2)

        for field in ("rtt_ms", "packet_loss_percent"):
            self.assertGreater(groups["degraded"][-1][field], groups["degraded"][0][field])
            self.assertLess(groups["recovery"][-1][field], groups["recovery"][0][field])
        self.assertLess(groups["recovery"][-1]["rtt_ms"], 5)
        self.assertGreater(groups["recovery"][3]["rtt_ms"], 30)

    def test_handoffs_only_occur_on_scheduled_windows(self):
        simulator = TelemetrySimulator(7, scenarios=("handoff_failure",))
        samples = [simulator.fetch_network_parameters() for _ in range(7)]
        self.assertEqual([s["handoff_attempts"] for s in samples], [1, 0, 0, 1, 0, 0, 1])
        self.assertEqual([s["handoff_failures"] for s in samples], [1, 0, 0, 1, 0, 0, 1])
        self.assertEqual([s["handoff_failure_percent"] for s in samples],
                         [100, None, None, 100, None, None, 100])
        # A control-plane failure can leave the old data path healthy.
        self.assertTrue(all(s["rtt_ms"] < 5 for s in samples))

    def test_same_seed_replays_identical_measurements(self):
        first = TelemetrySimulator(seed=42)
        second = TelemetrySimulator(seed=42)

        for _ in range(3 * len(SCENARIOS)):
            left = first.fetch_network_parameters()
            right = second.fetch_network_parameters()
            left.pop("collected_at")
            right.pop("collected_at")
            self.assertEqual(left, right)

    def test_single_scenario_and_invalid_settings(self):
        simulator = TelemetrySimulator(2, scenarios=("loss",))
        self.assertEqual(simulator.fetch_network_parameters()["scenario"], "loss")
        self.assertEqual(simulator.fetch_network_parameters()["scenario"], "loss")

        with self.assertRaises(StopIteration):
            simulator.fetch_network_parameters()
        with self.assertRaises(ValueError):
            TelemetrySimulator(0)
        with self.assertRaises(ValueError):
            TelemetrySimulator(1.5)
        with self.assertRaises(ValueError):
            TelemetrySimulator(scenarios=("unknown",))

    def test_timestamped_json_and_no_overwrites(self):
        sample = TelemetrySimulator().fetch_network_parameters()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            folder = Path(temporary) / "telemetry_data"
            first = demo.save_samples([sample], seed=7, output_dir=folder)
            report = json.loads(first.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(report["collected_at"].replace("Z", "+00:00"))

            self.assertEqual(first.name, f"telemetry_{stamp:%Y-%m-%d_%H-%M-%S_%fZ}.json")
            self.assertEqual(report["source"], "synthetic")
            self.assertEqual(report["schema_version"], 2)
            self.assertFalse(report["model"]["calibrated_to_real_measurements"])
            self.assertEqual(report["model"]["references"], list(demo.REFERENCES))
            self.assertTrue(report["complete"])
            self.assertEqual(report["sample_count"], 1)
            self.assertEqual(report["samples"], [sample])
            original = first.read_bytes()

            with patch.object(demo, "datetime") as clock:
                clock.now.return_value = stamp
                with self.assertRaises(FileExistsError):
                    demo.save_samples([], seed=7, output_dir=folder)
            self.assertEqual(first.read_bytes(), original)

    def test_default_command_saves_all_scenarios(self):
        expected_dir = Path(demo.__file__).resolve().parents[1] / "policy" / "telemetry_data"
        self.assertEqual(demo.OUTPUT_DIR, expected_dir)

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            with patch.object(demo, "OUTPUT_DIR", Path(temporary)), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(demo.main([]), 0)

            files = list(Path(temporary).glob("*.json"))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text())
            self.assertEqual(report["sample_count"], 3 * len(SCENARIOS))
            self.assertEqual({s["scenario"] for s in report["samples"]}, set(SCENARIOS))
            self.assertIn(str(files[0]), output.getvalue())

    def test_interrupted_collection_saves_partial_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            with patch.object(demo, "OUTPUT_DIR", Path(temporary)), \
                    patch.object(demo.time, "sleep", side_effect=KeyboardInterrupt), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(demo.main(["--interval", "1"]), 130)

            report = json.loads(next(Path(temporary).glob("*.json")).read_text())
            self.assertFalse(report["complete"])
            self.assertEqual(report["sample_count"], 1)

    def test_cli_rejects_invalid_settings(self):
        for arguments in (["--samples", "0"], ["--interval", "-1"], ["--interval", "nan"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    demo.main(arguments)
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
