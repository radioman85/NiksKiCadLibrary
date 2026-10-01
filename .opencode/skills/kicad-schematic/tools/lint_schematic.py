#!/usr/bin/env python3
"""Static structural lint for a generated KiCad schematic (.kicad_sch).

Checks the rules that were verified empirically against kicad-cli 10.0.5:
  1. All coordinates (symbol origins, wire endpoints, junctions, pin tips)
     lie on the 1.27 mm schematic grid.
  2. Connectivity: a wire endpoint touching the middle of another wire does
     NOT connect unless an explicit (junction) marker is present. Crossings
     (both wires passing through) never connect without a marker.
  3. Every symbol pin must land on something (a wire endpoint, another pin,
     or a junction) or it is unconnected.
  4. Any net that contains a Power Input pin must also contain a Power Output
     pin (mirrors ERC's power_pin_not_driven rule).
  5. Junctions must sit on actual connections; wires must be axis-aligned.

Exit code 0 = clean, 1 = errors, 2 = warnings only.
"""

import sys


def tokenize(text):
    toks = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in '()':
            toks.append(c)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 1
            toks.append('"' + text[i + 1:j] + '"')
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            toks.append(text[i:j])
            i = j
    return toks


def parse(text):
    toks = tokenize(text)
    pos = [0]

    def _num(s):
        try:
            return float(s)
        except ValueError:
            return s

    def _atom(t):
        if t.startswith('"'):
            return t[1:-1]
        return _num(t)

    def _expr():
        assert toks[pos[0]] == '(', toks[pos[0] - 1] if pos[0] else "?EOF"
        pos[0] += 1
        out = []
        while toks[pos[0]] != ')':
            t = toks[pos[0]]
            if t == '(':
                out.append(_expr())
            else:
                out.append(_atom(t))
                pos[0] += 1
        pos[0] += 1
        return out

    tree = []
    while pos[0] < len(toks):
        if toks[pos[0]] == '(':
            tree.append(_expr())
        else:
            pos[0] += 1
    return tree


def tag(node):
    return node[0]


def find_all(tree, name, depth=0):
    for node in tree:
        if isinstance(node, list) and node and tag(node) == name:
            yield node
        if isinstance(node, list):
            yield from find_all(node, name, depth + 1)


def plist(node, name):
    for child in node[1:]:
        if isinstance(child, list) and child and child[0] == name:
            return child[1:]
    return None


def rg(val):
    return round(float(val), 2)


