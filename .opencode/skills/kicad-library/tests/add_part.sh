#!/usr/bin/env bash
# Self-test for tools/add_part.py against generated fixtures in a temp dir.
# Checks that:
#   - a vendor part is added with Reference/Footprint/MPN/DigiKey/Origin
#   - the footprint and 3D model land in the right library folders
#   - a duplicate symbol is rejected with a non-zero exit
#   - an empty source dir fails before creating library folders
#   - a malformed footprint fails with a non-zero exit (error on stderr)
#   - a KiCad 4/5 `(module ...)` footprint is converted (arc, pads, %R, model)
#   - a legacy footprint with an unknown construct is skipped with a `!` note
#   - footprint files sharing one internal name do not overwrite each other
#   - a symbol whose Footprint is not in the library is reported
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tool="$here/../tools/add_part.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }

# ---- fixtures ------------------------------------------------------------
src="$tmp/src"
mkdir -p "$src"

cat >"$src/TEST_IND.kicad_sym" <<'EOF'
(kicad_symbol_lib
	(version 20241209)
	(generator "kicad_symbol_editor")
	(symbol "TEST-IND-0603"
		(property "Reference" "U" (at 0 0 0))
		(property "Value" "TEST-IND-0603" (at 0 5.08 0))
		(property "Footprint" "" (at 0 0 0))
		(property "Datasheet" "" (at 0 0 0))
		(symbol "TEST-IND-0603_1_1"
			(rectangle (start -1.27 1.27) (end 1.27 -1.27))
		)
	)
)
EOF

cat >"$src/TEST_FOOTPRINT.kicad_mod" <<'EOF'
(footprint "TEST_FOOTPRINT"
	(version 20241229)
	(generator "pcbnew")
	(attr smd)
	(fp_text reference "REF**" (at 0 0 0))
	(fp_text value "TEST_FOOTPRINT" (at 0 0 0))
	(pad "1" smd rect (at -0.8 0) (size 0.8 1.2))
	(pad "2" smd rect (at 0.8 0) (size 0.8 1.2))
	(embedded_fonts no)
)
EOF

echo "dummy step" >"$src/TEST_FOOTPRINT.step"

lib="$tmp/lib"
mkdir -p "$lib"

run() {
  python3 "$tool" --lib "$lib" --designator L \
    --mpn TEST-IND-0603-3R3M --mfr TestMfr \
    --digikey 123-TEST-ND --origin CN \
    --sym-lib-table "$tmp/no-such-table" \
    "$@" "$src" >"$tmp/run.log" 2>&1
}

# ---- 1. clean add --------------------------------------------------------
run || { cat "$tmp/run.log"; fail "clean add failed"; }
sym="$lib/L/L.kicad_sym"
[ -f "$sym" ] || fail "symbol library not created"
grep -q '"TEST-IND-0603"' "$sym" || fail "symbol missing from library"
grep -q '(property "Reference" "L"' "$sym" || fail "Reference not set to designator"
grep -q 'NiksKiCadLibrary:TEST_FOOTPRINT' "$sym" || fail "Footprint property not set"
grep -q '(property "MPN" "TEST-IND-0603-3R3M"' "$sym" || fail "MPN not set"
grep -q '(property "DigiKey" "123-TEST-ND"' "$sym" || fail "DigiKey not set"
grep -q '(property "Origin" "CN"' "$sym" || fail "Origin not set"
grep -q '(property "MFG" "TestMfr"' "$sym" || fail "MFG not set"
[ -f "$lib/NiksKiCadLibrary.pretty/TEST_FOOTPRINT.kicad_mod" ] \
  || fail "footprint not written"
grep -q '(model "TEST_FOOTPRINT.step"' \
  "$lib/NiksKiCadLibrary.pretty/TEST_FOOTPRINT.kicad_mod" \
  || fail "model block not attached to footprint"
