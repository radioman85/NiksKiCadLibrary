#!/usr/bin/env python3
"""Schematic IR: a JITX-inspired, net-first description of a KiCad schematic.

The IR separates connectivity (declared nets) from geometry (placement,
wires, junctions) and is the source of truth the emitter renders. Component
definitions are loaded from the skill's lib_symbols templates via a small
adapter, so no pin/property tables are hand-maintained.

See ../../docs/ir-design.md for the design notes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# minimal s-expression parser (templates and schematics are small + trusted)
# --------------------------------------------------------------------------

_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+')


def parse(text):
    toks = _TOKEN.findall(text)

    def rec(i):
        if toks[i] != "(":
            return toks[i], i + 1
        i += 1
        node = []
        while toks[i] != ")":
            child, i = rec(i)
            node.append(child)
        return node, i + 1

    root, _ = rec(0)
    # unwrap a leading list wrapper: result is the node list itself
    return root


def tag(node):
    return node[0] if isinstance(node, list) and node else None


def unq(s):
    return str(s).strip('"')


def find(node, name):
    for child in node[1:]:
        if isinstance(child, list) and tag(child) == name:
            return child
    return None


# --------------------------------------------------------------------------
# IR types
# --------------------------------------------------------------------------

@dataclass
class Pin:
    num: str
    name: str
    kind: str                       # passive | power_in | power_out | ...
    offset: tuple[float, float]     # symbol anchor in lib space (x, y)


@dataclass
class Component:
    lib_id: str
    symbol_name: str
    pins: dict[str, Pin] = field(default_factory=dict)          # by pin num
    prop_offsets: dict[str, tuple[tuple[float, float], float]] = \
        field(default_factory=dict)  # e.g. "Reference" -> ((dx, dy), rot)


@dataclass
class Instance:
    ref: str
    lib_id: str
    value: str
    at: tuple[float, float] = (0.0, 0.0)
    angle: int = 0
    prop_overrides: dict[str, tuple[tuple[float, float], float]] | None = None

    def __getattr__(self, name):
        # pN -> endpoint (ref, pin_num); e.g. inst.p1 -> ("R1", "1")
        if name.startswith("p") and name[1:].isdigit():
            return (self.ref, name[1:])
        raise AttributeError(name)


@dataclass
class Net:
    name: str
    endpoints: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Sheet:
    name: str = "root"
    instances: list[Instance] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    wires: list[tuple[tuple[float, float], tuple[float, float]]] = \
        field(default_factory=list)
    junctions: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Design:
    sheets: list[Sheet] = field(default_factory=list)


# --------------------------------------------------------------------------
# adapter: lib_symbols template (.sym) -> Component
# --------------------------------------------------------------------------

def parse_symbol_template(path, lib_id):
    """Load one verbatim lib_symbol template into a Component."""
    with open(path, encoding="utf-8") as f:
        tree = parse(f.read())
    if tag(tree) != "symbol":
        raise ValueError(f"{path}: expected a (symbol ...) definition")
    symbol_name = unq(tree[1])
    pins = {}
    props = {}
    for node in tree[1:]:
        if not isinstance(node, list):
            continue
        if tag(node) == "symbol":
            for pin in node[1:]:
                if isinstance(pin, list) and tag(pin) == "pin":
                    num_n = find(pin, "number")
                    name_n = find(pin, "name")
                    at = find(pin, "at")
                    if num_n is not None and at is not None:
                        pnum = unq(num_n[1])
                        pname = (unq(name_n[1]) if name_n is not None and
                                 len(name_n) > 1 else "")
                        pins[pnum] = Pin(
                            num=pnum,
                            name=pname,
                            kind=str(pin[1]),
                            offset=(float(at[1]), float(at[2])),
                        )
        elif tag(node) == "property":
            at = find(node, "at")
            if at is not None:
                props[unq(node[1])] = ((float(at[1]), float(at[2])),
                                       float(at[3]))
    return Component(lib_id=lib_id, symbol_name=symbol_name,
                     pins=pins, prop_offsets=props)


def load_parts(templates_dir, libfiles):
    """libfiles: list of (symbol_name_in_template, filename, lib_id).
    Returns lib_id -> Component."""
    parts = {}
    for _symname, fname, lib_id in libfiles:
        parts[lib_id] = parse_symbol_template(
            os.path.join(templates_dir, fname), lib_id)
    return parts


# --------------------------------------------------------------------------
# builder & checker
# --------------------------------------------------------------------------

def add_net(sheet, name, *ends):
    """Declare a named net from endpoints (ref, pin_num). Same name = same net."""
    eps = []
    for e in ends:
        if e not in eps:
            eps.append(e)
    for net in sheet.nets:
        if net.name == name:
            net.endpoints.extend(e for e in eps if e not in net.endpoints)
            return net
    net = Net(name=name, endpoints=eps)
    sheet.nets.append(net)
    return net


def find_net(design, name):
    for sheet in design.sheets:
        for net in sheet.nets:
            if net.name == name:
                return net
    return None


def collect_instances(design):
    by_ref = {}
    for sheet in design.sheets:
        for inst in sheet.instances:
            by_ref.setdefault(inst.ref, []).append(inst)
    return by_ref


def check(design, parts):
    """Pre-emit checks. Returns list of error strings (empty = ok)."""
    errs = []
    by_ref = collect_instances(design)
    for ref, insts in sorted(by_ref.items()):
        if len(insts) > 1:
            errs.append(f"duplicate reference {ref}")
    refs = {inst.ref for sheet in design.sheets for inst in sheet.instances}
    for sheet in design.sheets:
        for net in sheet.nets:
            if len(net.endpoints) < 2:
                errs.append(f"net {net.name!r} has fewer than 2 endpoints")
            kinds = []
            seen = set()
            for r, p in net.endpoints:
                if r not in refs:
                    errs.append(f"net {net.name!r} references unknown instance {r!r}")
                    continue
                part = parts.get(_lib_of(by_ref, r))
                if part is None:
                    errs.append(f"instance {r!r} has no component definition")
                    continue
                pin = part.pins.get(p)
                if pin is None:
                    errs.append(f"instance {r}:{p} uses unknown pin {p} of {part.lib_id}")
                    continue
                kinds.append(pin.kind)
                if (r, p) in seen:
                    errs.append(f"net {net.name!r} lists {r}:{p} twice")
                seen.add((r, p))
            if any(k == "power_in" for k in kinds) and \
                    not any(k == "power_out" for k in kinds):
                errs.append(
                    f"net {net.name!r} has power_in pins but no power_out "
                    f"(needs a PWR_FLAG endpoint)")
    return errs


def _lib_of(by_ref, ref):
    return by_ref[ref][0].lib_id


# --------------------------------------------------------------------------
# nets summary (for print/debug, same form as golden netlist)
# --------------------------------------------------------------------------

def nets_summary(design):
    out = {}
    for sheet in design.sheets:
        for net in sheet.nets:
            out.setdefault(net.name, []).extend(
                f"{r}:{p}" for r, p in sorted(net.endpoints))
    return {k: sorted(set(v)) for k, v in out.items()}