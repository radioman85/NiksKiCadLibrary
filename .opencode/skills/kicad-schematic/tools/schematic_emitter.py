#!/usr/bin/env python3
"""Emit a KiCad 10 .kicad_sch file from an IR Design + explicit layout.

Mirrors the reference generator (ai_pcb_hw/generate_schematic.py):
  * instances are placed from an explicit layout dict {ref: (origin, angle)};
    connectivity is declared net-first in the Design object and the pins
    /property-text offsets come from the IR parts (loaded from the skill's
    lib_symbols templates) rather than hand-maintained tables.
  * PROPS_OVERRIDES keeps output byte-identical to the reference output where
    a template's property offsets differ from what the reference emits
    (R Value, GND Reference), so the golden netlist and file diffs stay valid.
  * uuids are deterministic (uuid5) under a seed prefix, so regeneration is
    byte-for-byte stable.

Auto-routing from nets to wires/junctions is a later stage; for now wires and
junctions are still part of the explicit layout, chosen on the 1.27 mm grid.
"""

from __future__ import annotations

import math
import os
import uuid

NAMESPACE = uuid.UUID('9a5c6e18-4d7f-4c1b-a2e3-8f0d1b2c3d4e')

# lib-level property-text overrides so emitted files equal the reference
# generator's output even where the .sym template differs from it.
PROPS_OVERRIDES = {
    "Device:R": {"Value": ((0, -2.03), 0)},
    "power:GND": {"Reference": ((0, -3.81), 0)},
}


def gen_uuid(seed):
    return str(uuid.uuid5(NAMESPACE, seed))


def placed_offset(x, y, angle):
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    return (x * c - y * s, -(x * s + y * c))


def rot(x, y, angle):
    a = math.radians(angle)
    return (x * math.cos(a) - y * math.sin(a),
            x * math.sin(a) + y * math.cos(a))


def prop_offset(part, lib_id, prop):
    ov = PROPS_OVERRIDES.get(lib_id, {}).get(prop)
    if ov is not None:
        return ov
    return part.prop_offsets.get(prop, ((0.0, 0.0), 0))


def lib_symbols(libs_dir, libfiles):
    """libfiles: list of (lib_symbol_name, filename, prefixed_lib_id)."""
    syms = []
    for name, fname, prefixed in libfiles:
        with open(os.path.join(libs_dir, fname), encoding="utf-8") as f:
            text = f.read()
        first, rest = text.split("\n", 1)
        first = first.replace(f'(symbol "{name}"', f'(symbol "{prefixed}"', 1)
        syms.append(first + "\n" + rest)
    return "  (lib_symbols\n" + "\n".join(syms) + "  )"


def make_symbol(ref, lib_id, value, origin, angle, sym_uuid, pin_uuids, part,
                project_name):
    x, y = origin
    a = angle % 360
    r_off, r_rot = prop_offset(part, lib_id, "Reference")
    v_off, v_rot = prop_offset(part, lib_id, "Value")
    rx, ry = rot(*r_off, a)
    vx, vy = rot(*v_off, a)
    ref_effects = '(effects (font (size 1.27 1.27)) (justify left bottom))'
    if lib_id.startswith("power:"):
        ref_effects = '(effects (font (size 1.27 1.27)) hide)'
    pins = "".join(f'\n    (pin "{pn}" (uuid "{pu}"))' for pn, pu in pin_uuids)
    return f'''  (symbol
    (lib_id "{lib_id}")
    (at {x} {y} {a})
    (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (uuid "{sym_uuid}")
    (property "Reference" "{ref}" (at {round(x+rx,2)} {round(y+ry,2)} {(a+r_rot)%360}) {ref_effects})
    (property "Value" "{value}" (at {round(x+vx,2)} {round(y+vy,2)} {(a+v_rot)%360}) (effects (font (size 1.27 1.27)) (justify left)))
    (property "Footprint" "" (at {x} {y} {a}) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "" (at {x} {y} {a}) (effects (font (size 1.27 1.27)) hide))
    (property "Description" "" (at {x} {y} {a}) (effects (font (size 1.27 1.27)) hide)){pins}
    (instances
      (project "{project_name}"
        (path "/" (reference "{ref}") (unit 1))))
  )'''


