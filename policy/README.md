# Policy

Choose public IPs/ports and when to change them.

## Telemetry test data

Run from the project root with Python 3.10+:

```bash
python3 -m policy.telemetry_emulator
python3 -m policy.telemetry_emulator --scenario burst_loss --samples 10
```

Each run saves readable JSON to `policy/telemetry_data/telemetry_<UTC timestamp>.json`.
The default produces 33 samples across 11 cases: healthy, latency, latency spikes,
random loss, burst loss, handoff failure, worsening degradation, outage, gradual
recovery, idle, and missing data. Use `--interval 1` to pace
collection; the default generates immediately. Ctrl+C saves an incomplete run.

Read the file's `samples` list. Each sample has its own UTC `collected_at`, raw
packet/handoff counts, calculated percentages, mean round-trip latency (`rtt_ms`),
`rtt_p95_ms`, `rtt_max_ms`, and `max_loss_burst_packets`.
RTT summaries include successful probe replies only; loss bursts count consecutive
missing replies within a window. These are probe statistics, not TCP counters.
The file timestamp marks when collection ended. Ten-second windows are simulated
time, separate from collection timestamps. `--seed` repeats measurements, not timestamps.
Idle means zero activity; missing data uses `null`. Rates with no observations are
also `null`. All data is synthetic; the collector should classify measurements,
not the `scenario` label. The polling API remains `TelemetrySimulator.fetch_network_parameters()`.

Model: correlated Gaussian delay, Pareto latency spikes, and Gilbert-Elliott burst
loss follow models supported by [Linux netem](https://github.com/iproute2/iproute2/blob/main/man/man8/tc-netem.8).
[RFC 3357](https://www.rfc-editor.org/rfc/rfc3357.html) motivates tracking loss bursts;
[RFC 7567](https://www.rfc-editor.org/rfc/rfc7567.html) motivates testing delay and loss together.
Numeric settings are **demo assumptions, not measured MANTA values**: 3 ms baseline
RTT, 0.1% background loss, 8% random loss, and up to 120 ms extra delay/10% loss
under degradation. Recovery ramps back to baseline; use `--samples 10` for more steps.
Each scenario starts independently, with a handoff at its start and every 30 simulated
seconds thereafter (idle disables activity; missing data hides observations).
JSON includes model metadata and sources. No real traffic is sent or captured.

## Where to start

1. [Python IP address tools](https://docs.python.org/3/library/ipaddress.html): learn how to validate addresses and work with an address pool. Start by choosing one unused IP/port.
2. [Ryu switching tutorial](https://book.ryu-sdn.org/en/html/switching_hub.html): see how the controller installs the rules your mappings will need.
3. [Ryu traffic monitor](https://book.ryu-sdn.org/en/html/traffic_monitor.html): see a periodic task using `hub.spawn` and `hub.sleep`; use that pattern for a rotation timer, then explore measurements for adaptive timing.
