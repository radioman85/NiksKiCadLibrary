#!/usr/bin/env python3
"""
Build a Digi-Key order list from a KiCad design, ready for upload.

The tool reads the components actually used in a design (PCB footprints or
placed schematic symbols), picks up the distributor reference stored in the
`DigiKey` property (any of the usual spellings) and groups equal parts into one
order line with a summed quantity.

Typical source of truth is the PCB, because Aisler/Digi-Key receive the board
file; `--source sch` works on a schematic (all sheets of the hierarchy) when a
board has not been exported yet.

Outputs (written into --out-dir):

    <prefix>_upload.csv   Digi-Key list-upload format
                          ("Digi-Key Part Number", "Customer Reference",
                          "Quantity", optionally "Manufacturer" and
                          "Manufacturer Part Number")
    <prefix>_list.txt     one Digi-Key part number per line (cart entry)
    <prefix>_bom.csv      full internal BOM incl. Value/Footprint/MPN/Origin
    <prefix>_missing.csv  parts that have no Digi-Key part number yet

Generic R/C/L parts usually carry a Smart Match value instead of a
manufacturer part number; `--generic-map FILE` fills them in from a small CSV
with the columns Value,Footprint,DigiKey so the upload stays complete.

Usage:
    digikey_order.py board.kicad_pcb
    digikey_order.py --source sch --out-dir order/ train_controller.kicad_sch
    digikey_order.py board.kicad_pcb --generic-map generics.csv --multiplier 2

Exit status is 0 unless an input cannot be read; `--strict` additionally makes
components without a Digi-Key part number fail with status 1.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from pathlib import Path

# Property names accepted as the Digi-Key order number.
DK_ALIASES = (
    "DigiKey",
    "Digikey",
    "Digi-Key",
    "Digi-Key PN",
    "Digi-Key Part Number",
    "Digikey Part Number",
    "DigiKey Part Number",
    "DigiKey_Part_Number",
    "Digi-Key_PN",
    "DK PN",
    "DK",
)
# Aisler/other MPN spellings (used for the report columns only).
MPN_ALIASES = (
    "MPN",
    "Mpn",
    "mpn",
    "AISLER_MPN",
    "Manufacturer_Part_Number",
    "Manufacturer Part Number",
)
MFG_ALIASES = ("MFG", "Manufacturer", "Manufacturer_Name", "Manufacturer Name")
ORIGIN_ALIASES = ("Origin", "Country of Origin", "CountryOfOrigin")
GENERIC_PREFIXES = ("R", "C", "L", "LED")

DES_RE = re.compile(r"^([A-Za-z]+)")
DK_RE = re.compile(r"^[A-Za-z0-9.,/_+\-]+\-(ND|F-ND|CT-ND|TR-ND|IR-ND)$")


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
    text = path.read_text(encoding="utf-8", errors="replace")
    return _Parser(tokenize(text)).parse()


def head_of(node) -> str | None:
    if isinstance(node, list) and node and is_token(node[0], "atom"):
        return node[0][1]
    return None


def is_token(node, kind: str, value: str | None = None) -> bool:
    return (
        isinstance(node, tuple)
        and node[0] == kind
        and (value is None or node[1] == value)
    )


def property_map(node: list) -> dict:
    """Extract the (property "Name" "Value" ...) pairs of a symbol/footprint."""
    out = {}
    for child in node:
        if (
            isinstance(child, list)
            and len(child) >= 3
            and head_of(child) == "property"
            and is_token(child[1], "str")
            and is_token(child[2], "str")
        ):
            out.setdefault(child[1][1], child[2][1].strip())
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
            return child[2][1].strip()
    return ""


def node_flag(node: list, name: str) -> bool | None:
    """Value of a child flag such as (dnp no) / (attr smd exclude_from_bom)."""
    for child in node:
        if isinstance(child, list) and child and head_of(child) == name:
            for item in child[1:]:
                if is_token(item, "atom"):
                    return item[1] == "yes"
                if is_token(item, "str"):
                    return item[1].lower() in ("yes", "true", "1")
            return False
    return None


def node_attrs(node: list, name: str) -> set[str]:
    out = set()
    for child in node:
        if isinstance(child, list) and child and head_of(child) == name:
            for item in child[1:]:
                if is_token(item, "atom"):
                    out.add(item[1])
    return out


def iter_nodes(root, head: str) -> list:
    """All nodes with the given head, at any depth (footprints / symbols)."""
    out = []
    for item in root:
        if not isinstance(item, list):
            continue
        h = head_of(item)
        if h == head:
            out.append(item)
        elif h in ("kicad_sch", "kicad_pcb", "sheet"):
            out.extend(iter_nodes(item, head))
    return out


def first_str(node: list, head: str) -> str:
    for child in node:
        if (
            isinstance(child, list)
            and len(child) >= 2
            and head_of(child) == head
            and is_token(child[1], "str")
        ):
            return child[1][1]
    return ""


def pick(props: dict, aliases) -> str:
    for key in aliases:
        value = props.get(key, "").strip()
        if value and value not in ("~", "-"):
            return value
    return ""


# ---------------------------------------------------------------- extraction

class Component:
    __slots__ = ("ref", "value", "footprint", "mpn", "mfg", "dk", "origin",
                 "desc", "sheet", "generic")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot, ""))

    def key(self) -> tuple:
        return (
            self.dk,
            self.mpn,
            self.value,
            self.footprint.rsplit(":", 1)[-1],
            self.ref.rstrip("0123456789"),
        )


def is_placeholder(ref: str) -> bool:
    return not ref or ref.startswith("#") or ref.startswith("REF") or "*" in ref


def component_from_footprint(node: list, path: Path) -> Component:
    props = property_map(node)
    ref = props.get("Reference", "") or fp_text(node, "reference")
    name = node[1][1] if len(node) > 1 and is_token(node[1], "str") else ""
    return Component(
        ref=ref,
        value=props.get("Value", "") or fp_text(node, "value"),
        footprint=props.get("Footprint", "") or name,
        mpn=pick(props, MPN_ALIASES),
        mfg=pick(props, MFG_ALIASES),
        dk=pick(props, DK_ALIASES),
        origin=pick(props, ORIGIN_ALIASES),
        desc=props.get("Description", "") or props.get("Datasheet", ""),
        sheet=path.name,
    )


def component_from_symbol(node: list, path: Path) -> Component:
    props = property_map(node)
    ref = props.get("Reference", "")
    return Component(
        ref=ref,
        value=props.get("Value", ""),
        footprint=props.get("Footprint", ""),
        mpn=pick(props, MPN_ALIASES),
        mfg=pick(props, MFG_ALIASES),
        dk=pick(props, DK_ALIASES),
        origin=pick(props, ORIGIN_ALIASES),
        desc=props.get("Description", "") or props.get("Datasheet", ""),
        sheet=path.name,
    )


def schematic_files(root: Path) -> list[Path]:
    """Root schematic plus every sheet referenced by the hierarchy."""
    seen = {root.resolve()}
    queue = [root]
    files = []
    while queue:
        current = queue.pop(0)
        files.append(current)
        for node in iter_nodes(parse_file(current), "sheet"):
            props = property_map(node)
            name = props.get("Sheetfile") or first_str(node, "Sheetfile")
            if not name:
                continue
            child = current.parent / name
            if child.exists() and child.resolve() not in seen:
                seen.add(child.resolve())
                queue.append(child)
    return files


def extract(paths: list[Path], source: str, include_dnp: bool) -> tuple[list[Component], list[str]]:
    """Collect components plus a list of skipped (excluded) references."""
    comps: list[Component] = []
    skipped: list[str] = []

    if source == "auto":
        source = "pcb" if any(p.suffix == ".kicad_pcb" for p in paths) else "sch"
    targets: list[tuple[Path, str]] = [(p, source) for p in paths
                                       if p.suffix == f".kicad_{source}"]

    seen_refs: set[str] = set()
    for path, kind in targets:
        if kind == "pcb":
            for node in iter_nodes(parse_file(path), "footprint"):
                attrs = node_attrs(node, "attr")
                comp = component_from_footprint(node, path)
                if "exclude_from_bom" in attrs or "exclude_from_pos_files" in attrs:
                    skipped.append(f"{comp.ref} (excluded from BOM)")
                    continue
                if is_placeholder(comp.ref):
                    skipped.append(f"{comp.ref or comp.value} (no designator)")
                    continue
                if comp.ref in seen_refs:
                    continue
                seen_refs.add(comp.ref)
                comps.append(comp)
        else:
            for sch in schematic_files(path):
                for node in iter_nodes(parse_file(sch), "symbol"):
                    comp = component_from_symbol(node, sch)
                    lib_id = first_str(node, "lib_id")
                    if node_flag(node, "exclude_from_sim") or node_flag(node, "in_bom") is False:
                        skipped.append(f"{comp.ref or lib_id} (excluded from BOM/sim)")
                        continue
                    if node_flag(node, "dnp") and not include_dnp:
                        skipped.append(f"{comp.ref} (DNP)")
                        continue
                    if lib_id.startswith("power:") or is_placeholder(comp.ref):
                        skipped.append(f"{comp.ref or lib_id} (power symbol)")
                        continue
                    if not comp.footprint and not comp.dk:
                        skipped.append(f"{comp.ref or lib_id} (no footprint)")
                        continue
                    if comp.ref in seen_refs:
                        continue
                    seen_refs.add(comp.ref)
                    comps.append(comp)

    comps.sort(key=lambda c: (DES_RE.match(c.ref).group(0) if DES_RE.match(c.ref) else "",
                              c.ref))
    return comps, skipped


# ---------------------------------------------------------------- grouping

def load_generic_map(path: Path | None) -> dict:
    if path is None:
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dk = (row.get("DigiKey") or row.get("Digi-Key Part Number") or "").strip()
            value = (row.get("Value") or "").strip()
            fp = (row.get("Footprint") or "").strip().rsplit(":", 1)[-1]
            if dk:
                out[(value.lower(), fp.lower())] = dk
    return out


def apply_generic_map(comps: list[Component], gmap: dict) -> list[str]:
    """Fill in Digi-Key numbers for generic parts; return a log of what was set."""
    hits: dict[str, list[str]] = {}
    for comp in comps:
        if comp.dk:
            continue
        value = comp.value.split(" ")[0].lower()
        fp = comp.footprint.rsplit(":", 1)[-1].lower()
        dk = gmap.get((value, fp)) or gmap.get((value, ""))
        if dk:
            comp.dk = dk
            hits.setdefault(dk, []).append(comp.ref)
    return [
        f"  {len(refs):>3} parts -> {dk}  ({' '.join(sorted(refs))}) [generic map]"
        for dk, refs in sorted(hits.items())
    ]


def group(comps: list[Component]) -> tuple[list[dict], list[dict]]:
    """Split components into orderable lines (with DK number) and missing ones."""
    lines: dict[str, dict] = {}
    missing: dict[tuple, dict] = {}

    for comp in comps:
        if comp.dk:
            line = lines.get(comp.dk)
            if line is None:
                line = lines[comp.dk] = {
                    "dk": comp.dk,
                    "refs": [],
                    "qty": 0,
                    "mpn": comp.mpn,
                    "mfg": comp.mfg,
                    "origin": comp.origin,
                    "desc": comp.desc,
                }
            line["refs"].append(comp.ref)
            line["qty"] += 1
            for field, value in (("mpn", comp.mpn), ("mfg", comp.mfg),
                                 ("desc", comp.desc)):
                if not line[field] and value:
                    line[field] = value
            continue

        key = (comp.value, comp.footprint.rsplit(":", 1)[-1], comp.mpn)
        item = missing.get(key)
        if item is None:
            match = DES_RE.match(comp.ref)
            item = missing[key] = {
                "value": comp.value,
                "footprint": comp.footprint.rsplit(":", 1)[-1],
                "mpn": comp.mpn,
                "mfg": comp.mfg,
                "origin": comp.origin,
                "generic": bool(match) and match.group(1) in GENERIC_PREFIXES,
                "refs": [],
                "qty": 0,
            }
        item["refs"].append(comp.ref)
        item["qty"] += 1

    def first_ref(line: dict) -> str:
        return sorted(line["refs"])[0]

    def ref_key(line: dict) -> tuple[str, str]:
        ref = first_ref(line)
        match = DES_RE.match(ref)
        return (match.group(0) if match else "", ref)

    order = sorted(lines.values(), key=lambda l: ref_key(l))
    return order, [missing[k] for k in sorted(missing)]


# ---------------------------------------------------------------- output

def write_outputs(out_dir: Path, prefix: str, order: list[dict], missing: list[dict],
                  comps: list[Component], with_mpn: bool, multiplier: float) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    upload = out_dir / f"{prefix}_upload.csv"
    header = ["Digi-Key Part Number", "Customer Reference", "Quantity"]
    if with_mpn:
        header += ["Manufacturer", "Manufacturer Part Number"]
    with upload.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for line in order:
            qty = int(math.ceil(line["qty"] * multiplier))
            row = [line["dk"], " ".join(sorted(line["refs"])), qty]
            if with_mpn:
                row += [line["mfg"], line["mpn"]]
            writer.writerow(row)
    written.append(upload)

    lst = out_dir / f"{prefix}_list.txt"
    lst.write_text("\n".join(sorted({l["dk"] for l in order})) + "\n", encoding="utf-8")
    written.append(lst)

    bom = write_full_bom(out_dir, prefix, comps)
    written.append(bom)

    miss = out_dir / f"{prefix}_missing.csv"
    with miss.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["Reference", "Quantity", "Value", "Footprint",
                         "Manufacturer", "Manufacturer Part Number", "Origin"])
        for item in missing:
            writer.writerow([
                " ".join(sorted(item["refs"])),
                int(math.ceil(item["qty"] * multiplier)),
                item["value"], item["footprint"], item["mfg"], item["mpn"],
                item["origin"],
            ])
    written.append(miss)
    return written


def write_full_bom(out_dir: Path, prefix: str, comps: list[Component]) -> Path:
    path = out_dir / f"{prefix}_bom.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["Reference", "Value", "Footprint", "Manufacturer",
                         "Manufacturer Part Number", "Digi-Key Part Number",
                         "Origin", "Description", "Sheet"])
        for comp in comps:
            writer.writerow([comp.ref, comp.value, comp.footprint, comp.mfg,
                             comp.mpn, comp.dk, comp.origin, comp.desc, comp.sheet])
    return path


def missing_tag(item: dict) -> str:
    """Short hint on what is missing for one group of parts."""
    mpn = item["mpn"]
    if mpn:
        tokens = mpn.split()
        if len(tokens) >= 2 and any(t.isdigit() and len(t) == 4 for t in tokens):
            return "Smart Match value - pick a part, add MPN + DK#"
        return "MPN known - look up Digi-Key number"
    if item["generic"]:
        return "generic part - add MPN/Smart Match + DigiKey property"
    return "no MPN and no DigiKey property"


# ---------------------------------------------------------------- main

def in_hidden_dir(path: Path) -> bool:
    """True for files inside a backup/history directory such as .history or _restore_*."""
    return any(part.startswith((".", "_")) for part in path.parts[:-1])


def schematic_roots(files: list[Path]) -> list[Path]:
    """Top-level schematics only: drop files another sheet pulls in as a Sheetfile."""
    if len(files) < 2:
        return files
    referenced = set()
    for path in files:
        for node in iter_nodes(parse_file(path), "sheet"):
            name = property_map(node).get("Sheetfile") or first_str(node, "Sheetfile")
            if name:
                referenced.add((path.parent / name).resolve())
    roots = [f for f in files if f.resolve() not in referenced]
    return roots or files


def collect_files(paths: list[str], source: str) -> list[Path]:
    wanted = {".kicad_pcb"} if source == "pcb" else {".kicad_sch"}
    if source == "auto":
        wanted = {".kicad_pcb", ".kicad_sch"}
    files = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*")
                                if f.suffix in wanted and not in_hidden_dir(f)))
        elif p.suffix in wanted:
            files.append(p)
        else:
            print(f"  ERROR: not a KiCad {sorted(wanted)} file: {p}", file=sys.stderr)
    files = sorted(set(files))
    return files if source == "pcb" else schematic_roots(files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract the components of a KiCad design and write a Digi-Key "
            "order list (upload CSV, plain part-number list, full BOM, and a "
            "report of parts without a Digi-Key number)."
        ),
        epilog="""\
