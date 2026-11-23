#include <core.p4>
#include <v1model.p4>

header ethernet_t {
    bit<48> dst_addr;
    bit<48> src_addr;
    bit<16> ether_type;
}

header ipv4_t {
    bit<4> version;
    bit<4> ihl;
    bit<8> diffserv;
    bit<16> total_len;
    bit<16> identification;
    bit<3> flags;
    bit<13> frag_offset;
    bit<8> ttl;
    bit<8> protocol;
    bit<16> hdr_checksum;
    bit<32> src_addr;
    bit<32> dst_addr;
}

header tcp_t {
    bit<16> src_port;
    bit<16> dst_port;
    bit<32> seq_no;
    bit<32> ack_no;
    bit<4> data_offset;
    bit<3> reserved;
    bit<9> flags;
    bit<16> window;
    bit<16> checksum;
    bit<16> urgent_ptr;
}

struct headers_t {
    ethernet_t ethernet;
    ipv4_t ipv4;
    tcp_t tcp;
}

struct metadata_t {
    bit<16> tcp_length;
    bit<32> active_generation_id;
}

parser ParserImpl(
    packet_in packet,
    out headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata
) {
    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            16w0x0800: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            8w6: parse_tcp;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }
}

control VerifyChecksumImpl(
    inout headers_t hdr,
    inout metadata_t meta
) {
    apply {
    }
}

control IngressImpl(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata
) {
    action set_active_generation(bit<32> generation_id) {
        meta.active_generation_id = generation_id;
    }

    table active_generation_selector {
        key = {
            standard_metadata.ingress_port: exact;
        }
        actions = {
            set_active_generation;
        }
        size = 2;
        default_action = set_active_generation(32w1);
    }

    action drop() {
        mark_to_drop(standard_metadata);
    }

    action set_forward_mapping(
        bit<32> internal_ip,
        bit<16> internal_port,
        bit<48> source_mac,
        bit<48> destination_mac
    ) {
        hdr.ipv4.dst_addr = internal_ip;
        hdr.tcp.dst_port = internal_port;
        hdr.ethernet.src_addr = source_mac;
        hdr.ethernet.dst_addr = destination_mac;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
        standard_metadata.egress_spec = 2;
    }

    action set_reverse_mapping(
        bit<32> public_ip,
        bit<16> public_port,
        bit<48> source_mac,
        bit<48> destination_mac
    ) {
        hdr.ipv4.src_addr = public_ip;
        hdr.tcp.src_port = public_port;
        hdr.ethernet.src_addr = source_mac;
        hdr.ethernet.dst_addr = destination_mac;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
        standard_metadata.egress_spec = 1;
    }

    table public_identity_forward {
        key = {
            meta.active_generation_id: exact;
            hdr.ipv4.dst_addr: exact;
            hdr.tcp.dst_port: exact;
        }
        actions = {
            set_forward_mapping;
            drop;
        }
        size = 16;
        default_action = drop();
    }

    table internal_endpoint_reverse {
        key = {
            meta.active_generation_id: exact;
            hdr.ipv4.src_addr: exact;
            hdr.tcp.src_port: exact;
        }
        actions = {
            set_reverse_mapping;
            drop;
        }
        size = 16;
        default_action = drop();
    }

    apply {
        active_generation_selector.apply();
        if (hdr.ipv4.isValid() && hdr.tcp.isValid()) {
            // The parser extracts the fixed IPv4 header, so the TCP length is
            // the IPv4 total length minus that 20-byte header.
            meta.tcp_length = hdr.ipv4.total_len - 16w20;
        }
        if (!hdr.ipv4.isValid() || !hdr.tcp.isValid() || hdr.ipv4.ttl <= 1) {
            drop();
        } else if (standard_metadata.ingress_port == 1) {
            public_identity_forward.apply();
        } else if (standard_metadata.ingress_port == 2) {
            internal_endpoint_reverse.apply();
        } else {
            drop();
        }
    }
}

control EgressImpl(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata
) {
    apply {
    }
}

control ComputeChecksumImpl(
    inout headers_t hdr,
    inout metadata_t meta
) {
    apply {
        update_checksum(
            hdr.ipv4.isValid(),
            {
                hdr.ipv4.version,
                hdr.ipv4.ihl,
                hdr.ipv4.diffserv,
                hdr.ipv4.total_len,
                hdr.ipv4.identification,
                hdr.ipv4.flags,
                hdr.ipv4.frag_offset,
                hdr.ipv4.ttl,
                hdr.ipv4.protocol,
                hdr.ipv4.src_addr,
                hdr.ipv4.dst_addr
            },
            hdr.ipv4.hdr_checksum,
            HashAlgorithm.csum16
        );
        update_checksum_with_payload(
            hdr.tcp.isValid(),
            {
                hdr.ipv4.src_addr,
                hdr.ipv4.dst_addr,
                8w0,
                hdr.ipv4.protocol,
                meta.tcp_length,
                hdr.tcp.src_port,
                hdr.tcp.dst_port,
                hdr.tcp.seq_no,
                hdr.tcp.ack_no,
                hdr.tcp.data_offset,
                hdr.tcp.reserved,
                hdr.tcp.flags,
                hdr.tcp.window,
                hdr.tcp.urgent_ptr
            },
            hdr.tcp.checksum,
            HashAlgorithm.csum16
        );
    }
}

control DeparserImpl(
    packet_out packet,
    in headers_t hdr
) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.tcp);
    }
}

V1Switch(
    ParserImpl(),
    VerifyChecksumImpl(),
    IngressImpl(),
    EgressImpl(),
    ComputeChecksumImpl(),
    DeparserImpl()
) main;
