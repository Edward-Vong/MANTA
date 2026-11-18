# Policy

Choose public IPs/ports and when to change them.

## Telemetry test data

Run from the project root with Python 3.10+:

```bash
python3 -m experiments.telemetry_demo
python3 -m experiments.telemetry_demo --scenario loss --samples 10
```

Each run saves readable JSON to `policy/telemetry_data/telemetry_<UTC timestamp>.json`.
The default produces 27 samples: healthy, latency, loss, handoff failure, combined
degradation, outage, idle, missing data, and recovery. Use `--interval 1` to pace
collection; the default generates immediately. Ctrl+C saves an incomplete run.

Read the file's `samples` list. Each sample has its own UTC `collected_at`, raw
packet/handoff counts, calculated percentages, and round-trip latency (`rtt_ms`).
The file timestamp marks when collection ended. Ten-second windows are simulated
time, separate from collection timestamps. `--seed` repeats measurements, not timestamps.
Idle means zero activity; missing data uses `null`. Rates with no observations are
also `null`. All data is synthetic; the collector should classify measurements,
not the `scenario` label. The polling API remains `TelemetrySimulator.fetch_network_parameters()`.

## Where to start

1. [Python IP address tools](https://docs.python.org/3/library/ipaddress.html): learn how to validate addresses and work with an address pool. Start by choosing one unused IP/port.
2. [Ryu switching tutorial](https://book.ryu-sdn.org/en/html/switching_hub.html): see how the controller installs the rules your mappings will need.
3. [Ryu traffic monitor](https://book.ryu-sdn.org/en/html/traffic_monitor.html): see a periodic task using `hub.spawn` and `hub.sleep`; use that pattern for a rotation timer, then explore measurements for adaptive timing.
