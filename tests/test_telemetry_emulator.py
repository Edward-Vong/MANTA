import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from experiments import telemetry_emulator as demo
from experiments.telemetry_emulator import SCENARIOS, TelemetrySimulator


class TelemetryDemoTests(unittest.TestCase):
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
                self.assertLessEqual(sample["packets_received"], sample["packets_sent"])
                self.assertEqual(sample["packets_lost"], sample["packets_sent"] - sample["packets_received"])
                self.assertLessEqual(sample["handoff_failures"], sample["handoff_attempts"])
            if sample["packets_sent"]:
                self.assertEqual(sample["packet_loss_percent"], round(
                    100 * sample["packets_lost"] / sample["packets_sent"], 2))

        healthy, latency, loss, handoff, degraded, outage, idle, no_data, recovery = samples
        self.assertLessEqual(healthy["rtt_ms"], 5)
        self.assertGreaterEqual(latency["rtt_ms"], 80)
        self.assertGreater(loss["packets_sent"] - loss["packets_received"], 0)
        self.assertEqual(handoff["handoff_failures"], 1)
        self.assertEqual(handoff["handoff_failure_percent"], 100)
        self.assertGreaterEqual(degraded["rtt_ms"], 80)
        self.assertGreater(degraded["packet_loss_percent"], 0)
        self.assertEqual(degraded["handoff_failures"], 1)
        self.assertEqual(outage["packet_loss_percent"], 100)
        self.assertIsNone(outage["rtt_ms"])
        self.assertEqual(idle["packets_sent"], 0)
        self.assertEqual(idle["handoff_attempts"], 0)
        self.assertIsNone(idle["packet_loss_percent"])
        self.assertIsNone(idle["handoff_failure_percent"])
        self.assertIsNone(no_data["rtt_ms"])
        self.assertIsNone(no_data["packets_sent"])
        self.assertIsNone(no_data["handoff_attempts"])
        self.assertIsNone(no_data["packet_loss_percent"])
        self.assertLessEqual(recovery["rtt_ms"], 5)
        self.assertEqual(recovery["handoff_failures"], 0)

        with self.assertRaises(StopIteration):
            simulator.fetch_network_parameters()

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
