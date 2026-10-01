#!/usr/bin/env python3
"""Compare a kicadxml netlist export against a golden netlist.

Normalizes away everything that is volatile between runs (uuids, timestamps,
absolute source paths, sheet stamps) and reduces each net to its stable
essence: net name -> sorted list of "ref:pin" members.

Usage:
    netlist_diff.py <netlist.xml> <golden>       compare; exit 0 same / 1 diff
    netlist_diff.py --update <netlist.xml> <golden>   (re)write golden
"""

import sys
import xml.etree.ElementTree as ET

GOLDEN_HEADER = "# golden netlist (normalized: net name -> sorted ref:pin)\n"


def local(tag):
    return tag.rsplit("}", 1)[-1]


def normalize(path):
    tree = ET.parse(path)
    root = tree.getroot()
    nets = {}
    for net in root.iter("net"):
        name = net.get("name") or ""
        members = []
        for node in net.iter("node"):
            ref = node.get("ref")
            pin = node.get("pin")
            if ref:
                members.append(f"{ref}:{pin}")
        nets.setdefault(name, []).extend(members)
    out = {}
    for name, members in sorted(nets.items()):
        out[name] = sorted(set(members))
    return out


def dump(nets):
    lines = []
    for name, members in sorted(nets.items()):
        lines.append(f"{name}\t{','.join(members)}")
    return lines


def load_golden(path):
    nets = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            name, _, members = line.partition("\t")
            nets[name] = sorted(members.split(",") if members else [])
    return nets


def write_golden(path, nets):
    with open(path, "w", encoding="utf-8") as f:
        f.write(GOLDEN_HEADER)
        f.write("\n".join(dump(nets)))
        f.write("\n")


def main():
    update = "--update" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--update"]
    if len(args) < 2:
        print(__doc__)
        return 2
    xml, golden = args[:2]
    try:
        cur = normalize(xml)
    except ET.ParseError as e:
        print(f"ERROR: could not parse netlist XML {xml}: {e}")
        return 2
    if update:
        write_golden(golden, cur)
        print(f"Golden netlist updated: {golden} ({len(cur)} nets)")
        return 0
    ref = load_golden(golden)
    only_ref = sorted(set(ref) - set(cur))
    only_cur = sorted(set(cur) - set(ref))
    diffs = []
    for name in sorted(set(ref) & set(cur)):
        if ref[name] != cur[name]:
            diffs.append(name)
    if not (only_ref or only_cur or diffs):
        print(f"netlist matches golden ({len(cur)} nets)")
        return 0
    print("netlist MISMATCH against golden")
    for name in only_ref:
        print(f"  - net missing in current: {name}  golden={ref[name]}")
    for name in only_cur:
        print(f"  + net added in current:   {name}  now={cur[name]}")
    for name in diffs:
        print(f"  ~ net member diff: {name}")
        print(f"      golden: {ref[name]}")
        print(f"      now:    {cur[name]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())