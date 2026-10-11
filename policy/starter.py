def propose_mapping(service, reserved_mappings):
    # TODO: Choose an unused public IP/port for the service's real IP/port and protocol.
    raise NotImplementedError


def rotate_mapping(service, reserved_mappings, install_mapping):
    # TODO: Propose a mapping on a fixed timer and ask the controller to install it.
    # TODO: Mark active only on success; otherwise keep the previous mapping.
    # TODO: Reserve old mappings until the controller releases them.
    raise NotImplementedError
