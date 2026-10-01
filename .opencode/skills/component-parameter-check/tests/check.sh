#!/usr/bin/env bash
# Self-test for tools/check_component_params.py against the fixtures in this
# directory. Exits nonzero if the tool mis-reports the expected statuses.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tool="$here/../tools/check_component_params.py"

expect_exit=4
set +e
out="$("$tool" "$here" 2>&1)"
code=$?
set -e

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

[ "$code" -eq "$expect_exit" ] || fail "expected exit $expect_exit, got $code"

# Only used components (schematic/PCB) are checked; library files are ignored.
grep -q "\.kicad_sym" <<<"$out" && fail "library symbols must not be scanned"

# Spot-check known fixtures.
grep -q "R5               schematic   Aisler OK    (MPN \"RC0603FR-0710KL\")" <<<"$out" \
    || fail "schematic resistor with MPN should be OK"
grep -q "C3               schematic   Aisler OK    (Smart Match" <<<"$out" \
    || fail "schematic capacitor Smart Match should be OK"
grep -q "R5               pcb         Aisler OK    (MPN \"RC0603FR-0710KL\")" <<<"$out" \
    || fail "pcb footprint with MPN should be OK"
grep -q "DigiKey OK    (311-10.0KCT-ND)" <<<"$out" \
    || fail "DigiKey number with a decimal should be OK"
grep -q "Origin OK    (TH)" <<<"$out" \
    || fail "Origin code should be OK"
grep -q "J1               schematic   Aisler WARN" <<<"$out" \
    || fail "connector with only a Value should be WARN"

echo "PASS: all checks passed ($(wc -l <<<"$out") report lines)"