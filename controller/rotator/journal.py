"""Persistent controller state and its versioned JSON representation."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .identities import PublicIdentity
from .models import MappingGeneration, RotationRecord, RotationRequest, RotationState


@dataclass
class ControllerState:
    active: dict[str, MappingGeneration] = field(default_factory=dict)
    records: dict[str, RotationRecord] = field(default_factory=dict)
    generation_ids: dict[str, int] = field(default_factory=dict)


class JsonStateStore:
    """Atomic journal for one controller process."""

    def __init__(self, path, initial_generations=()):
        self.path = Path(path)

        if not self.path.exists():
            self.save(ControllerState(
                active={g.service_id: g for g in initial_generations}))

    def load(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))

        if data["version"] != 1:
            raise ValueError("Unsupported rotation journal version")

        def generation(value):
            if value is None:
                return None

            return MappingGeneration(
                value["service_id"], value["generation_id"],
                PublicIdentity(**value["identity"]),
                PublicIdentity(**value["internal_endpoint"]))

        records = {}

        for key, value in data["records"].items():
            records[key] = RotationRecord(
                request=RotationRequest(**value["request"]),
                current_generation=generation(value["current_generation"]),
                generation_id=value["generation_id"],
                next_generation=generation(value["next_generation"]),
                state=RotationState(value["state"]),
                committed=value["committed"],
                cleanup_confirmed=value["cleanup_confirmed"],
                overlap_until=value["overlap_until"], error=value["error"],
                gateway_staged=value.get("gateway_staged", False))

        return ControllerState(
            active={key: generation(value) for key, value in data["active"].items()},
            records=records, generation_ids=data["generation_ids"])

    def save(self, state):
        data = json.dumps({"version": 1, **asdict(state)}, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")

        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())

            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class PersistenceError(RuntimeError):
    pass
