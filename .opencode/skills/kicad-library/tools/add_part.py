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
#  legacy (KiCad 4/5) footprint conversion
# ---------------------------------------------------------------------------

LEGACY_MODULE_RE = re.compile(r'\s*\(module\s+"?([^"\s()]+)"?')
LAYER_TOKEN_RE = re.compile(r'\((layer|layers)\s+([^()]*)\)')
WIDTH_RE = re.compile(r'\(width\s+([0-9.]+)\)')
PAD_NUM_RE = re.compile(r'\(pad\s+"?([A-Za-z0-9_]+)"?')
HEAD_TOKEN_RE = re.compile(r'^\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)')


def _quote_layers(text: str) -> str:
    def repl(m):
        tok, names = m.group(1), m.group(2).split()
        return f'({tok} ' + " ".join(f'"{n}"' for n in names) + ')'
    return LAYER_TOKEN_RE.sub(repl, text)


def _width_to_stroke(text: str, stroke_type: str = "solid") -> str:
    return WIDTH_RE.sub(
        lambda m: f'(stroke (width {m.group(1)}) (type {stroke_type}))', text
    )


def _count_parens(text: str) -> tuple[int, int]:
    """Count '(' and ')' outside of quoted strings."""
    opens = closes = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "(":
            opens += 1
        elif c == ")":
            closes += 1
        i += 1
    return opens, closes


def _leading_closes(text: str) -> int:
    """Number of ')' that appear before the first '(' (outside strings)."""
    i, n = 0, len(text)
    lead = 0
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "(":
            return lead
        if c == ")":
            lead += 1
        i += 1
    return lead


def _indent_tabs(text: str) -> str:
    """Re-indent a single s-expression with tabs (repo convention)."""
    out = []
    depth = 0
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        opens, closes = _count_parens(s)
        out.append("\t" * max(depth - _leading_closes(s), 0) + s)
        depth += opens - closes
    return "\n".join(out)


def _legacy_arc_points(sx, sy, ex, ey, angle_deg):
    """Legacy `fp_arc` -> modern (start, mid, end).

    In the KiCad 4/5 format the arc's "start" is its CENTRE, "end" is the
    first point of the arc and "angle" is the sweep (positive = increasing
    angle).  The modern format describes the arc by three points.
    """
    import math

    r = math.hypot(ex - sx, ey - sy)
    if r == 0.0:
        return None
    phi = math.atan2(ey - sy, ex - sx)          # direction of the first point
    a = math.radians(angle_deg)

    def point(theta):
        return (sx + r * math.cos(theta), sy + r * math.sin(theta))

    return point(phi), point(phi + a / 2.0), point(phi + a)


def _legacy_child(child: str) -> str | None:
    """Convert one legacy footprint child; None = unsupported construct."""
    mt = HEAD_TOKEN_RE.match(child)
    head = (mt.group(1) if mt else "").lower()
    if head in ("tedit", "tstamp"):
        return ""
    if head == "fp_arc":
        ms = re.search(r'\(start\s+(-?[\d.]+)\s+(-?[\d.]+)\)', child)
        me = re.search(r'\(end\s+(-?[\d.]+)\s+(-?[\d.]+)\)', child)
        ma = re.search(r'\(angle\s+(-?[\d.]+)\)', child)
        if not (ms and me and ma):
            return None
        pts = _legacy_arc_points(
            float(ms.group(1)), float(ms.group(2)),
            float(me.group(1)), float(me.group(2)),
            float(ma.group(1)),
        )
        if pts is None:
            return None
        start, mid, end = pts
        triple = (
            f"(start {start[0]:.6f} {start[1]:.6f}) "
            f"(mid {mid[0]:.6f} {mid[1]:.6f}) "
            f"(end {end[0]:.6f} {end[1]:.6f})"
        )
        child, n = re.subn(
            r'\(start\s+-?[\d.]+\s+-?[\d.]+\)\s*'
            r'\(end\s+-?[\d.]+\s+-?[\d.]+\)\s*'
            r'\(angle\s+-?[\d.]+\)',
            triple, child, count=1)
        if not n:
            return None
        child = _width_to_stroke(child, "default")
    elif head in ("fp_line", "fp_poly", "fp_rect"):
        child = _width_to_stroke(child, "solid")
    elif head == "fp_circle":
        child = _width_to_stroke(child, "default")
    elif head == "pad":
        child = PAD_NUM_RE.sub(lambda m: f'(pad "{m.group(1)}"', child)
    elif head == "model":
        child = child.replace("(at (xyz", "(offset (xyz")
        # modern format expects the file name as a quoted string
        mm = re.match(r'(\s*\(model\s+)([^\s()"]+)', child)
        if mm:
            child = mm.group(1) + f'"{mm.group(2)}"' + child[mm.end(2):]
    elif head == "fp_text":
        # legacy allowed an unquoted text -> modern needs a quoted string,
        # and the legacy reference placeholder "%R" is now "${REFERENCE}"
        mt2 = re.match(
            r'(\s*\(fp_text\s+(?:reference|value|user)\s+)("[^"]*"|[^\s()]+)', child
        )
        if mt2:
            raw = mt2.group(2)
            val = raw[1:-1] if raw.startswith('"') else raw
            if val in ("%R", "%%R"):
                val = "${REFERENCE}"
            child = mt2.group(1) + f'"{val}"' + child[mt2.end(2):]
        # repo convention: the value text belongs on F.Fab, not on F.SilkS
        if re.match(r'\s*\(fp_text\s+value\b', child):
            child = re.sub(r'(\(layer\s+)"?F\.SilkS"?', r'\1F.Fab', child, count=1)
            child = re.sub(r'\s+hide\b', "", child, count=1)
    elif head in ("layer", "descr", "tags", "attr", "embedded_fonts",
                  "version", "generator", "generator_version", "property"):
        pass
    else:
        return None
    child = re.sub(r'\(tstamp\s+[0-9A-Fa-f-]+\)', "", child)
    child = _quote_layers(child)
    return child.strip()


