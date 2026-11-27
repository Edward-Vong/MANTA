"""BMv2/P4Runtime gateway adapter for the generation-aware prototype."""

import asyncio
import ipaddress
import queue
import sys
from pathlib import Path

from .interface import GatewayRuleInterface
from .models import (
    GatewayResult,
    InternalEndpoint,
    MappingGeneration as GatewayMappingGeneration,
    PublicIdentity,
)


REPOSITORY_DIR = Path(__file__).resolve().parents[1]
DEFAULT_P4INFO_PATH = REPOSITORY_DIR / "network" / "build" / "manta_minimal.p4info.txt"
DEFAULT_BMV2_JSON_PATH = REPOSITORY_DIR / "network" / "build" / "manta_minimal.json"
DEFAULT_P4RUNTIME_ADDRESS = "127.0.0.1:9559"
DEFAULT_GENERATION_ID = 1
DEVICE_ID = 0
ELECTION_ID_LOW = 1

PUBLIC_GATEWAY_MAC = "02:00:00:00:01:01"
INTERNAL_GATEWAY_MAC = "02:00:00:00:01:02"
PUBLIC_NEXT_HOP_MAC = "02:00:00:00:00:01"
PRIVATE_NEXT_HOP_MAC = "02:00:00:00:00:02"


def _load_p4runtime_bindings():
    """Load optional P4Runtime dependencies only when the adapter is used."""

    import grpc
    from google.protobuf import text_format
    from p4.config.v1 import p4info_pb2
    from p4.v1 import p4runtime_pb2, p4runtime_pb2_grpc

    return (
        grpc,
        text_format,
        p4info_pb2,
        p4runtime_pb2,
        p4runtime_pb2_grpc,
    )


def _find_named(items, short_name):
    for item in items:
        name = item.preamble.name if hasattr(item, "preamble") else item.name
        if name == short_name or name.endswith("." + short_name):
            return item
    raise RuntimeError(f"P4Info object not found: {short_name}")


def _table_match_fields(table):
    for attribute in ("match_fields", "match"):
        fields = getattr(table, attribute, None)
        if fields:
            return fields
    raise RuntimeError(
        f"P4Info table {table.preamble.name} has no match field metadata"
    )


def _required_match_fields(table, field_names):
    fields = {field.name: field for field in _table_match_fields(table)}
    missing = [name for name in field_names if name not in fields]
    if missing:
        raise RuntimeError(
            f"P4Info table {table.preamble.name} is missing match fields: {missing}"
        )
    return fields


def _validate_action_for_table(table, action):
    action_refs = getattr(table, "action_refs", None)
    if action_refs and action.preamble.id not in {ref.id for ref in action_refs}:
        raise RuntimeError(
            f"P4Info action {action.preamble.name} (ID {action.preamble.id}) "
            f"is not referenced by table {table.preamble.name} "
            f"(ID {table.preamble.id})"
        )


def _action_parameter_ids(action, parameters):
    params_by_name = {param.name: param.id for param in action.params}
    missing = [name for name, _ in parameters if name not in params_by_name]
    if missing:
        raise RuntimeError(
            f"P4Info action {action.preamble.name} is missing parameters: {missing}"
        )
    return params_by_name


def _bytes_ip(value):
    return ipaddress.ip_address(value).packed


def _bytes_port(value):
    return value.to_bytes(2, byteorder="big")


def _bytes_generation(value):
    return value.to_bytes(4, byteorder="big")


def _bytes_mac(value):
    return bytes.fromhex(value.replace(":", ""))


