# Policy

Choose public IPs/ports and when to change them.

## Where to start

1. [Python IP address tools](https://docs.python.org/3/library/ipaddress.html): learn how to validate addresses and work with an address pool. Start by choosing one unused IP/port.
2. [Ryu switching tutorial](https://book.ryu-sdn.org/en/html/switching_hub.html): see how the controller installs the rules your mappings will need.
3. [Ryu traffic monitor](https://book.ryu-sdn.org/en/html/traffic_monitor.html): see a periodic task using `hub.spawn` and `hub.sleep`; use that pattern for a rotation timer, then explore measurements for adaptive timing.
