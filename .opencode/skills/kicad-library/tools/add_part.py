#!/usr/bin/env python3
"""
Add a new part (symbol + footprint + 3D model) to NiksKiCadLibrary.

Operates on the designator-based library layout:

    <lib>/
    ├── <DES>/<DES>.kicad_sym          one symbol library per designator (26 exist)
    ├── NiksKiCadLibrary.pretty/       single footprint library
    └── NiksKiCadLibrary.3dshapes/     3D models (referenced by bare filename)

Usage:
    add_part.py --lib <repo> --designator <DES> [--footprint NAME] [--model FILE] <source_dir>
    add_part.py --lib <repo> -L U --mpn STA8089FG --mfr STMicroelectronics <source_dir>

The source directory (a vendor/SnapEDA KiCad download) may contain:
    *.kicad_sym    symbol(s) added to <DES>/<DES>.kicad_sym
    *.kicad_mod    footprint(s) written to NiksKiCadLibrary.pretty/
    *.step/.stp    3D model(s) copied to NiksKiCadLibrary.3dshapes/

Behaviour:
  - The symbol `Reference` property is set to the designator, and the
    `Footprint` property is pointed at "NiksKiCadLibrary:<footprint>".
  - 3D models are referenced by bare filename inside the footprint so KiCad
    resolves them from the sibling .3dshapes folder.
  - A brand-new designator library is created and registered in the KiCad
    global sym-lib-table automatically.

Aisler.net (PCBA) parameters:
  Aisler auto-assigns parts on a 100% exact match of the MPN field. It accepts
  the property names MPN, Mpn, mpn or AISLER_MPN; `Value` is used as fallback.
  For generic parts (SMD R/C/L and signal LEDs) a Smart Match string can be
  placed in the MPN field instead ("100R 0603 1% 100mW").
  --mpn sets the MPN property (updates an existing alias in place).
  --mfr sets the MFG property (manufacturer name).
  --assume-mpn inserts MPN = part-number symbol name when no MPN field exists.
  All symbol fields transfer to the PCB file and are read on upload; an
  imported part without a matching MPN must be assigned manually on aisler.net.

DigiKey (distributor) parameter:
  --digikey sets the `DigiKey` property to the part's Digi-Key order number
  (e.g. "535-13583-1-ND"). Look the number up per component and pass it here;
  the symbol stores it so the BOM carries the distributor reference.

Country of origin parameter:
  --origin sets the `Origin` property to the part's country of origin (ISO 3166
  country code, e.g. "CN", "MY", "TH", "US"), as listed on the distributor
  (DigiKey) product page or the datasheet/box.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HEADER_SYM = (
    "(kicad_symbol_lib\n"
    "\t(version 20251024)\n"
    '\t(generator "kicad_symbol_editor")\n'
    '\t(generator_version "10.0")\n'
    ")\n"
)

MODEL_BLOCK = (
    '\t(model "{model}"\n'
    "\t\t(offset (xyz 0 0 0))\n"
    "\t\t(scale (xyz 1 1 1))\n"
    "\t\t(rotate (xyz 0 0 0))\n"
    "\t)\n"
)

PROPERTY_BLOCK = (
    '\t\t(property "{key}" "{value}"\n'
    "\t\t\t(at 0 0 0)\n"
    "\t\t\t(show_name no)\n"
    "\t\t\t(do_not_autoplace no)\n"
    "\t\t\t(hide yes)\n"
    "\t\t\t(effects\n"
    "\t\t\t\t(font\n"
    "\t\t\t\t\t(size 1.27 1.27)\n"
    "\t\t\t\t)\n"
    "\t\t\t\t(justify left top)\n"
    "\t\t\t)\n"
    "\t\t)\n"
)

DEFAULT_SYM_LIB_TABLE = Path.home() / ".var/app/org.kicad.KiCad/config/kicad/10.0/sym-lib-table"

KNOWN_DESIGNATORS = {
    "R": "Resistor", "C": "Capacitor", "L": "Inductor", "FB": "Ferrite Bead",
    "POT": "Potentiometer", "F": "Fuse",
    "Q": "Transistor (BJT/FET/MOSFET)", "D": "Diode (Zener, LED)",
    "U": "Integrated Circuit / MCU", "TR": "Transformer",
    "SW": "Switch (incl. tactile, rotary encoders)", "K": "Relay", "M": "Motor",
    "J": "Connector / Jack", "X": "Crystal / Resonator", "TP": "Test Point",
    "B": "Battery", "P": "Plug / Pin / Connector", "ANT": "Antenna",
    "LED": "LED", "DS": "Display (7-segment, LCD)", "LAMP": "Lamp / Bulb",
    "RT": "Thermistor", "S": "Sensor",
    "MOD": "Module", "FL": "Filter",
}

HEAD_RE = re.compile(r'\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*"([^"]*)"')
PROP_RE = re.compile(r'(\(property "([A-Za-z0-9_]+)"\s*)"([^"]*)"')
FOOTPRINT_OPEN_RE = re.compile(r'\s*\(footprint\s+"?([^"\s()]+)"?([\s\S]*)')


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
#  s-expression helpers (string-aware: parens inside quotes are ignored)
# ---------------------------------------------------------------------------

def _skip_string(text: str, i: int) -> int:
    n = len(text)
    i += 1
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return n


def find_close(text: str, start: int) -> int:
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ConfigError("unbalanced parentheses")


def top_spans(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c != "(":
            i += 1
            continue
        end = find_close(text, i)
        yield (i, end + 1)
        i = end + 1


def child_spans(form: str):
    for (s, e) in top_spans(form[1:]):
        yield (s + 1, e + 1)


def form_head(form: str) -> tuple[str | None, str | None]:
    m = HEAD_RE.match(form.strip())
    if not m:
        return None, None
    return m.group(1), m.group(2)


def balanced(text: str) -> bool:
    depth = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def indent_tab(form: str) -> str:
    return "\t" + form.strip() + "\n"


# ---------------------------------------------------------------------------
#  symbol handling
# ---------------------------------------------------------------------------

def extract_symbol_forms(sym_text: str) -> list[str]:
    spans = list(top_spans(sym_text))
    if not spans:
        raise ConfigError("source .kicad_sym contains no top-level form")
    if len(spans) == 1:
        outer = sym_text[spans[0][0]: spans[0][1]]
        children = [outer[s:e] for (s, e) in child_spans(outer)]
    else:
        children = [sym_text[s:e] for (s, e) in spans]
    return [c for c in children if (form_head(c)[0] or "").lower() == "symbol"]


def set_property(form: str, key: str, value: str) -> tuple[str, bool]:
    pat = re.compile(r'(\(property "' + re.escape(key) + r'"\s*)"[^"]*"')
    m = pat.search(form)
    if not m:
        return form, False
    return form[: m.start()] + m.group(1) + f'"{value}"' + form[m.end():], True


def insert_property(form: str, key: str, value: str) -> str:
    prop = PROPERTY_BLOCK.format(key=key, value=value)
    m = re.search(r"\n\s*\(symbol \"", form)
    if m:
        head = form[m.start():]
        head_indent = head[: len(head) - len(head.lstrip("\r\n\t "))]
        return form[: m.start()] + "\n" + prop + head_indent + head.lstrip("\r\n\t ")
    pos = form.rfind(")")
    return form[:pos] + "\n" + prop + form[pos:]


# Aisler.net recognizes the MPN from any of these symbol-property names
# (exact-match only); `Value` is used as a fallback when none is present.
AISLER_MPN_FIELDS = ("MPN", "Mpn", "mpn", "AISLER_MPN")


def get_property(form: str, key: str) -> str | None:
    m = re.search(r'\(property "' + re.escape(key) + r'"\s*"([^"]*)"', form)
    return m.group(1) if m else None


def get_mpn(form: str) -> tuple[str, str] | None:
    for key in AISLER_MPN_FIELDS:
        v = get_property(form, key)
        if v is not None:
            return key, v
    return None


def apply_aisler_fields(
    form: str, name: str, mpn: str | None, mfr: str | None, assume_mpn: bool
) -> list[str]:
    """Set the fields Aisler.net reads for PCBA ordering.

    - MPN field (value = exact manufacturer part number, or a Smart Match
      string for generic R/C/L/LEDs). Updating an existing alias keeps it.
    - MFG field (manufacturer name).
    Returns human-readable descriptions of the changes made.
    """
    changes: list[str] = []
    if mfr:
        if get_property(form, "MFG") is None:
            form = insert_property(form, "MFG", mfr)
        else:
            form, _ = set_property(form, "MFG", mfr)
        changes.append(f"MFG='{mfr}'")
    existing = get_mpn(form)
    if mpn:
        if existing:
            which = existing[0]
            if existing[1] != mpn:
                changes.append(f"{which}: {existing[1]!r} -> {mpn!r}")
        else:
            which = "MPN"
            changes.append(f"MPN:'{mpn}'")
        if existing:
            form, _ = set_property(form, which, mpn)
        else:
            form = insert_property(form, "MPN", mpn)
    elif assume_mpn and existing is None:
        form = insert_property(form, "MPN", name)
        changes.append(f"MPN:'{name}' (assumed from part number)")
    return form, changes


def set_or_insert_property(form: str, key: str, value: str) -> tuple[str, bool]:
    if get_property(form, key) is None:
        return insert_property(form, key, value), False
    return set_property(form, key, value)[0], True


def apply_property_field(form: str, key: str, value: str | None) -> list[str]:
    """Set/update a sourcing or metadata property; returns (form, changes)."""
    changes: list[str] = []
    if value:
        prev = get_property(form, key)
        form, was = set_or_insert_property(form, key, value)
        if was and prev != value:
            changes.append(f"{key}: {prev!r} -> {value!r}")
        elif not was:
            changes.append(f"{key}:'{value}'")
    return form, changes


def existing_symbol_names(lib_text: str) -> set[str]:
    spans = list(top_spans(lib_text))
    if not spans:
        return set()
    outer = lib_text[spans[0][0]: spans[0][1]]
    names = set()
    for (s, e) in child_spans(outer):
        head, name = form_head(outer[s:e])
        if (head or "").lower() == "symbol" and name and not re.search(r"_\d+_\d+$", name):
            names.add(name)
    return names


def add_symbols_to_lib(lib_path: Path, forms: list[tuple[str, str]]) -> None:
    if lib_path.exists():
        content = lib_path.read_text()
        existing = existing_symbol_names(content)
    else:
        content = HEADER_SYM
        existing = set()
    for name, form in forms:
        if name in existing:
            raise ConfigError(f"symbol {name!r} already exists in {lib_path}")
        existing.add(name)
    close = content.rfind(")")
    if close < 0:
        raise ConfigError(f"malformed library file {lib_path}")
    insert = "".join(indent_tab(f) for _, f in forms)
    content = content[:close] + insert + content[close:]
    lib_path.parent.mkdir(parents=True, exist_ok=True)
    lib_path.write_text(content)


# ---------------------------------------------------------------------------
#  footprint handling
# ---------------------------------------------------------------------------

def process_footprint(ft_text: str) -> tuple[str, str]:
    ft_text = ft_text.strip()
    m = FOOTPRINT_OPEN_RE.match(ft_text)
    if not m:
        raise ConfigError("not a footprint form")
    name = m.group(1)
    rest = m.group(2)
    header = f'(footprint "{name}"'
    if not re.search(r"\(\s*version ", rest):
        header += '\n\t(version 20241229)\n\t(generator "pcbnew")'
    return header + rest, name


def attach_model(ft_form: str, model_filename: str) -> str:
    spans = list(child_spans(ft_form))
    chunks = []
    cursor = 0
    for (s, e) in spans:
        child = ft_form[s:e]
        head, fn = form_head(child)
        if (head or "").lower() == "model" and fn and Path(fn).name == model_filename:
            cursor = e
            continue
        chunks.append(ft_form[cursor:e])
        cursor = e
    chunks.append(ft_form[cursor:])
    ft_form = "".join(chunks)
    block = MODEL_BLOCK.format(model=model_filename)
    if "embedded_fonts" not in ft_form:
        block = "\t(embedded_fonts no)\n" + block
    pos = ft_form.rfind(")")
    return ft_form[:pos] + "\n" + block + ft_form[pos:]


# ---------------------------------------------------------------------------
#  registration & helpers
# ---------------------------------------------------------------------------

def register_symbol_lib(table: Path, des: str, uri: str) -> bool:
    if not table.exists():
        print(f"  ! sym-lib-table not found (skipping registration): {table}")
        return False
    content = table.read_text()
    if f'Niks_{des}"' in content:
        return False
    close = content.rfind(")")
    if close < 0 or "(sym_lib_table" not in content:
        print(f"  ! malformed sym-lib-table (skipping registration): {table}")
        return False
    entry = (
        f'\n\t(lib (name "Niks_{des}") (type "KiCad") '
        f'(uri "{uri}") (options "") (descr ""))'
    )
    table.write_text(content[:close] + entry + content[close:])
    print(f"  registered Niks_{des} -> {uri}")
    return True


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------

def pick_model(ft_name: str, steps: list[Path], explicit: str | None) -> str | None:
    if explicit:
        return Path(explicit).name
    if len(steps) == 1:
        return steps[0].name
    lname = ft_name.lower()
    for s in steps:
        base = s.stem.lower()
        if base and (lname.startswith(base) or base.startswith(lname)):
            return s.name
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--lib", required=True, help="path to NiksKiCadLibrary repo")
    parser.add_argument("--designator", required=True, help="designator (e.g. L, U, SW)")
    parser.add_argument("--footprint", help="footprint name override")
    parser.add_argument("--model", help="3D model filename override (basename)")
    parser.add_argument("--sym-lib-table", default=str(DEFAULT_SYM_LIB_TABLE))
    parser.add_argument("--mpn", help="exact manufacturer part number; sets the MPN property (required by Aisler for auto-assignment)")
    parser.add_argument("--mfr", help="manufacturer name; sets the MFG property (recognized by Aisler)")
    parser.add_argument("--assume-mpn", action="store_true",
                        help="if the source has no MPN field, set MPN to the symbol part number")
    parser.add_argument("--digikey", help="Digi-Key part (order) number, e.g. 535-13583-1-ND; sets the DigiKey property")
    parser.add_argument("--origin", help="country of origin of the part (e.g. CN, MY, TH, US); sets the Origin property")
    parser.add_argument("src", help="directory containing the vendor part files")
    args = parser.parse_args()

    repo = Path(args.lib).expanduser().resolve()
    des = args.designator.strip().upper()
    src = Path(args.src).expanduser().resolve()

    if not repo.is_dir():
        print(f"ERROR: library repo not found: {repo}", file=sys.stderr)
        return 1
    if not src.is_dir():
        print(f"ERROR: source dir not found: {src}", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Z0-9]{1,8}", des):
        print(f"ERROR: invalid designator {des!r}", file=sys.stderr)
        return 1

    pretty = repo / "NiksKiCadLibrary.pretty"
    shapes = repo / "NiksKiCadLibrary.3dshapes"
    lib_path = repo / des / f"{des}.kicad_sym"
    pretty.mkdir(parents=True, exist_ok=True)
    shapes.mkdir(parents=True, exist_ok=True)

    syms = sorted(src.glob("*.kicad_sym"))
    fts = sorted(src.glob("*.kicad_mod"))
    steps = sorted(p for p in src.iterdir() if p.suffix.lower() in (".step", ".stp"))

    if not (syms or fts or steps):
        print(f"ERROR: no .kicad_sym/.kicad_mod/.step files found in {src}", file=sys.stderr)
        return 1

    if des not in KNOWN_DESIGNATORS:
        print(f"  ! {des!r} is not a standard designator; creating/using it anyway")

    print(f"designator  : {des} ({KNOWN_DESIGNATORS.get(des, 'custom')})")
    print(f"symbol files: {[p.name for p in syms]}")
    print(f"footprints  : {[p.name for p in fts]}")
    print(f"3D models   : {[p.name for p in steps]}")
    print()

    new_lib = not lib_path.exists()

    # ---- symbols ---------------------------------------------------------
    for sf in syms:
        forms = extract_symbol_forms(sf.read_text())
        processed = []
        for form in forms:
            _, name = form_head(form)
            if not name:
                continue
            form, _ = set_property(form, "Reference", des)
            fp_name = args.footprint
            if not fp_name:
                for (s, e) in child_spans(form):
                    m = PROP_RE.search(form[s:e])
                    if m and m.group(2) == "Footprint" and m.group(3):
                        tail = m.group(3).rsplit(":", 1)[-1]
                        if tail:
                            fp_name = tail
                        break
            if not fp_name and len(fts) == 1:
                fp_name = fts[0].stem
            if fp_name:
                form, _ = set_property(form, "Footprint", f"NiksKiCadLibrary:{fp_name}")
            form, aisler_changes = apply_aisler_fields(
                form, name, args.mpn, args.mfr, args.assume_mpn
            )
            form, dk_changes = apply_property_field(form, "DigiKey", args.digikey)
            form, origin_changes = apply_property_field(form, "Origin", args.origin)
            processed.append(
                (name, form, aisler_changes + dk_changes + origin_changes)
            )
        add_symbols_to_lib(lib_path, [(n, f) for n, f, _ in processed])
        for name, form, changes in processed:
            print(f"  symbol added: {name}")
            for c in changes:
                print(f"      field: {c}")
            mpn = get_mpn(form)
            if mpn:
                print(f"      field: MPN recognised in field {mpn[0]}='{mpn[1]}'")
            else:
                print("      field: no MPN set - pass --mpn <exact part number> for Aisler auto-assignment")
            dk = get_property(form, "DigiKey")
            if dk:
                print(f"      field: DigiKey='{dk}'")
            else:
                print("      field: no DigiKey set - pass --digikey <order number>")
            origin = get_property(form, "Origin")
            if origin:
                print(f"      field: Origin='{origin}'")
            else:
                print("      field: no Origin set - pass --origin <country code>")
        print(f"  {len(processed)} symbol(s) added to {lib_path.name}")

    # ---- footprints ------------------------------------------------------
    for ff in fts:
        ft_text = ff.read_text()
        try:
            form, ft_name = process_footprint(ft_text)
        except ConfigError as exc:
            print(f"  ! {ff.name}: {exc}")
            continue
        model = pick_model(ft_name, steps, args.model)
        if model:
            if not (shapes / model).exists():
                shutil.copy2(next(s for s in steps if s.name == model), shapes / model)
                print(f"  3D model copied: {shapes / model}")
            else:
                print(f"  3D model exists: {shapes / model}")
            form = attach_model(form, model)
        else:
            print(f"  ! {ft_name}: no matching 3D model; footprint left without model")
        target = pretty / f"{ft_name}.kicad_mod"
        if target.exists():
            print(f"  ! footprint already exists, overwriting: {target}")
        target.write_text(form + "\n")
        print(f"  footprint written: {target.name}")

    # ---- any remaining 3D models ----------------------------------------
    for sp in steps:
        if not (shapes / sp.name).exists():
            shutil.copy2(sp, shapes / sp.name)
            print(f"  3D model copied: {shapes / sp.name}")

    # ---- registration of a brand-new designator library ------------------
    if new_lib:
        register_symbol_lib(Path(args.sym_lib_table), des, str(lib_path))

    # ---- verify ----------------------------------------------------------
    print()
    print("verify:")
    targets = [lib_path]
    targets += sorted(pretty.glob("*.kicad_mod"))
    damaged = False
    for t in targets:
        if not t.exists():
            continue
        ok = balanced(t.read_text())
        damaged = damaged or not ok
        print(f"  {'OK ' if ok else 'ERR'} {t.relative_to(repo)}")
    if damaged:
        print("ERROR: unbalanced s-expr produced; inspect before committing", file=sys.stderr)
        return 1

    print()
    print("done. Reload/restart KiCad so the Symbol Library Manager picks it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())