[ -f "$lib/NiksKiCadLibrary.3dshapes/TEST_FOOTPRINT.step" ] \
  || fail "3D model not copied"

# ---- 2. duplicate symbol rejected ---------------------------------------
set +e
run
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "duplicate symbol should exit non-zero"
grep -q "already exists" "$tmp/run.log" || fail "duplicate not reported"

# ---- 3. empty source dir fails before creating folders -------------------
empty="$tmp/empty"
mkdir -p "$empty"
lib2="$tmp/lib2"
mkdir -p "$lib2"
set +e
python3 "$tool" --lib "$lib2" --designator L \
  --sym-lib-table "$tmp/no-such-table" "$empty" >"$tmp/empty.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "empty source should exit 1"
[ ! -e "$lib2/NiksKiCadLibrary.pretty" ] \
  || fail "pretty dir created despite failed validation"

# ---- 4. malformed footprint fails loudly --------------------------------
bad="$tmp/bad"
mkdir -p "$bad"
echo "not a footprint" >"$bad/broken.kicad_mod"
set +e
python3 "$tool" --lib "$lib" --designator L \
  --sym-lib-table "$tmp/no-such-table" "$bad" >"$tmp/bad.log" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "malformed footprint should exit 1"
grep -q "^ERROR: broken.kicad_mod" "$tmp/bad.log" \
  || fail "footprint error not on stderr in ERROR form"

