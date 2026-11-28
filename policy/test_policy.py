from policy import PolicyManager, TelemetryCollector

def main(policy_manager, telemetry_collector):
    print("xyz")

if __name__ == "__main__":
    tc = TelemetryCollector("Service address")
    pm = PolicyManager("Rotation coordinator here", tc, 10)
    main()