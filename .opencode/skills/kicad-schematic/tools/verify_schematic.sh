#!/bin/sh
# Verify a KiCad schematic: static lint, ERC, and golden netlist diff.
#
# Usage:
#   verify_schematic.sh [--update-golden] [--pdf] <schematic.kicad_sch> [golden]
#
#   --update-golden  (re)write the golden netlist from the current schematic
#   --pdf            also export a PDF next to the schematic
#   schematic        path to the .kicad_sch to verify (required)
#   golden           golden netlist file
#                    (default: <schematic-stem>.netlist.golden beside the schematic)
#
# Runs the skill's bundled utilities (lint_schematic.py and netlist_diff.py,
# resolved relative to this script) plus kicad-cli ERC and netlist export.
# Any relative golden path is resolved against the schematic's directory; if
# the golden is missing, use --update-golden to create it.
#
# Exit status: 0 all checks passed, 1 any check failed, 2 usage error.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

UPDATE_GOLDEN=0
DO_PDF=0
sch=
golden=

for a in "$@"; do
  case "$a" in
    --update-golden) UPDATE_GOLDEN=1 ;;
    --pdf) DO_PDF=1 ;;
    -*)
      echo "unknown option: $a" >&2
      exit 2
      ;;
    *)
      if [ -z "$sch" ]; then
        sch=$a
      elif [ -z "$golden" ]; then
        golden=$a
      else
        echo "too many positional arguments" >&2
        exit 2
      fi
      ;;
  esac
done

if [ -z "$sch" ]; then
  echo "usage: $0 [--update-golden] [--pdf] <schematic.kicad_sch> [golden]" >&2
  exit 2
fi

[ -f "$sch" ] || { echo "FAIL: schematic not found: $sch" >&2; exit 2; }
SCH_DIR=$(CDPATH= cd -- "$(dirname -- "$sch")" && pwd)
SCH_BASE=$(basename "$sch")
SCH_STEM=${SCH_BASE%.kicad_sch}

# Resolve the golden path against the schematic directory.
if [ -z "$golden" ]; then
  golden="$(dirname "$sch")/$SCH_STEM.netlist.golden"
else
  case "$golden" in
    /*) : ;;
    *) golden="$SCH_DIR/$golden" ;;
  esac
fi

stage() { printf '\n== %s\n' "$1"; }

stage "static lint"
python3 "$HERE/lint_schematic.py" "$sch" || { echo "FAIL: lint"; exit 1; }

stage "ERC"
rm -f erc.rpt
if ! kicad-cli sch erc --output erc.rpt --exit-code-violations "$sch" >/dev/null 2>&1; then
  echo "FAIL: kicad-cli erc reported violations"
  grep -E "ERC messages|Error|Warning" erc.rpt 2>/dev/null
  exit 1
fi
sum=$(grep -E 'ERC messages' erc.rpt || true)
echo "  $sum"
if ! printf '%s' "$sum" | grep -qE 'Errors 0[[:space:]]+Warnings 0'; then
  echo "FAIL: ERC error/warning count mismatch"
  exit 1
fi

stage "golden netlist diff"
net="$SCH_DIR/_verify.netlist.xml"
rm -f "$net"
kicad-cli sch export netlist --format kicadxml --output "$net" "$sch" >/dev/null 2>&1 \
  || { echo "FAIL: netlist export"; exit 1; }
if [ "$UPDATE_GOLDEN" -eq 1 ]; then
  python3 "$HERE/netlist_diff.py" --update "$net" "$golden" || { rm -f "$net"; exit 1; }
else
  if [ ! -f "$golden" ]; then
    echo "FAIL: golden netlist missing: $golden (rerun with --update-golden)" >&2
    rm -f "$net"
    exit 1
  fi
  python3 "$HERE/netlist_diff.py" "$net" "$golden" || { rm -f "$net"; exit 1; }
fi

if [ "$DO_PDF" -eq 1 ]; then
  stage "PDF export"
  pdf="$SCH_DIR/$SCH_STEM.pdf"
  rm -f "$pdf"
  kicad-cli sch export pdf --output "$pdf" "$sch" >/dev/null 2>&1 \
    && echo "  PDF written to $pdf" \
    || { echo "FAIL: pdf export"; rm -f "$net"; exit 1; }
fi

rm -f "$net"
printf '\nALL CHECKS PASSED\n'