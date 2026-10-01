#!/usr/bin/env python3
"""
Check whether components carry the parameters Aisler.net needs for part
selection, plus the DigiKey order number and country-of-origin fields.

Aisler.net reads the MPN from the symbol/footprint property named MPN, Mpn,
mpn or AISLER_MPN (a 100% exact match) and falls back to the Value field. For
generic SMD resistors/capacitors/inductors and signal LEDs Aisler also accepts
a Smart Match string ("100R 0603 1% 100mW"). The `DigiKey` property should hold
the distributor order number (e.g. "535-13583-1-ND") and the `Origin` property
the ISO 3166 country code (e.g. "CN", "MY", "TH", "US").

This tool scans the KiCad s-expression files of the components actually used
in a design:
    *.kicad_sch   placed schematic symbols -> (property "Reference" "R1" ...)
    *.kicad_pcb   pcb footprints           -> (fp_text reference ...) + (property ...)

Library source symbols (*.kicad_sym) are intentionally NOT scanned — this
skill verifies the components used in the schematic/PCB, which is what Aisler
reads on upload.

Usage:
    check_component_params.py FILE... [DIR...]
    check_component_params.py schematic.kicad_sch
    check_component_params.py --ignore-origin board.kicad_sch board.kicad_pcb

Arguments may be files or directories; directories are searched recursively for
*.kicad_sch and *.kicad_pcb.

Output is one line per component plus a summary. The exit status is the number
of components that failed a required check (0 = everything is in order).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MPN_ALIASES = ("MPN", "Mpn", "mpn", "AISLER_MPN")
GENERIC_PREFIXES = ("LED", "R", "C", "L")
EXTS = (".kicad_sch", ".kicad_pcb")

ISO3166_2 = re.compile(r"^[A-Za-z]{2}$")
DIGIKEY_RE = re.compile(r"^[0-9A-Za-z.-]+-ND$")
DES_RE = re.compile(r"^([A-Za-z]+)")
PACKAGE_RE = re.compile(r"^[0-9]{4}$")

STATUS_WIDTH = 16


# ---------------------------------------------------------------- s-expr parser

def tokenize(text: str) -> list:
    """Split s-expression text into '(', ')' and ('str'|'atom', value) items."""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "()":
            tokens.append(c)
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            tokens.append(("str", "".join(buf)))
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            tokens.append(("atom", text[i:j]))
            i = j
    return tokens


class _Parser:
    def __init__(self, tokens: list):
        self.tokens = tokens
        self.i = 0

    def _list(self) -> list:
        out = []
        while self.i < len(self.tokens):
            t = self.tokens[self.i]
            if t == ")":
                self.i += 1
                return out
            if t == "(":
                self.i += 1
                out.append(self._list())
            else:
                out.append(t)
                self.i += 1
        return out

    def parse(self) -> list:
        out = []
        while self.i < len(self.tokens):
            t = self.tokens[self.i]
            if t == "(":
                self.i += 1
                out.append(self._list())
            else:
                self.i += 1
        return out


def parse_file(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"  ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return []
    return _Parser(tokenize(text)).parse()


def is_token(node, kind: str, value: str | None = None) -> bool:
    return (
        isinstance(node, tuple)
        and node[0] == kind
        and (value is None or node[1] == value)
    )


def head_of(node) -> str | None:
    if isinstance(node, list) and len(node) and is_token(node[0], "atom"):
        return node[0][1]
    return None


def property_map(node: list) -> dict:
    """Extract (property "Name" "Value" ...) pairs from a symbol/footprint."""
    out = {}
    for child in node:
        if (
            isinstance(child, list)
            and len(child) >= 3
            and head_of(child) == "property"
            and is_token(child[1], "str")
            and is_token(child[2], "str")
        ):
            out.setdefault(child[1][1], child[2][1])
    return out


def fp_text(node: list, kind: str) -> str:
    for child in node:
        if (
            isinstance(child, list)
            and len(child) >= 3
            and head_of(child) == "fp_text"
            and (is_token(child[1], "str", kind) or is_token(child[1], "atom", kind))
            and is_token(child[2], "str")
        ):
            return child[2][1]
    return ""


# ---------------------------------------------------------------- extraction

def component_nodes(root: list, suffix: str) -> list:
    """Placed symbol (sch) or footprint (pcb) nodes at component level."""
    head = "symbol" if suffix == ".kicad_sch" else "footprint"
    nodes = []
    for item in root:
        h = head_of(item)
        if h == head:
            nodes.append(item)
        elif h in ("kicad_sch", "kicad_pcb"):
            nodes.extend(component_nodes(item, suffix))
    return nodes


def extract_components(path: Path) -> list[dict]:
    root = parse_file(path)
    comps = []
    for node in component_nodes(root, path.suffix):
        props = property_map(node)
        if path.suffix == ".kicad_sch":
            ref = props.get("Reference", "")
            if ref:
                comps.append(_comp(path, "schematic", ref, ref, props.get("Value", ""), props))
        else:  # .kicad_pcb
            ref = fp_text(node, "reference") or props.get("Reference", "")
            comps.append(_comp(path, "pcb", ref or fp_text(node, "value"), ref, props.get("Value", "") or fp_text(node, "value"), props))
    return comps


def _comp(path: Path, kind: str, uid: str, ref: str, value: str, props: dict) -> dict:
    return {
        "file": str(path),
        "kind": kind,
        "id": uid or ref or "(unnamed)",
        "ref": ref,
        "value": value,
        "props": props,
    }


def prefix_of(ref: str) -> str:
    m = DES_RE.match(ref or "")
    return m.group(1) if m else ""


def is_generic(ref: str) -> bool:
    return prefix_of(ref) in GENERIC_PREFIXES


def looks_smart_match(value: str) -> bool:
    tokens = (value or "").split()
    return len(tokens) >= 2 and any(PACKAGE_RE.fullmatch(t) for t in tokens)


# ---------------------------------------------------------------- checks

def check_component(comp: dict) -> tuple[dict, dict, dict]:
    props = comp["props"]
    value = comp["value"] or ""
    generic = is_generic(comp["ref"])

    mpn = next((props[k].strip() for k in MPN_ALIASES if props.get(k, "").strip()), "")
    if mpn:
        aisler = {"status": "ok", "detail": f'MPN "{mpn}"'}
    elif generic and looks_smart_match(value):
        aisler = {"status": "ok", "detail": f'Smart Match "{value}"'}
    elif value:
        aisler = {"status": "warn", "detail": f'Value "{value}" — add exact MPN or Smart Match'}
    else:
        aisler = {"status": "fail", "detail": "no MPN and no Value"}

    dk = props.get("DigiKey", "").strip()
    if dk:
        digikey = {"status": "ok" if DIGIKEY_RE.match(dk) else "warn", "detail": dk}
    else:
        digikey = {"status": "fail", "detail": "missing"}

    origin = props.get("Origin", "").strip()
    if ISO3166_2.match(origin):
        origin_s = {"status": "ok" if origin.isupper() else "warn", "detail": origin}
    elif origin:
        origin_s = {"status": "warn", "detail": f'"{origin}" not an ISO 3166 country code'}
    else:
        origin_s = {"status": "fail", "detail": "missing"}

    return aisler, digikey, origin_s


def fmt_status(status: str) -> str:
    return {"ok": "OK", "warn": "WARN", "fail": "FAIL", "n/a": "n/a"}[status]


# ---------------------------------------------------------------- main

def collect_files(paths: list[str]) -> list[Path]:
    files = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for found in sorted(p.rglob("*")):
                if found.suffix in EXTS:
                    files.append(found)
        elif p.suffix in EXTS:
            files.append(p)
        else:
            print(f"  ERROR: not a supported KiCad file ({EXTS}): {p}", file=sys.stderr)
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check components for Aisler.net selection parameters "
            "(MPN / Smart Match), DigiKey order number, and country of origin."
        ),
        epilog="""\