def legacy_footprint_to_modern(text: str) -> tuple[str, str] | None:
    """Convert a KiCad 4/5 `(module ...)` footprint to the modern format.

    Returns (modern_text, name) or None if the file uses constructs that
    cannot be converted safely.
    """
    m = LEGACY_MODULE_RE.match(text)
    if not m:
        return None
    name = m.group(1)
    open_at = text.index("(")
    close_at = find_close(text, open_at)
    inner = text[open_at + 1:close_at]
    q_end = inner.index('"')
    q_end2 = inner.index('"', q_end + 1)
    rest = inner[q_end2 + 1:]
    parts = []
    for (s, e) in top_spans(rest):
        conv = _legacy_child(rest[s:e])
        if conv is None:
            return None
        if conv:
            parts.append(conv)
    body = "\n".join("\t" + p.replace("\n", "\n\t") for p in parts)
    header = f'(footprint "{name}" (version 20241229) (generator "pcbnew")'
    return _indent_tabs(f"{header}\n{body}\n)"), name


# ---------------------------------------------------------------------------
#  footprint handling
# ---------------------------------------------------------------------------

def process_footprint(ft_text: str) -> tuple[str, str]:
    ft_text = ft_text.strip()
    if LEGACY_MODULE_RE.match(ft_text):
        conv = legacy_footprint_to_modern(ft_text)
        if conv is None:
            raise ConfigError(
                "legacy (module ...) footprint with constructs that need a manual conversion"
            )
        ft_text = conv[0]
    m = FOOTPRINT_OPEN_RE.match(ft_text)
    if not m:
        raise ConfigError("not a footprint form")
    name = m.group(1)
    rest = m.group(2)
    header = f'(footprint "{name}"'
    if not re.search(r"\(\s*version ", rest):
        header += '\n\t(version 20241229)\n\t(generator "pcbnew")'
    form = header + rest
    # repo convention: the reference placeholder is "REF**"
    form = re.sub(r'(\(fp_text\s+reference\s+)"[^"]*"', r'\1"REF**"', form)
    return form, name


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
    added_symbols: list[tuple[str, str]] = []

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
        added_symbols.extend((n, f) for n, f, _ in processed)
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
    # Vendor packages often ship several footprint files that all carry the
    # same internal name (e.g. DGS10_TEX / DGS10_TEX-M / DGS10_TEX-L).  The
    # output file name comes from that internal name, so they would overwrite
    # each other -> name them after their source file instead.
    fp_items = []
    for ff in fts:
        try:
            form, ft_name = process_footprint(ff.read_text())
        except ConfigError as exc:
            print(f"  ! {ff.name}: {exc}")
            continue
        fp_items.append((ff, form, ft_name))
    name_count = {}
    for (_ff, _form, nm) in fp_items:
        name_count[nm] = name_count.get(nm, 0) + 1

    for (ff, form, ft_name) in fp_items:
        if name_count.get(ft_name, 0) > 1 and ff.stem != ft_name:
            form = re.sub(
                r'^\(footprint\s+"[^"]*"', f'(footprint "{ff.stem}"', form, count=1
            )
            print(f"  ! {ff.name}: name {ft_name!r} is used by several files "
                  f"-> using {ff.stem!r}")
            ft_name = ff.stem
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

    # ---- cross-check symbol -> footprint ---------------------------------
    for (sym_name, sym_form) in added_symbols:
        fp_val = get_property(sym_form, "Footprint") or ""
        tail = fp_val.rsplit(":", 1)[-1].strip()
        if tail and not (pretty / f"{tail}.kicad_mod").exists():
            print(f"  ! symbol {sym_name}: footprint {tail!r} is not in the library "
                  f"(available: {', '.join(sorted(p.stem for p in pretty.glob('*.kicad_mod')))})")

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