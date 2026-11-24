"""Public identities and their lease pool."""

from dataclasses import dataclass
from ipaddress import ip_address


@dataclass(frozen=True)
class PublicIdentity:
    public_ip: str
    public_port: int
    protocol: str = "tcp"

    def __post_init__(self):
        ip_address(self.public_ip)

        if not 1 <= self.public_port <= 65535:
            raise ValueError("Port must be between 1 and 65535")

        if self.protocol not in ("tcp", "udp"):
            raise ValueError("Protocol must be tcp or udp")


class IdentityPool:
    """Ten virtual service addresses for the local simulation."""

    def __init__(self, public_port=8080, protocol="tcp"):
        self.identities = tuple(
            PublicIdentity(f"10.0.0.{number}", public_port, protocol)
            for number in range(100, 110)
        )
        self._leases = {}

    def allocate(self, lease_id, service_id, protocol):
        existing = self._leases.get(lease_id)

        if existing is not None:
            if existing.protocol != protocol:
                raise ValueError("Lease protocol does not match")

            return existing

        for identity in self.identities:
            if identity.protocol == protocol and identity not in self._leases.values():
                self.reserve(identity, lease_id)

                return identity

        raise RuntimeError(f"No available {protocol} identities")

    def reserve(self, identity, lease_id):
        if not lease_id or identity not in self.identities:
            raise ValueError("A lease ID and an identity from this pool are required")

        existing = self._leases.get(lease_id)

        if existing == identity:
            return

        if existing is not None or identity in self._leases.values():
            raise ValueError("Lease or identity is already reserved")

        self._leases[lease_id] = identity

    def release(self, identity, lease_id):
        if self._leases.get(lease_id) == identity:
            del self._leases[lease_id]