Examples:

  Check a whole board project:
    check_component_params.py my-board.kicad_sch my-board.kicad_pcb

  Check a directory, skipping the DigiKey requirement:
    check_component_params.py --ignore-digikey ./

Exit status = number of failing components (0 = all pass).
Only components used in the design are checked (schematic/PCB); library
symbol files (*.kicad_sym) are not scanned.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to scan.")
    parser.add_argument("--ignore-digikey", action="store_true", help="Do not require the DigiKey property.")
    parser.add_argument("--ignore-origin", action="store_true", help="Do not require the Origin property.")
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("ERROR: no .kicad_sch / .kicad_pcb files found.", file=sys.stderr)
        return 1

    failures = 0
    counts = {"ok": 0, "warn": 0, "fail": 0}

    for path in files:
        comps = extract_components(path)
        if not comps:
            continue
        print(f"{path}:")
        for comp in comps:
            aisler, dk, origin = check_component(comp)
            if args.ignore_digikey:
                dk["status"] = "n/a"
            if args.ignore_origin:
                origin_s = {"status": "n/a", "detail": "ignored"}
            else:
                origin_s = origin
            failed = any(
                s["status"] == "fail"
                for s in (aisler, dk, origin_s)
            )
            for s in (aisler, dk, origin_s):
                counts[s["status"]] = counts.get(s["status"], 0) + 1
            if failed:
                failures += 1
            print(
                f"  {comp['id']:<16} {comp['kind']:<11} "
                f"Aisler {fmt_status(aisler['status']):<5} ({aisler['detail'][:38]})  "
                f"DigiKey {fmt_status(dk['status']):<5} ({dk['detail'][:22]})  "
                f"Origin {fmt_status(origin_s['status']):<5} ({origin_s['detail'][:22]})"
            )
        print()

    print("Summary:")
    print(f"  ok:   {counts.get('ok', 0)}")
    print(f"  warn: {counts.get('warn', 0)}")
    print(f"  fail: {counts.get('fail', 0)}")
    print(f"  failing components: {failures}")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())