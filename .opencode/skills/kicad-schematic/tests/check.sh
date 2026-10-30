#!/usr/bin/env bash
# Self-test for the tools in this skill's tools/ directory:
#   lint_schematic.py, netlist_diff.py, schematic_ir.py, schematic_emitter.py,
#   verify_schematic.sh (usage paths only - ERC needs kicad-cli)
# Exits non-zero if any expectation fails.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tools="$here/../tools"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }

run_rc() {  # run_rc <expected-rc> <cmd...>; captures output in $tmp/last.log
  local want="$1"; shift
  local rc=0
  set +e
  "$@" >"$tmp/last.log" 2>&1
  rc=$?
  set -e
  [ "$rc" -eq "$want" ] || { cat "$tmp/last.log"; fail "expected rc=$want, got rc=$rc: $*"; }
}

# ---- lint_schematic.py -----------------------------------------------------
cat >"$tmp/clean.kicad_sch" <<'EOF'
(kicad_sch
	(version 20231120)
	(generator "eeschema")
	(uuid "00000000-0000-0000-0000-000000000001")
	(paper "A4")
	(lib_symbols)
	(sheet_instances (path "/" (page "1")))
)
EOF
run_rc 0 python3 "$tools/lint_schematic.py" "$tmp/clean.kicad_sch"
grep -q "0 errors, 0 warnings" "$tmp/last.log" || fail "clean lint report wrong"

cat >"$tmp/broken.kicad_sch" <<'EOF'
(kicad_sch
	(version 20231120)
	(generator "eeschema")
	(uuid "00000000-0000-0000-0000-000000000002")
	(paper "A4")
	(lib_symbols)
	(wire (pts (xy 0 0) (xy 2.54 2.54)) (stroke (width 0) (type default)) (uuid "00000000-0000-0000-0000-000000000003"))
	(sheet_instances (path "/" (page "1")))
)
EOF
run_rc 1 python3 "$tools/lint_schematic.py" "$tmp/broken.kicad_sch"
grep -q "not axis-aligned" "$tmp/last.log" || fail "diagonal wire not reported"

run_rc 1 python3 "$tools/lint_schematic.py" "$tmp/missing.kicad_sch"
grep -q "ERROR: cannot read" "$tmp/last.log" || fail "missing file not reported on stderr"
run_rc 0 python3 "$tools/lint_schematic.py" --help

# ---- netlist_diff.py -------------------------------------------------------
cat >"$tmp/net.xml" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<export version="E">
  <nets>
    <net code="1" name="GND">
      <node ref="R1" pin="2"/>
      <node ref="C1" pin="2"/>
    </net>
    <net code="2" name="+5V">
      <node ref="R1" pin="1"/>
    </net>
  </nets>
</export>
EOF
cat >"$tmp/golden" <<'EOF'
# golden netlist (normalized: net name -> sorted ref:pin)
+5V	R1:1
GND	C1:2,R1:2
EOF
run_rc 0 python3 "$tools/netlist_diff.py" "$tmp/net.xml" "$tmp/golden"

cat >"$tmp/golden_short" <<'EOF'
# golden netlist (normalized: net name -> sorted ref:pin)
GND	C1:2,R1:2
EOF
run_rc 1 python3 "$tools/netlist_diff.py" "$tmp/net.xml" "$tmp/golden_short"
grep -q "net added in current" "$tmp/last.log" || fail "diff detail missing"

run_rc 0 python3 "$tools/netlist_diff.py" --update "$tmp/net.xml" "$tmp/golden_short"
grep -q "Golden netlist updated" "$tmp/last.log" || fail "update message missing"
run_rc 0 python3 "$tools/netlist_diff.py" "$tmp/net.xml" "$tmp/golden_short"

run_rc 2 python3 "$tools/netlist_diff.py" "$tmp/missing.xml" "$tmp/golden"
grep -q "ERROR: cannot read netlist" "$tmp/last.log" || fail "missing netlist error not on stderr"
run_rc 0 python3 "$tools/netlist_diff.py" --help

# ---- schematic_ir.py / schematic_emitter.py --------------------------------
python3 - "$tools" <<'PYEOF'
import importlib.util
import pathlib
import sys
import math

tools = pathlib.Path(sys.argv[1])

def load(name):
    spec = importlib.util.spec_from_file_location(name, tools / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

ir = load("schematic_ir")
em = load("schematic_emitter")

def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)

# --- ir: happy path ---
passive = ir.Pin(num="1", name="1", kind="passive", offset=(0.0, 0.0))
res = ir.Component(lib_id="Device:R", symbol_name="R",
                   pins={"1": passive, "2": ir.Pin("2", "2", "passive", (0.0, 0.0))})
gnd = ir.Component(lib_id="power:GND", symbol_name="GND",
                   pins={"1": ir.Pin("1", "1", "power_in", (0.0, 0.0))})

