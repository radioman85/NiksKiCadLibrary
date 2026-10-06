#!/usr/bin/env bash
# Self-test for tools/digikey_order.py
#
# Checks that:
#   - equal parts are merged into one order line with the summed quantity
#   - parts without a DigiKey property are reported as missing, not uploaded
#   - footprints flagged "exclude_from_bom" are skipped
#   - --generic-map fills a generic part, --multiplier scales the quantity
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tool="$here/../tools/digikey_order.py"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

cat >"$out/generics.csv" <<'CSV'
Value,Footprint,DigiKey
10k,R_0603,311-10.0KCRCT-ND
CSV

run() { python3 "$tool" "$@" >"$out/run.log" 2>&1 || return $?; }

fail() { echo "FAIL: $1"; exit 1; }

# 1. plain run
run "$here/sample.kicad_pcb" --out-dir "$out/a" || fail "run failed"
grep -q '^490-10698-1-ND,C1 C2,2$' "$out/a/digikey_upload.csv" \
  || fail "C1/C2 not merged into one line"
head -1 "$out/a/digikey_upload.csv" \
  | grep -qx 'Digi-Key Part Number,Customer Reference,Quantity' \
  || fail "wrong upload header"
[ "$(wc -l <"$out/a/digikey_upload.csv")" = "2" ] \
  || fail "unexpected number of upload lines (missing parts must not be listed)"
grep -q 'R1' "$out/a/digikey_missing.csv" || fail "R1 not reported as missing"
grep -q 'U1' "$out/a/digikey_list.txt" && fail "excluded-from-BOM part was uploaded"

# 2. generic map + multiplier
run "$here/sample.kicad_pcb" --out-dir "$out/b" --generic-map "$out/generics.csv" \
    --multiplier 2 || fail "run with generic map failed"
grep -q '^311-10.0KCRCT-ND,R1,2$' "$out/b/digikey_upload.csv" \
  || fail "generic map not applied / multiplier wrong"
grep -q '^490-10698-1-ND,C1 C2,4$' "$out/b/digikey_upload.csv" \
  || fail "multiplier not applied"

# 3. --strict fails while parts lack a DigiKey number
if run "$here/sample.kicad_pcb" --out-dir "$out/c" --strict; then
  fail "--strict should exit non-zero"
fi

# 4. schematic source
cat >"$out/sample.kicad_sch" <<'SCH'
(kicad_sch
	(version 20231120)
	(generator "eeschema")
	(uuid "00000000-0000-0000-0000-0000000000ff")
	(paper "A4")
	(lib_symbols)
	(symbol
		(lib_id "NiksKiCadLibrary:R_0603")
		(at 10 10 0)
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "00000000-0000-0000-0000-0000000000a1")
		(property "Reference" "R5" (at 0 0 0))
		(property "Value" "10k" (at 0 0 0))
		(property "Footprint" "NiksKiCadLibrary:R_0603" (at 0 0 0))
		(property "Digi-Key PN" "311-10.0KCRCT-ND" (at 0 0 0))
	)
	(symbol
		(lib_id "power:+5V")
		(at 20 20 0)
		(unit 1)
		(exclude_from_sim yes)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "00000000-0000-0000-0000-0000000000a2")
		(property "Reference" "#PWR01" (at 0 0 0))
		(property "Value" "+5V" (at 0 0 0))
	)
)
SCH
run "$out/sample.kicad_sch" --source sch --out-dir "$out/d" || fail "sch run failed"
grep -q '^311-10.0KCRCT-ND,R5,1$' "$out/d/digikey_upload.csv" \
  || fail "schematic part / Digi-Key PN alias not found"

echo "PASS: digikey_order.py"