# ---- 5. legacy (KiCad 4/5) module footprint converted -------------------
legacy="$tmp/legacy"
mkdir -p "$legacy"
cat >"$legacy/LEGACY_TEST.kicad_mod" <<'EOF'
(module LEGACY_TEST (layer F.Cu) (tedit 5A02FF57) (tstamp 5A030000)
  (at 0 0)
  (descr "legacy test footprint")
  (tags "test")
  (attr smd)
  (fp_text reference %R (at 0 -3.5) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
  (fp_text value LEGACY_TEST (at 0 3.5) (layer F.SilkS) hide (effects (font (size 1 1) (thickness 0.15))))
  (fp_line (start -1 -1) (end 1 -1) (layer F.SilkS) (width 0.12))
  (fp_arc (start 0 0) (end 1 0) (angle 90) (layer F.SilkS) (width 0.12))
  (pad 1 smd rect (at -0.9 0) (size 0.9 1.2) (layers F.Cu F.Paste F.Mask))
  (pad 2 smd rect (at 0.9 0) (size 0.9 1.2) (layers F.Cu F.Paste F.Mask))
  (model ${KISYS3DMOD}/LEGACY_TEST.step (at (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
)
EOF
echo "dummy step" >"$legacy/LEGACY_TEST.step"
lib5="$tmp/lib5"
mkdir -p "$lib5"
python3 "$tool" --lib "$lib5" --designator U \
  --sym-lib-table "$tmp/no-such-table" "$legacy" >"$tmp/legacy.log" 2>&1 \
  || { cat "$tmp/legacy.log"; fail "legacy footprint conversion failed"; }
legacy_fp="$lib5/NiksKiCadLibrary.pretty/LEGACY_TEST.kicad_mod"
[ -f "$legacy_fp" ] || fail "legacy footprint not written"
grep -q '^(footprint "LEGACY_TEST"' "$legacy_fp" || fail "legacy header not modernised"
grep -q '(fp_arc (start 1.000000 0.000000) (mid 0.707107 0.707107) (end 0.000000 1.000000)' \
  "$legacy_fp" || fail "legacy fp_arc not converted to three points"
grep -q '(pad "1" smd rect' "$legacy_fp" || fail "legacy pad number not quoted"
grep -q '(fp_text reference "REF\*\*"' "$legacy_fp" || fail "%R not replaced by REF**"
grep -q '(layer "F.Fab")' "$legacy_fp" || fail "value text not moved to F.Fab"
grep -q '(offset (xyz 0 0 0))' "$legacy_fp" || fail "legacy model offset not converted"
! grep -q '^\s*(at 0 0)$' "$legacy_fp" || fail "module placement kept in footprint"

# ---- 6. unconvertible legacy footprint skipped with a note --------------
mixed="$tmp/mixed"
mkdir -p "$mixed"
cat >"$mixed/BAD_LEGACY.kicad_mod" <<'EOF'
(module BAD_LEGACY (layer F.Cu)
  (bogus_construct 1)
)
EOF
cat >"$mixed/GOOD.kicad_mod" <<'EOF'
(footprint "GOOD"
	(version 20241229)
	(generator "pcbnew")
	(pad "1" smd rect (at 0 0) (size 0.8 0.8))
)
EOF
lib6="$tmp/lib6"
mkdir -p "$lib6"
python3 "$tool" --lib "$lib6" --designator U \
  --sym-lib-table "$tmp/no-such-table" "$mixed" >"$tmp/mixed.log" 2>&1 \
  || { cat "$tmp/mixed.log"; fail "mixed source should still succeed"; }
grep -q '! BAD_LEGACY.kicad_mod: legacy (module ...).*manual conversion' "$tmp/mixed.log" \
  || fail "manual-conversion note missing"
[ ! -f "$lib6/NiksKiCadLibrary.pretty/BAD_LEGACY.kicad_mod" ] \
  || fail "unconvertible footprint should be skipped"
[ -f "$lib6/NiksKiCadLibrary.pretty/GOOD.kicad_mod" ] \
  || fail "convertible footprint in the same run was not written"

# ---- 7. duplicate internal footprint names get distinct file names ------
dupes="$tmp/dupes"
mkdir -p "$dupes"
for variant in A B; do
  cat >"$dupes/TEX-$variant.kicad_mod" <<'EOF'
(footprint "TEX"
	(version 20241229)
	(generator "pcbnew")
	(pad "1" smd rect (at 0 0) (size 0.8 0.8))
)
EOF
done
lib7="$tmp/lib7"
mkdir -p "$lib7"
python3 "$tool" --lib "$lib7" --designator U \
  --sym-lib-table "$tmp/no-such-table" "$dupes" >"$tmp/dupes.log" 2>&1 \
  || { cat "$tmp/dupes.log"; fail "duplicate-name run failed"; }
[ -f "$lib7/NiksKiCadLibrary.pretty/TEX-A.kicad_mod" ] || fail "TEX-A not written"
[ -f "$lib7/NiksKiCadLibrary.pretty/TEX-B.kicad_mod" ] || fail "TEX-B not written"
grep -q 'is used by several files' "$tmp/dupes.log" || fail "collision not reported"

# ---- 8. symbol footprint missing from the library is reported -----------
orphan="$tmp/orphan"
mkdir -p "$orphan"
cat >"$orphan/ORPHAN.kicad_sym" <<'EOF'
(kicad_symbol_lib
	(version 20241209)
	(generator "kicad_symbol_editor")
	(symbol "ORPHAN"
		(property "Reference" "U" (at 0 0 0))
		(property "Value" "ORPHAN" (at 0 5.08 0))
		(property "Footprint" "NiksKiCadLibrary:NOPE" (at 0 0 0))
	)
)
EOF
lib8="$tmp/lib8"
mkdir -p "$lib8"
python3 "$tool" --lib "$lib8" --designator U \
  --sym-lib-table "$tmp/no-such-table" "$orphan" >"$tmp/orphan.log" 2>&1 \
  || { cat "$tmp/orphan.log"; fail "orphan symbol run failed"; }
grep -q "symbol ORPHAN: footprint 'NOPE' is not in the library" "$tmp/orphan.log" \
  || fail "missing footprint not reported"

# ---- 9. usage -------------------------------------------------------------
python3 "$tool" --help >/dev/null || fail "--help failed"

echo "PASS: add_part.py"