sheet = ir.Sheet()
sheet.instances.append(ir.Instance(ref="R1", lib_id="Device:R", value="10k"))
sheet.instances.append(ir.Instance(ref="R2", lib_id="Device:R", value="10k"))
ir.add_net(sheet, "N$1", ("R1", "1"), ("R2", "1"))
design = ir.Design(sheets=[sheet])

errs = ir.check(design, {"Device:R": res})
if errs:
    fail(f"clean design reported errors: {errs}")
summary = ir.nets_summary(design)
if summary != {"N$1": ["R1:1", "R2:1"]}:
    fail(f"nets_summary wrong: {summary}")

# --- ir: power_in without power_out must fail ---
sheet.instances.append(ir.Instance(ref="PWR1", lib_id="power:GND", value="GND"))
ir.add_net(sheet, "GND", ("PWR1", "1"), ("R1", "2"))
errs = ir.check(design, {"Device:R": res, "power:GND": gnd})
if not any("power_in" in e for e in errs):
    fail(f"undriven power net not reported: {errs}")

# --- ir: duplicate reference must fail ---
dup = ir.Sheet()
dup.instances.append(ir.Instance(ref="R1", lib_id="Device:R", value="10k"))
dup.instances.append(ir.Instance(ref="R1", lib_id="Device:R", value="4k7"))
errs = ir.check(ir.Design(sheets=[dup]), {"Device:R": res})
if not any("duplicate reference" in e for e in errs):
    fail(f"duplicate reference not reported: {errs}")

# --- emitter: deterministic uuids + rotation helpers ---
if em.gen_uuid("x") != em.gen_uuid("x") or em.gen_uuid("x") == em.gen_uuid("y"):
    fail("gen_uuid not deterministic/unique")
def close(a, b):
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9
if not close(em.rot(1.0, 0.0, 90), (0.0, 1.0)):
    fail(f"rot wrong: {em.rot(1.0, 0.0, 90)}")
if not close(em.placed_offset(1.0, 0.0, 90), (0.0, -1.0)):
    fail(f"placed_offset wrong: {em.placed_offset(1.0, 0.0, 90)}")
PYEOF

# round trip lives in a separate block (needs fixture files on disk)
python3 - "$tools" "$tmp" <<'PYEOF'
import importlib.util
import pathlib
import subprocess
import sys

tools = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(sys.argv[2])

def load(name):
    spec = importlib.util.spec_from_file_location(name, tools / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

ir = load("schematic_ir")
em = load("schematic_emitter")

def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)

template = tmp / "R.sym"
template.write_text('''(symbol "R"
	(property "Reference" "R" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))
	(property "Value" "R" (at 0 2.54 0) (effects (font (size 1.27 1.27))))
	(symbol "R_1_1"
		(pin passive line (at 0 0 0) (length 0)
			(name "~" (effects (font (size 1.27 1.27))))
			(number "1" (effects (font (size 1.27 1.27)))))
		(pin passive line (at 0 2.54 0) (length 0)
			(name "~" (effects (font (size 1.27 1.27))))
			(number "2" (effects (font (size 1.27 1.27)))))
	)
)
''')

parts = ir.load_parts(str(tmp), [("R", "R.sym", "Device:R")])

sheet = ir.Sheet()
sheet.instances.append(ir.Instance(ref="R1", lib_id="Device:R", value="10k"))
sheet.instances.append(ir.Instance(ref="R2", lib_id="Device:R", value="10k"))
ir.add_net(sheet, "NET1", ("R1", "1"), ("R2", "1"))
design = ir.Design(sheets=[sheet])

opts = {
    "seed_prefix": "selftest",
    "project": "selftest",
    "title_block": {"title": "t", "date": "d", "rev": "r", "company": "c"},
    "layout": {"R1": ((2.54, 2.54), 0), "R2": ((7.62, 2.54), 0)},
    "wires": [((2.54, 2.54), (7.62, 2.54)), ((2.54, 0.0), (7.62, 0.0))],
    "junctions": [],
}
text = em.build(design, parts, [("R", "R.sym", "Device:R")], str(tmp), opts)
if '(lib_id "Device:R")' not in text:
    fail("emitted file missing lib_id")
if '(property "Reference" "R1"' not in text:
    fail("emitted file missing reference")

out = tmp / "emitted.kicad_sch"
out.write_text(text)

# the emitted schematic must pass the skill's own lint
res = subprocess.run([sys.executable, str(tools / "lint_schematic.py"), str(out)],
                     capture_output=True, text=True)
if res.returncode != 0:
    fail(f"emitted schematic fails lint (rc={res.returncode}):\n{res.stdout}{res.stderr}")
print("emitted->lint round trip OK")
PYEOF

# ---- verify_schematic.sh (usage paths; ERC needs kicad-cli) ----------------
run_rc 0 "$tools/verify_schematic.sh" --help
grep -q "Usage:" "$tmp/last.log" || fail "verify --help missing usage"
run_rc 2 "$tools/verify_schematic.sh"
run_rc 2 "$tools/verify_schematic.sh" --bogus
run_rc 2 "$tools/verify_schematic.sh" "$tmp/missing.kicad_sch"

echo "PASS: kicad-schematic tools"
