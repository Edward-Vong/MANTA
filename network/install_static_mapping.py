"""Install the Milestone 3A-1 static mapping through the gateway adapter."""

import sys
from pathlib import Path


REPOSITORY_DIR = Path(__file__).resolve().parents[1]
if str(REPOSITORY_DIR) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_DIR))

from gateway.bmv2 import Bmv2P4RuntimeGateway
from gateway.models import InternalEndpoint, MappingGeneration, PublicIdentity


def install():
    generation = MappingGeneration(
        service_id="manta-http",
        generation_id=1,
        identity=PublicIdentity("10.0.0.100", 8080, "TCP"),
        internal_endpoint=InternalEndpoint("10.0.1.2", 80),
        protocol="TCP",
    )
    result = Bmv2P4RuntimeGateway().apply_mapping(generation)
    if not result.success:
        raise SystemExit(
            f"static mapping installation failed: {result.code}: {result.message}"
        )
    return result


if __name__ == "__main__":
    install()