Examples:

  Order list from the board (preferred source, what Aisler/Digi-Key receive):
    digikey_order.py board.kicad_pcb --out-dir order/

  Order list from the schematic, all sheets included:
    digikey_order.py --source sch train_controller.kicad_sch

  Fill in generic R/C/L parts from a small CSV (Value,Footprint,DigiKey),
  include manufacturer columns, and double the quantities for 2 boards:
    digikey_order.py board.kicad_pcb --generic-map generics.csv \\
        --with-mpn --multiplier 2

The PCB is preferred automatically; use --source sch when the design has not
been exported to a board yet (or when you want the schematic's part numbers).
Parts flagged "exclude from BOM", DNP symbols and power symbols are skipped.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+", help="KiCad files or directories.")
    parser.add_argument("--source", choices=("auto", "pcb", "sch"), default="auto",
                        help="Where to read the components from (default: auto).")
    parser.add_argument("--out-dir", default=".", help="Directory for the output files.")
    parser.add_argument("--prefix", default="digikey", help="Output file name prefix.")
    parser.add_argument("--with-mpn", action="store_true",
                        help="Add Manufacturer / Manufacturer Part Number columns.")
    parser.add_argument("--generic-map", help="CSV (Value,Footprint,DigiKey) for generic parts.")
    parser.add_argument("--multiplier", type=float, default=1.0,
                        help="Multiply all quantities (e.g. 2 for two boards).")
    parser.add_argument("--include-dnp", action="store_true", help="Include DNP parts.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any component lacks a Digi-Key part number.")
    args = parser.parse_args()

    files = collect_files(args.paths, args.source)
    if not files:
        print("ERROR: no .kicad_pcb / .kicad_sch files found.", file=sys.stderr)
        return 1

    try:
        comps, skipped = extract(files, args.source, args.include_dnp)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not comps:
        print("ERROR: no components found.", file=sys.stderr)
        return 1

    generic_log = apply_generic_map(comps, load_generic_map(
        Path(args.generic_map) if args.generic_map else None))
    for line in generic_log:
        print(line)
    if generic_log:
        print("  (generic-map numbers are used as given - verify them on Digi-Key)")

    order, missing = group(comps)
    written = write_outputs(Path(args.out_dir), args.prefix, order, missing, comps,
                            args.with_mpn, args.multiplier)

    total_qty = sum(l["qty"] for l in order)
    print(f"\nComponents read: {len(comps)}   order lines: {len(order)}   "
          f"qty to order: {int(math.ceil(total_qty * args.multiplier))}")
    if skipped:
        print(f"Skipped: {len(skipped)}")
    print("\nWritten:")
    for path in written:
        print(f"  {path}")

    if missing:
        print(f"\n{len(missing)} part group(s) have no Digi-Key part number "
              f"({sum(m['qty'] for m in missing)} components):")
        for item in missing:
            print(f"  {item['value'] or '(no value)':<26} {item['footprint']:<20} "
                  f"x{item['qty']:<3} {' '.join(sorted(item['refs']))[:56]:<56} "
                  f"[{missing_tag(item)}]")

    warnings = [l for l in order if not DK_RE.match(l["dk"])]
    if warnings:
        print(f"\nWarning: {len(warnings)} part number(s) do not look like a "
              f"Digi-Key number (expected e.g. 535-13583-1-ND):")
        for line in warnings:
            print(f"  {line['dk']}")

    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())