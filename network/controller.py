def setup_forwarding(datapath):
    # TODO: Call from Ryu's switch connection handler to install forwarding rules.
    raise NotImplementedError


def install_mapping(datapath, mapping):
    # TODO: Install and verify request/reply rules before activating the mapping.
    # TODO: Report success and publish to authorized clients; restore old state on failure.
    raise NotImplementedError


def retire_mapping(datapath, mapping):
    # TODO: Reject new connections but preserve established flows.
    # TODO: Tell policy when the old mapping is safe to reuse.
    raise NotImplementedError
