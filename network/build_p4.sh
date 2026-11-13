#!/usr/bin/env bash
set -euo pipefail

NETWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P4C_BIN="${P4C_BIN:-p4c}"
P4_SOURCE="${NETWORK_DIR}/p4/manta_minimal.p4"
BUILD_DIR="${NETWORK_DIR}/build"
BMV2_JSON="${BUILD_DIR}/manta_minimal.json"
P4INFO="${BUILD_DIR}/manta_minimal.p4info.txt"

if ! command -v "${P4C_BIN}" >/dev/null 2>&1; then
    echo "p4c was not found: ${P4C_BIN}" >&2
    exit 1
fi

mkdir -p "${BUILD_DIR}"
"${P4C_BIN}" \
    --target bmv2 \
    --arch v1model \
    --std p4-16 \
    -o "${BUILD_DIR}" \
    --p4runtime-files "${P4INFO}" \
    --p4runtime-format text \
    "${P4_SOURCE}"

if [[ ! -f "${BMV2_JSON}" ]]; then
    echo "BMv2 JSON artifact is missing or not a regular file: ${BMV2_JSON}" >&2
    exit 1
fi

if [[ ! -f "${P4INFO}" ]]; then
    echo "P4Info artifact is missing or not a regular file: ${P4INFO}" >&2
    exit 1
fi

echo "BMv2 JSON: ${BMV2_JSON}"
echo "P4Info: ${P4INFO}"