def make_wire(p1, p2, w_uuid):
    return f'''  (wire (pts (xy {p1[0]} {p1[1]}) (xy {p2[0]} {p2[1]}))
    (stroke (width 0) (type default)) (uuid "{w_uuid}"))'''


def make_junction(p, j_uuid):
    return f'''  (junction (at {p[0]} {p[1]}) (diameter 0) (color 0 0 0 0) (uuid "{j_uuid}"))'''


def build(design, parts, libfiles, libs_dir, opts):
    """opts: seed_prefix, project, version, generator_version, paper,
    title_block dict {title,date,rev,company,comments:[(n,text)]},
    texts [(content, at, seed_key)], layout {ref: (origin, angle)},
    wires [...], junctions [...]."""
    sp = opts["seed_prefix"]

    header = [
        f'(kicad_sch',
        f'  (version {opts.get("version", 20260306)})',
        f'  (generator "eeschema")',
        f'  (generator_version "{opts.get("generator_version", "10.0")}")',
        f'  (uuid "{gen_uuid(sp + ".sheet")}")',
        f'  (paper "{opts.get("paper", "A4")}")',
        f'  (title_block',
    ]
    tb = opts["title_block"]
    header.append(f'    (title "{tb["title"]}")')
    header.append(f'    (date "{tb["date"]}")')
    header.append(f'    (rev "{tb["rev"]}")')
    header.append(f'    (company "{tb["company"]}")')
    comments = tb.get("comments", [])
    for i, (n, t) in enumerate(comments):
        close = ")" if i == len(comments) - 1 else ""
        header.append(f'    (comment {n} "{t}"){close}')
    parts_out = ["\n".join(header)]

    for text, at, seed_key in opts.get("texts", []):
        parts_out.append(
            f'  (text "{text}"\n'
            f'    (effects (font (size 1.27 1.27))) (uuid "{gen_uuid(sp + "." + seed_key)}") '
            f'(at {at[0]} {at[1]} {at[2]}))')

    parts_out.append(lib_symbols(libs_dir, libfiles))

    layout = opts["layout"]
    instances = [i for sheet in design.sheets for i in sheet.instances]
    sym_uuid = {inst.ref: gen_uuid(f"{sp}.sym.{inst.ref}")
                for inst in instances}
    pin_uuid = {}
    for inst in instances:
        part = parts[inst.lib_id]
        for pn in part.pins:
            pin_uuid[(inst.ref, pn)] = gen_uuid(f"{sp}.pin.{inst.ref}.{pn}")
    for inst in instances:
        origin, angle = layout[inst.ref]
        pins = [(pn, pin_uuid[(inst.ref, pn)]) for pn in parts[inst.lib_id].pins]
        parts_out.append(make_symbol(
            inst.ref, inst.lib_id, inst.value, origin, angle,
            sym_uuid[inst.ref], pins, parts[inst.lib_id], opts["project"]))

    for i, (p1, p2) in enumerate(opts["wires"]):
        parts_out.append(make_wire(p1, p2, gen_uuid(f"{sp}.wire.{i}")))
    for i, p in enumerate(opts["junctions"]):
        parts_out.append(make_junction(p, gen_uuid(f"{sp}.junc.{i}")))

    parts_out.append('''  (sheet_instances
    (path "/" (page "1")))
  (embedded_fonts no)
)''')
    return "\n".join(parts_out) + "\n"


def tip_coords(inst, part, origin, angle):
    out = []
    for pn, pin in part.pins.items():
        dx, dy = placed_offset(*pin.offset, angle)
        out.append((f"{inst.ref}.{pn}",
                    (round(origin[0] + dx, 2), round(origin[1] + dy, 2))))
    return out