def normalize_mapping_generation(generation):
    """Convert coordinator or gateway generations to the canonical gateway model.

    The merged coordinator currently represents its internal endpoint with the
    same ``public_ip``/``public_port`` shape used by its identity model and
    stores the protocol on that identity.  The gateway boundary accepts that
    shape and the canonical gateway ``MappingGeneration`` shape, but exposes
    only the current TCP prototype to P4Runtime.
    """

    if isinstance(generation, GatewayMappingGeneration):
        service_id = generation.service_id
        generation_id = generation.generation_id
        public_ip = generation.identity.public_ip
        public_port = generation.identity.public_port
        internal_ip = generation.internal_endpoint.internal_ip
        internal_port = generation.internal_endpoint.internal_port
        protocol = generation.protocol
        identity_protocol = generation.identity.protocol
    else:
        service_id = getattr(generation, "service_id", None)
        generation_id = getattr(generation, "generation_id", None)
        identity = getattr(generation, "identity", None)
        endpoint = getattr(generation, "internal_endpoint", None)
        public_ip = getattr(identity, "public_ip", None)
        public_port = getattr(identity, "public_port", None)
        identity_protocol = getattr(identity, "protocol", None)
        internal_ip = getattr(endpoint, "internal_ip", None)
        internal_port = getattr(endpoint, "internal_port", None)
        if internal_ip is None:
            internal_ip = getattr(endpoint, "public_ip", None)
        if internal_port is None:
            internal_port = getattr(endpoint, "public_port", None)
        protocol = getattr(generation, "protocol", None) or identity_protocol or "TCP"

    if not isinstance(service_id, str) or not service_id.strip():
        raise ValueError("mapping generation requires a non-empty service_id")
    if isinstance(generation_id, bool) or not isinstance(generation_id, int) or generation_id < 1:
        raise ValueError("mapping generation requires a positive integer generation_id")
    if not isinstance(protocol, str) or protocol.upper() != "TCP":
        raise ValueError("only TCP mappings are supported by the current P4 prototype")
    if identity_protocol is not None and (
        not isinstance(identity_protocol, str) or identity_protocol.upper() != "TCP"
    ):
        raise ValueError("only TCP public identities are supported by the current P4 prototype")

    try:
        public_ip = str(ipaddress.ip_address(public_ip))
        internal_ip = str(ipaddress.ip_address(internal_ip))
    except (TypeError, ValueError) as error:
        raise ValueError("mapping generation contains invalid IP address fields") from error

    for name, value in (("public_port", public_port), ("internal_port", internal_port)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError(f"mapping generation contains an invalid {name}")

    return GatewayMappingGeneration(
        service_id=service_id,
        generation_id=generation_id,
        identity=PublicIdentity(public_ip, public_port, "TCP"),
        internal_endpoint=InternalEndpoint(internal_ip, internal_port),
        protocol="TCP",
    )


def _exact_match(p4runtime_pb2, field, value):
    return p4runtime_pb2.FieldMatch(
        field_id=field.id,
        exact=p4runtime_pb2.FieldMatch.Exact(value=value),
    )


def _action(p4runtime_pb2, action, parameters):
    params_by_name = _action_parameter_ids(action, parameters)
    return p4runtime_pb2.TableAction(
        action=p4runtime_pb2.Action(
            action_id=action.preamble.id,
            params=[
                p4runtime_pb2.Action.Param(param_id=params_by_name[name], value=value)
                for name, value in parameters
            ],
        )
    )


def _table_entry(p4runtime_pb2, table, matches, table_action=None):
    entry = p4runtime_pb2.TableEntry(table_id=table.preamble.id, match=matches)
    if table_action is not None:
        entry.action.CopyFrom(table_action)
    return p4runtime_pb2.Entity(table_entry=entry)


def _print_rpc_error(operation, error):
    debug_error_string = getattr(error, "debug_error_string", None)
    debug_error_string = debug_error_string() if callable(debug_error_string) else ""
    print(
        f"{operation} failed: gRPC status code={error.code()} "
        f"details={error.details()!r} "
        f"debug error string={debug_error_string!r}",
        file=sys.stderr,
    )


def _print_update_details(
    text_format,
    label,
    table,
    action,
    match_fields,
    match_values,
    parameters,
    request,
):
    parameter_ids = _action_parameter_ids(action, parameters)
    print(f"{label} update: table={table.preamble.name} table_id={table.preamble.id}")
    print(f"  action={action.preamble.name} action_id={action.preamble.id}")
    for field_name, value in zip(match_fields, match_values):
        field = _required_match_fields(table, [field_name])[field_name]
        print(f"  match field={field.name} field_id={field.id} value=0x{value.hex()}")
    for parameter_name, value in parameters:
        print(
            f"  action parameter={parameter_name} "
            f"parameter_id={parameter_ids[parameter_name]} value=0x{value.hex()}"
        )
    print("P4Runtime WriteRequest:")
    print(text_format.MessageToString(request))


class P4RuntimeSession:
    """Persistent P4Runtime session with BMv2 master arbitration."""

    def __init__(self, channel, bindings):
        _, _, _, p4runtime_pb2, p4runtime_pb2_grpc = bindings
        self.channel = channel
        self.p4runtime_pb2 = p4runtime_pb2
        self.stub = p4runtime_pb2_grpc.P4RuntimeStub(channel)
        self.requests = queue.Queue()
        self.closed = False

        def request_stream():
            while True:
                request = self.requests.get()
                if request is None:
                    return
                yield request

        self.responses = self.stub.StreamChannel(request_stream())
        self.election_id = p4runtime_pb2.Uint128(high=0, low=ELECTION_ID_LOW)
        self.requests.put(
            p4runtime_pb2.StreamMessageRequest(
                arbitration=p4runtime_pb2.MasterArbitrationUpdate(
                    device_id=DEVICE_ID,
                    election_id=self.election_id,
                )
            )
        )
        response = next(self.responses)
        if not response.HasField("arbitration"):
            raise RuntimeError("P4Runtime arbitration returned a non-arbitration response")
        arbitration = response.arbitration
        if arbitration.device_id != DEVICE_ID:
            raise RuntimeError("P4Runtime arbitration returned the wrong device_id")
        if arbitration.election_id != self.election_id:
            raise RuntimeError("P4Runtime arbitration did not make this client primary")
        if arbitration.status.code != 0:
            raise RuntimeError(
                "P4Runtime master arbitration failed: "
                f"{arbitration.status.message or 'client is not primary'}"
            )

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.requests.put(None)
        self.channel.close()


class Bmv2P4RuntimeGateway(GatewayRuleInterface):
    """Generation-aware adapter for the current one-service BMv2 prototype."""

    def __init__(
        self,
        p4runtime_address=DEFAULT_P4RUNTIME_ADDRESS,
        p4info_path=DEFAULT_P4INFO_PATH,
        bmv2_json_path=DEFAULT_BMV2_JSON_PATH,
        *, backend_macs=None, trace_writes=True,
    ):
        self.p4runtime_address = p4runtime_address
        self.p4info_path = Path(p4info_path)
        self.bmv2_json_path = Path(bmv2_json_path)
        self.backend_macs = None if backend_macs is None else dict(backend_macs)
        self.trace_writes = trace_writes
        self._bindings = None
        self._p4info = None
        self._session = None
        self._pipeline_configured = False
        self._tables = {}
        self._actions = {}
        self._selector_table = None
        self._selector_field = None
        self._selector_action = None
        self._generations = {}
        self._staged = set()
        self._verified = set()
        self._active = {}
        self._pre_activation = {}
        self._activation_committed = set()
        self._completed_rollbacks = set()

    def _not_implemented(self, operation, generation_id=None):
        return GatewayResult.failure(
            operation,
            "NOT_IMPLEMENTED",
            "Current BMv2 pipeline does not support the requested lifecycle semantics",
            generation_id,
        )

    def _local_active_generation(self, service_id):
        """Return local selector state; selector state is tracked locally."""

        return self._active.setdefault(service_id, DEFAULT_GENERATION_ID)

    def _ensure_ready(self):
        if self._session is not None:
            return self._bindings[3]
        if not self.p4info_path.is_file() or not self.bmv2_json_path.is_file():
            raise RuntimeError("P4Info and BMv2 JSON must both be regular files")

        self._bindings = _load_p4runtime_bindings()
        grpc, text_format, p4info_pb2, p4runtime_pb2, _ = self._bindings
        self._p4info = p4info_pb2.P4Info()
        text_format.Parse(self.p4info_path.read_text(), self._p4info)
        self._tables = {
            name: _find_named(self._p4info.tables, name)
            for name in (
                "public_identity_forward",
                "internal_endpoint_reverse",
                "active_generation_selector",
            )
        }
        self._actions = {
            name: _find_named(self._p4info.actions, name)
            for name in (
                "set_forward_mapping",
                "set_reverse_mapping",
                "set_active_generation",
            )
        }
        self._selector_table = self._tables["active_generation_selector"]
        self._selector_action = self._actions["set_active_generation"]
        self._selector_field = _required_match_fields(
            self._selector_table, ("standard_metadata.ingress_port",)
        )
        _validate_action_for_table(
            self._tables["public_identity_forward"], self._actions["set_forward_mapping"]
        )
        _validate_action_for_table(
            self._tables["internal_endpoint_reverse"], self._actions["set_reverse_mapping"]
        )
        _validate_action_for_table(self._selector_table, self._selector_action)
        _action_parameter_ids(self._selector_action, [("generation_id", b"")])
        self._session = P4RuntimeSession(
            grpc.insecure_channel(self.p4runtime_address), self._bindings
        )
        if not self._pipeline_configured:
            self._session.stub.SetForwardingPipelineConfig(
                p4runtime_pb2.SetForwardingPipelineConfigRequest(
                    device_id=DEVICE_ID,
                    election_id=self._session.election_id,
                    action=p4runtime_pb2.SetForwardingPipelineConfigRequest.VERIFY_AND_COMMIT,
                    config=p4runtime_pb2.ForwardingPipelineConfig(
                        p4info=self._p4info,
                        p4_device_config=self.bmv2_json_path.read_bytes(),
                    ),
                )
            )
            self._pipeline_configured = True
        return p4runtime_pb2

    def _mapping_entities(self, generation: GatewayMappingGeneration):
        p4runtime_pb2 = self._bindings[3]
        forward_names = (
            "meta.active_generation_id",
            "hdr.ipv4.dst_addr",
            "hdr.tcp.dst_port",
        )
        reverse_names = (
            "meta.active_generation_id",
            "hdr.ipv4.src_addr",
            "hdr.tcp.src_port",
        )
        forward_table = self._tables["public_identity_forward"]
        reverse_table = self._tables["internal_endpoint_reverse"]
        forward_fields = _required_match_fields(forward_table, forward_names)
        reverse_fields = _required_match_fields(reverse_table, reverse_names)
        forward_values = (
            _bytes_generation(generation.generation_id),
            _bytes_ip(generation.identity.public_ip),
            _bytes_port(generation.identity.public_port),
        )
        reverse_values = (
            _bytes_generation(generation.generation_id),
            _bytes_ip(generation.internal_endpoint.internal_ip),
            _bytes_port(generation.internal_endpoint.internal_port),
        )
        backend_mac = PRIVATE_NEXT_HOP_MAC
        if self.backend_macs is not None:
            backend_mac = self.backend_macs[generation.internal_endpoint.internal_ip]
        forward_parameters = (
            ("internal_ip", _bytes_ip(generation.internal_endpoint.internal_ip)),
            ("internal_port", _bytes_port(generation.internal_endpoint.internal_port)),
            ("source_mac", _bytes_mac(INTERNAL_GATEWAY_MAC)),
            ("destination_mac", _bytes_mac(backend_mac)),
        )
        reverse_parameters = (
            ("public_ip", _bytes_ip(generation.identity.public_ip)),
            ("public_port", _bytes_port(generation.identity.public_port)),
            ("source_mac", _bytes_mac(PUBLIC_GATEWAY_MAC)),
            ("destination_mac", _bytes_mac(PUBLIC_NEXT_HOP_MAC)),
        )
        forward_action = self._actions["set_forward_mapping"]
        reverse_action = self._actions["set_reverse_mapping"]
        forward = _table_entry(
            p4runtime_pb2,
            forward_table,
            [
                    _exact_match(p4runtime_pb2, forward_fields[name], value)
                for name, value in zip(forward_names, forward_values)
            ],
            _action(p4runtime_pb2, forward_action, forward_parameters),
        )
        reverse = _table_entry(
            p4runtime_pb2,
            reverse_table,
            [
                    _exact_match(p4runtime_pb2, reverse_fields[name], value)
                for name, value in zip(reverse_names, reverse_values)
            ],
            _action(p4runtime_pb2, reverse_action, reverse_parameters),
        )
        return (
            ("forward", forward_table, forward_action, forward_names, forward_values, forward_parameters, forward),
            ("reverse", reverse_table, reverse_action, reverse_names, reverse_values, reverse_parameters, reverse),
        )

    def _selector_entities(self, generation_id):
        p4runtime_pb2 = self._bindings[3]
        field = self._selector_field["standard_metadata.ingress_port"]
        parameters = [("generation_id", _bytes_generation(generation_id))]
        entities = []
        for port in (1, 2):
            value = _bytes_port(port)
            entity = _table_entry(
                p4runtime_pb2,
                self._selector_table,
                [_exact_match(p4runtime_pb2, field, value)],
                _action(p4runtime_pb2, self._selector_action, parameters),
            )
            entities.append(
                (
                    f"selector-port-{port}",
                    self._selector_table,
                    self._selector_action,
                    ("standard_metadata.ingress_port",),
                    (value,),
                    parameters,
                    entity,
                )
            )
        return entities

    def _write_update(self, entity, update_type):
        p4runtime_pb2 = self._bindings[3]
        request = p4runtime_pb2.WriteRequest(
            device_id=DEVICE_ID,
            election_id=self._session.election_id,
        )
        update = p4runtime_pb2.Update(type=update_type)
        update.entity.CopyFrom(entity)
        request.updates.append(update)
        self._session.stub.Write(request)

    def _write_selector_update(
        self,
        entity,
        update_type,
        text_format,
        label,
        table,
        action,
        names,
        values,
        parameters,
    ):
        p4runtime_pb2 = self._bindings[3]
        request = p4runtime_pb2.WriteRequest(
            device_id=DEVICE_ID,
            election_id=self._session.election_id,
        )
        update = p4runtime_pb2.Update(type=update_type)
        update.entity.CopyFrom(entity)
        request.updates.append(update)
        if self.trace_writes:
            _print_update_details(text_format, label, table, action, names, values, parameters, request)
        self._session.stub.Write(request)

    def _install_entries(self, generation, allow_existing=True):
        p4runtime_pb2 = self._ensure_ready()
        text_format = self._bindings[1]
        for label, table, action, names, values, parameters, entity in self._mapping_entities(generation):
            request = p4runtime_pb2.WriteRequest(
                device_id=DEVICE_ID,
                election_id=self._session.election_id,
            )
            update = p4runtime_pb2.Update(type=p4runtime_pb2.Update.INSERT)
            update.entity.CopyFrom(entity)
            request.updates.append(update)
            if self.trace_writes:
                _print_update_details(text_format, label, table, action, names, values, parameters, request)
            try:
                self._session.stub.Write(request)
            except Exception as error:
                grpc = self._bindings[0]
                if not (allow_existing and isinstance(error, grpc.RpcError) and error.code() == grpc.StatusCode.ALREADY_EXISTS):
                    raise
                self._write_update(entity, p4runtime_pb2.Update.MODIFY)

    def _delete_entries(self, generation):
        p4runtime_pb2 = self._ensure_ready()
        for _, _, _, _, _, _, entity in self._mapping_entities(generation):
            try:
                self._write_update(entity, p4runtime_pb2.Update.DELETE)
            except Exception as error:
                grpc = self._bindings[0]
                if not (isinstance(error, grpc.RpcError) and error.code() == grpc.StatusCode.NOT_FOUND):
                    raise

    def _write_selector_generation(self, generation_id):
        p4runtime_pb2 = self._ensure_ready()
        text_format = self._bindings[1]
        for label, table, action, names, values, parameters, entity in self._selector_entities(generation_id):
            update_type = (
                p4runtime_pb2.Update.MODIFY
                if self._read_table_entry(entity)
                else p4runtime_pb2.Update.INSERT
            )
            self._write_selector_update(
                entity,
                update_type,
                text_format,
                label,
                table,
                action,
                names,
                values,
                parameters,
            )

    def _read_table_entry(self, entity):
        p4runtime_pb2 = self._bindings[3]
        table_entry = entity.table_entry
        key_entity = p4runtime_pb2.Entity(
            table_entry=p4runtime_pb2.TableEntry(
                table_id=table_entry.table_id,
                match=table_entry.match,
            )
        )
        request = p4runtime_pb2.ReadRequest(
            device_id=DEVICE_ID,
            entities=[key_entity],
        )
        for response in self._session.stub.Read(request):
            if response.entities:
                return True
        return False

    def _failure(self, operation, generation_id, error):
        if hasattr(error, "code") and callable(error.code):
            if self.trace_writes:
                _print_rpc_error(f"P4Runtime {operation}", error)
            else:
                print(f"The gateway reported {error.details() or error.code()}.", file=sys.stderr)
        return GatewayResult.failure(operation, "P4RUNTIME_ERROR", str(error), generation_id)

    def apply_mapping(self, generation) -> GatewayResult:
        """Install one mapping directly without changing the selector."""

        try:
            normalized = normalize_mapping_generation(generation)
            self._install_entries(normalized)
            key = (normalized.service_id, normalized.generation_id)
            self._generations[key] = normalized
            return GatewayResult.ok("apply_mapping", "mapping entries installed", normalized.generation_id)
        except Exception as error:
            if isinstance(error, ValueError):
                return GatewayResult.failure(
                    "apply_mapping",
                    "INVALID_GENERATION",
                    str(error),
                    getattr(generation, "generation_id", None),
                )
            return self._failure("apply_mapping", getattr(generation, "generation_id", None), error)

    def stage_mapping(self, generation) -> GatewayResult:
        try:
            normalized = normalize_mapping_generation(generation)
        except ValueError as error:
            return GatewayResult.failure(
                "stage_mapping",
                "INVALID_GENERATION",
                str(error),
                getattr(generation, "generation_id", None),
            )

        key = (normalized.service_id, normalized.generation_id)
        if key in self._staged:
            return GatewayResult.ok("stage_mapping", "mapping already staged", normalized.generation_id)
        try:
            self._install_entries(normalized)
            self._generations[key] = normalized
            self._staged.add(key)
            self._verified.discard(key)
            return GatewayResult.ok("stage_mapping", "mapping entries staged", normalized.generation_id)
        except Exception as error:
            return self._failure("stage_mapping", normalized.generation_id, error)

    def verify_staged(self, service_id: str, generation_id: int) -> GatewayResult:
        key = (service_id, generation_id)
        generation = self._generations.get(key)
        if key not in self._staged or generation is None:
            return GatewayResult.failure("verify_staged", "NOT_STAGED", "generation is not staged", generation_id)
        try:
            self._ensure_ready()
            entities = self._mapping_entities(generation)
            if not all(self._read_table_entry(item[-1]) for item in entities):
                return GatewayResult.failure("verify_staged", "NOT_READY", "one or more entries are missing", generation_id)
            self._verified.add(key)
            return GatewayResult.ok("verify_staged", "both mapping entries verified", generation_id)
        except Exception as error:
            return self._failure("verify_staged", generation_id, error)

    def activate_mapping(self, service_id: str, new_generation_id: int, previous_generation_id: int | None) -> GatewayResult:
        key = (service_id, new_generation_id)
        if key not in self._staged or key not in self._verified:
            return GatewayResult.failure("activate_mapping", "NOT_VERIFIED", "new generation must be staged and verified", new_generation_id)
        current = None
        try:
            current = self._local_active_generation(service_id)
            if previous_generation_id is not None and current != previous_generation_id:
                return GatewayResult.failure("activate_mapping", "ACTIVE_GENERATION_MISMATCH", "previous generation is not currently active", new_generation_id)
            self._pre_activation[service_id] = current
            self._write_selector_generation(new_generation_id)
            self._active[service_id] = new_generation_id
            self._activation_committed.add(service_id)
            self._pre_activation.pop(service_id, None)
            self._staged.discard(key)
            self._verified.discard(key)
            return GatewayResult.ok(
                "activate_mapping",
                "active-generation selector table writes succeeded",
                new_generation_id,
            )
        except Exception as error:
            if current is not None:
                try:
                    self._write_selector_generation(current)
                except Exception:
                    pass
            return self._failure("activate_mapping", new_generation_id, error)

    def retire_generation(self, service_id: str, generation_id: int) -> GatewayResult:
        try:
            current = self._local_active_generation(service_id)
            if current == generation_id:
                return GatewayResult.failure("retire_generation", "ACTIVE_GENERATION", "cannot retire the active generation", generation_id)
            generation = self._generations.get((service_id, generation_id))
            if generation is None:
                return GatewayResult.ok("retire_generation", "generation already absent", generation_id)
            self._delete_entries(generation)
            key = (service_id, generation_id)
            self._generations.pop(key, None)
            self._staged.discard(key)
            self._verified.discard(key)
            return GatewayResult.ok("retire_generation", "generation entries retired", generation_id)
        except Exception as error:
            return self._failure("retire_generation", generation_id, error)

    def rollback_to(self, service_id: str, generation_id: int) -> GatewayResult:
        pre_activation = self._pre_activation.get(service_id)
        staged_keys = [key for key in self._staged if key[0] == service_id]
        rollback_key = (service_id, generation_id)
        current = self._local_active_generation(service_id)
        if not staged_keys and rollback_key in self._completed_rollbacks:
            return GatewayResult.ok(
                "rollback_to",
                "staged cleanup already completed",
                generation_id,
            )
        if (
            service_id in self._activation_committed
            and pre_activation is None
            and not staged_keys
        ):
            return GatewayResult.failure("rollback_to", "COMMITTED", "cannot undo a committed activation", generation_id)
        try:
            if pre_activation is not None and generation_id != pre_activation:
                return GatewayResult.failure("rollback_to", "UNSAFE_TARGET", "rollback target was not active before the attempted activation", generation_id)
            if staged_keys and generation_id != current and pre_activation is None:
                return GatewayResult.failure("rollback_to", "UNSAFE_TARGET", "rollback target is not the current active generation", generation_id)
            if current != generation_id:
                self._write_selector_generation(generation_id)
            for key in staged_keys:
                generation = self._generations.get(key)
                if generation is not None:
                    self._delete_entries(generation)
                self._generations.pop(key, None)
                self._staged.discard(key)
                self._verified.discard(key)
            self._active[service_id] = generation_id
            self._pre_activation.pop(service_id, None)
            self._completed_rollbacks.add(rollback_key)
            return GatewayResult.ok("rollback_to", "pre-commit state restored", generation_id)
        except Exception as error:
            return self._failure("rollback_to", generation_id, error)


class AsyncBmv2CoordinatorAdapter:
    """Async coordinator boundary over the synchronous BMv2 gateway.

    The Rotation Coordinator passes generation objects and awaits operations;
    ``Bmv2P4RuntimeGateway`` intentionally keeps the canonical synchronous,
    service-scoped GatewayRuleInterface. This adapter translates that boundary
    without changing either public interface.
    """

    def __init__(self, gateway: Bmv2P4RuntimeGateway):
        self.gateway = gateway

    async def active_generation_id(self, service_id):
        """Return locally tracked selector state; BMv2 selector readback is not used."""

        return await asyncio.to_thread(
            self.gateway._local_active_generation,
            service_id,
        )

    async def stage_mapping(self, generation):
        result = await asyncio.to_thread(self.gateway.stage_mapping, generation)
        return result.success

    async def verify_staged(self, generation):
        result = await asyncio.to_thread(
            self.gateway.verify_staged,
            generation.service_id,
            generation.generation_id,
        )
        return result.success

    async def activate_mapping(self, new_generation, previous_generation):
        previous_id = None if previous_generation is None else previous_generation.generation_id
        result = await asyncio.to_thread(
            self.gateway.activate_mapping,
            new_generation.service_id,
            new_generation.generation_id,
            previous_id,
        )
        return result.success

    async def retire_generation(self, generation):
        result = await asyncio.to_thread(
            self.gateway.retire_generation,
            generation.service_id,
            generation.generation_id,
        )
        return result.success

    async def rollback_to(self, generation):
        result = await asyncio.to_thread(
            self.gateway.rollback_to,
            generation.service_id,
            generation.generation_id,
        )
        return result.success
