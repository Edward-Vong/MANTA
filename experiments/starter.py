def run_baseline(network):
    # TODO: Run traffic without rotation; record delay, throughput, and loss.
    raise NotImplementedError


def test_rotation(network):
    # TODO: Repeat the baseline workload with rotation enabled.
    # TODO: Check current mappings work, old ones reject new connections, and flows survive.
    # TODO: Check failed updates preserve service and lab scans cannot bypass MANTA.
    raise NotImplementedError


def save_results(settings, results):
    # TODO: Save experiment settings and measurements for comparison.
    raise NotImplementedError
