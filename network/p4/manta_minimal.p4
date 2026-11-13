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

struct headers_t {
    ethernet_t ethernet;
    ipv4_t ipv4;
}

struct metadata_t {
}

// Static Milestone 2E-2 smoke-path addresses.  These will be replaced by
// mapping-controlled table entries in a later pipeline.
const bit<48> PUBLIC_GATEWAY_MAC = 48w0x020000000101;
const bit<48> INTERNAL_GATEWAY_MAC = 48w0x020000000102;
const bit<48> CLIENT_MAC = 48w0x020000000001;
const bit<48> SERVER_MAC = 48w0x020000000002;

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
    apply {
        if (hdr.ipv4.isValid() && hdr.ipv4.ttl > 1) {
            if (standard_metadata.ingress_port == 1) {
                hdr.ethernet.src_addr = INTERNAL_GATEWAY_MAC;
                hdr.ethernet.dst_addr = SERVER_MAC;
                hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
                standard_metadata.egress_spec = 2;
            } else if (standard_metadata.ingress_port == 2) {
                hdr.ethernet.src_addr = PUBLIC_GATEWAY_MAC;
                hdr.ethernet.dst_addr = CLIENT_MAC;
                hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
                standard_metadata.egress_spec = 1;
            }
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
    }
}

control DeparserImpl(
    packet_out packet,
    in headers_t hdr
) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
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