class DSU:
    def __init__(self):
        self.p = {}
        self.size = {}

    def node(self, k):
        if k not in self.p:
            self.p[k] = k
            self.size[k] = 1
        return k

    def find(self, x):
        x = self.node(x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        self.size[ra] += self.size[rb]


def axis(a, b):
    return a[0] == b[0] or a[1] == b[1]


def on_segment(p, a, b, strict=False):
    """p on the (optionally strict-)interior of axis-aligned segment a-b."""
    x, y = p
    xa, ya = a
    xb, yb = b
    on = (xa == xb == x and min(ya, yb) <= y <= max(ya, yb)) or \
         (ya == yb == y and min(xa, xb) <= x <= max(xa, xb))
    if not on:
        return False
    if strict:
        return p != a and p != b
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: lint_schematic.py FILE.kicad_sch", file=sys.stderr)
        return 1
    text = open(sys.argv[1], encoding="utf-8").read()
    tree = parse(text)

    # ---- lib symbols: name -> {pin_num: (type, (x, y))} ----
    libs = {}
    root = tree[0]
    for node in root[1:]:
        if isinstance(node, list) and node and tag(node) == 'lib_symbols':
            for sub in node[1:]:
                if isinstance(sub, list) and sub and tag(sub) == 'symbol':
                    name = sub[1]
                    pins = {}
                    for unit in sub[1:]:
                        if isinstance(unit, list) and tag(unit) == 'symbol':
                            for pin in unit[1:]:
                                if isinstance(pin, list) and tag(pin) == 'pin':
                                    ptype = pin[1]
                                    at = plist(pin, 'at')
                                    number = None
                                    for prop in pin:
                                        if isinstance(prop, list) and tag(prop) == 'number':
                                            number = prop[1]
                                            break
                                    off = (rg(at[0]), rg(at[1])) if at else (0.0, 0.0)
                                    pins[str(number)] = (ptype, off)
                    libs[name] = pins

    # ---- placed symbols ----
    symbols = []  # (lib_id, ref, value, x, y, rot)
    for node in root[1:]:
        if isinstance(node, list) and tag(node) == 'symbol' and tag(node) != 'lib_symbols':
            lib_id = plist(node, 'lib_id')
            at = plist(node, 'at')
            ref = value = None
            for prop in node:
                if isinstance(prop, list) and prop[:2] == ['property', 'Reference']:
                    ref = prop[2]
                if isinstance(prop, list) and prop[:2] == ['property', 'Value']:
                    value = prop[2]
            if lib_id:
                symbols.append((lib_id[0], ref, value, rg(at[0]), rg(at[1]),
                                rg(at[2]) if len(at) > 2 else 0))

    # ---- wires / junctions ----
    segments = []
    junction_pts = set()
    for node in find_all(root, 'wire'):
        pts = plist(node, 'pts')
        xy = [c for c in pts if isinstance(c, list) and c and tag(c) == 'xy']
        if len(xy) == 2:
            segments.append(((rg(xy[0][1]), rg(xy[0][2])), (rg(xy[1][1]), rg(xy[1][2]))))
    for node in find_all(root, 'junction'):
        at = plist(node, 'at')
        junction_pts.add((rg(at[0]), rg(at[1])))

    # ---- pin tips per placed symbol ----
    pins = []  # (ref, pin_num, etype, (x, y))
    import math
    for lib_id, ref, value, ox, oy, rot in symbols:
        deg = math.radians(rot)
        c, s = math.cos(deg), math.sin(deg)
        for num, (ptype, (dx, dy)) in (libs.get(lib_id) or {}).items():
            x = dx * c - dy * s
            y = -(dx * s + dy * c)
            pins.append((ref, num, ptype, (round(ox + x, 2), round(oy + y, 2))))

    errs, warns = [], []

    # ---- 1. grid check ----
    def on_grid(v):
        return abs(round(v / 1.27) * 1.27 - v) < 1e-6

    for (a, b) in segments:
        for p in (a, b):
            if not on_grid(p[0]) or not on_grid(p[1]):
                warns.append(f"wire endpoint off grid: {p}")
    for jp in junction_pts:
        if not on_grid(jp[0]) or not on_grid(jp[1]):
            warns.append(f"junction off grid: {jp}")
    for lib_id, ref, value, ox, oy, rot in symbols:
        if not on_grid(ox) or not on_grid(oy):
            warns.append(f"symbol {ref} origin off grid: ({ox}, {oy})")
    for ref, num, ptype, pos in pins:
        if not on_grid(pos[0]) or not on_grid(pos[1]):
            warns.append(f"pin {ref}.{num} tip off grid: {pos}")

    # ---- axis alignment ----
    for (a, b) in segments:
        if not axis(a, b):
            errs.append(f"wire not axis-aligned: {a} -> {b}")

    # ---- 2/3/4/5. connectivity ----
    dsu = DSU()

    # every wire contributes a real edge between its endpoints
    for (a, b) in segments:
        na, nb = dsu.node(a), dsu.node(b)
        dsu.union(na, nb)
        if a == b:
            warns.append(f"zero-length wire at {a}")

    # junctions connect every segment that touches the point
    for jp in junction_pts:
        n = dsu.node(jp)
        for (a, b) in segments:
            if on_segment(jp, a, b, strict=False):
                dsu.union(n, dsu.node(a))
                dsu.union(n, dsu.node(b))

    # pin nodes
    for ref, num, ptype, pos in pins:
        dsu.node(pos)

    # endpoint on the strict interior of another wire, unterminated
    for (a, b) in segments:
        for (c, d) in segments:
            if (a, b) == (c, d):
                continue
            if a in junction_pts and b in junction_pts:
                continue
            if on_segment(a, c, d, strict=True) and a not in junction_pts:
                warns.append(
                    f"wire endpoint {a} ends on the interior of wire {c}->{d} "
                    "without a junction (no connection)")
            if on_segment(b, c, d, strict=True) and b not in junction_pts:
                warns.append(
                    f"wire endpoint {b} ends on the interior of wire {c}->{d} "
                    "without a junction (no connection)")

    # duplicate identical segments
    seen = set()
    for (a, b) in sorted(segments):
        key = (a, b) if a <= b else (b, a)
        if key in seen:
            warns.append(f"duplicate wire {a} -> {b}")
        seen.add(key)

    # pin connectivity: must be adjacent to a wire endpoint or junction point
    pin_nodes = {}
    for ref, num, ptype, pos in pins:
        pin_nodes[(ref, num)] = pos

    connected_pts = set()
    for (a, b) in segments:
        connected_pts.add(a)
        connected_pts.add(b)
    for jp in junction_pts:
        connected_pts.add(jp)

    for (ref, num), pos in pin_nodes.items():
        if pos in connected_pts:
            continue
        # maybe inside a segment with a junction at that exact point?
        with_junc = any(jp == pos for jp in junction_pts)
        on_wire = any(on_segment(pos, a, b, strict=False) for (a, b) in segments)
        if on_wire and with_junc:
            continue
        errs.append(f"pin {ref}.{num} ({pos}) is not connected to any wire/junction")

    # ---- 4. power-in nets need a power-out pin ----
    ref_lib = {s[1]: s[0] for s in symbols}
    ref_value = {s[1]: s[2] for s in symbols}
    NETNAME_LIBS = ('power:+5V', 'power:VCC', 'power:VDD', 'power:GND',
                    'power:GNDA', 'power:GNDD')

    comp_types = {}
    comp_netnames = {}   # geometry root -> set of global power-net names
    for ref, num, ptype, pos in pins:
        rid = dsu.find(pos)
        comp_types.setdefault(rid, []).append((ref, num, ptype))
        if ref_lib.get(ref) in NETNAME_LIBS:
            comp_netnames.setdefault(rid, set()).add(ref_value.get(ref))

    # Merge geometry components that carry the same global power-net name.
    root_groups = {}
    for rid, names in comp_netnames.items():
        for name in names:
            root_groups.setdefault(('net', name), set()).add(rid)
    for rid in comp_types:
        if rid not in set().union(*[g for g in root_groups.values()] if root_groups else [set()]):
            root_groups.setdefault(('geom', rid), set()).add(rid)

    for group, rids in root_groups.items():
        members = []
        for rid in rids:
            members.extend(comp_types.get(rid, []))
        in_ = [m for m in members if m[2] == 'power_in']
        out_ = [m for m in members if m[2] == 'power_out']
        if in_ and not out_:
            refs = sorted({m[0] for m in in_})
            errs.append(
                "net with Power Input pins has no Power Output pin "
                "(ERC: Input Power pin not driven) -- symbols "
                + ", ".join(refs))

    # ---- 5. lonely junctions ----
    for jp in junction_pts:
        touches = any(on_segment(jp, a, b, strict=False) for (a, b) in segments)
        touches |= any(pos == jp for (*_, pos) in pins)
        if not touches:
            warns.append(f"junction at {jp} touches no wire or pin")

    # ---- report ----
    for w in sorted(warns):
        print(f"WARN  {w}")
    for e in sorted(errs):
        print(f"ERROR {e}")
    print(f"\n{len(errs)} errors, {len(warns)} warnings "
          f"({len(symbols)} symbols, {len(segments)} wires, "
          f"{len(junction_pts)} junctions, {len(pins)} pins)")
    if errs:
        return 1
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())