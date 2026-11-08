def propose_mapping(service, reserved_mappings):
    # TODO: Choose an unused public IP/port for the service's real IP/port and protocol.
    raise NotImplementedError


def rotate_mapping(service, reserved_mappings, install_mapping):
    # TODO: Propose a mapping on a fixed timer and ask the controller to install it.
    # TODO: Mark active only on success; otherwise keep the previous mapping.
    # TODO: Reserve old mappings until the controller releases them.
    raise NotImplementedError

# Initial code proposal for policy management
# Needs interface with rotation controller and telemetry collector
'''
def rotate(interval_seconds=10):
  num_unconfirmed = 0
  network_online = True
  while(network_online):
    # Send current interval to rotation coordinator
    confirmed = await rotation_coordinator.update(interval_seconds)

    if (confirmed):
      healthy_network = check_status()
      if (healthy_network):
        interval_seconds = interval_seconds - 1
      elif (not healthy_network):
        interval_seconds = interval_seconds + 1
    
    # Skip this loop, try again next time
    # If this happens too often, report an error
    elif (not confirmed):
      num_unconfirmed = num_unconfirmed + 1
      if (num_unconfirmed > 10):
        # Error reporting/handling

    network_online = await network.status()
   
def check_status():
  status = await telemetry.status()
  if (status == “Positive”):
     return True
  elif (status == “Negative”):
    return False

'''