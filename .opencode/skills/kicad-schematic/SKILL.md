---
name: kicad-schematic
description: Specialist for creating, editing, and inspecting KiCad electrical schematics. Use when working with KiCad schematic files (.kicad_sch), placing symbols, drawing wires, labels, nets, hierarchical sheets, circuit diagrams, or ERC. Use the project-provided KiCad tooling for validation and live interaction with KiCad.
version: 0.1.0
---

# KiCad Schematic Specialist

You create, edit, and inspect KiCad electrical schematics as s-expression `.kicad_sch` files. This skill covers the file format, project layout, schematic design workflow, and the rules that keep generated schematics valid and loadable by KiCad Eeschema.

## When to use this skill

- Creating a new schematic or project from scratch.
- Adding/removing symbols, wires, buses, labels, or junctions.
- Connecting pins into nets and verifying electrical connectivity.
- Reviewing or fixing an existing `.kicad_sch`.
- Reworking or investigating an ERC (Electrical Rules Check) issue.
- Inspecting schematic structure, symbols, references, and connectivity.

## Tooling

Use the **KiCad tooling provided by the project setup** rather than assuming a system-native KiCad installation.

- Use `kicad-cli` for command-line operations such as schematic ERC, exports, upgrades, and other file-oriented automation.
- Use `kicad-python` with the official `kicad-python` IPC bindings when live interaction with a running KiCad instance is useful.
- Use direct `.kicad_sch` editing when constructing or making precise structural changes is simpler and reliable.
- After significant modifications, validate the result with KiCad where practical.

Do not assume that the Python interpreter invoked as `python` inside the VS Code Flatpak is the host Python environment. For KiCad IPC operations, use the project-provided `kicad-python` command.

The project setup documentation defines the available wrappers, Python environment, Flatpak integration, and KiCad version.

### Skill-provided utilities

This skill ships reusable, self-contained utilities in `tools/` and symbol
building blocks in `templates/lib_symbols/`. They take the target `.kicad_sch`
as an argument, so they work on any project:

- `tools/verify_schematic.sh [--update-golden] [--pdf] <schematic.kicad_sch> [golden]`
  — static lint, ERC, and golden-netlist diff in one command. Resolves its
  bundled scripts from its own directory; resolves relative golden paths
  against the schematic's directory. Exits nonzero when any check fails.
- `tools/lint_schematic.py` — static lint of a `.kicad_sch`: 1.27 mm grid
  alignment, wire/junction connection rules, pin connectivity, and the
  power-in-net-needs-a-power-out rule.
- `tools/netlist_diff.py` — normalizes a `kicad-cli sch export netlist`
  kicadxml file to `net name -> sorted ref:pin` and diffs it against a golden
  file (`--update` (re)writes the golden).
- `tools/schematic_ir.py` — JITX-inspired, net-first IR for describing a
  schematic (see `docs/ir-design.md`): `Pin`/`Component`/`Instance`/`Net`/
  `Sheet`/`Design` types, a small s-expression parser, an adapter that loads
  `Component` definitions from `templates/lib_symbols/*.sym`, and a pre-emit
  `check()` that validates connectivity (power-in nets need a power-out
  endpoint, references are unique, pin numbers exist).
- `tools/schematic_emitter.py` — renders an IR `Design` to a KiCad 10
  `.kicad_sch` (deterministic uuid5 seeds, byte-stable regeneration, explicit
  layout for instances/wires/junctions, per-lib property overrides so output
  stays byte-identical to the reference generator).
- `templates/lib_symbols/*.sym` — verbatim KiCad library symbol extracts
  (standard parts plus power/flag symbols) used to build embedded
  `lib_symbols` sections when generating schematics by direct file editing.

For a new project, generate its golden netlist first: `verify_schematic.sh
--update-golden <schematic.kicad_sch>`, then run plain `verify_schematic.sh`
for regression checks.

## Key vocabulary

- **Symbol** — a logical component such as a resistor, capacitor, MCU, or connector.
- **Footprint** — the physical PCB land pattern associated with a symbol.
- **Net** — an electrically connected set of pins and connections.
- **Lib identifier** — `"LibraryNickname:SymbolName"` (e.g. `"Device:R"`).
- **Reference designator** — instance label such as `R1`, `C3`, or `U2`.
- **Pin** — logical connection point identified by its pin number.
- **UUID** — unique identifier used to identify schematic objects and instances.

## Project structure

```text
project.kicad_pro          # project file
project.kicad_sch          # root schematic
project.kicad_pcb          # PCB, generated/edited separately
```

A root schematic is normally located at the project root.

Hierarchical sub-sheets are separate `.kicad_sch` files referenced by sheet blocks.

## The `.kicad_sch` file format

A `.kicad_sch` file is an s-expression tree.

Coordinates use KiCad's internal schematic coordinate system. Keep objects aligned to the schematic grid where practical.

### Top-level structure

```text
(kicad_sch
  (version ...)
  (generator "eeschema")
  (uuid "...")
  (paper "A4")

  (title_block
    (title "My Board")
    (date "2025-03-05")
    (rev "1.0")
    (company "ACME")
  )

  (lib_symbols ...)

  (junction ...)
  (no_connect ...)
  (wire ...)
  (bus ...)
  (bus_entry ...)

  (label ...)
  (global_label ...)
  (hierarchical_label ...)

  (text ...)

  (symbol ...)
  (sheet_instances ...)
)
```

Do not blindly copy a schematic header from an older KiCad version. Preserve the format version and structure of the existing file when editing it.

## Embedded library symbols

KiCad embeds the symbol definitions used by a schematic in its `lib_symbols` section.

A placed symbol refers to its library identifier:

```text
(lib_id "Device:R")
```

The corresponding symbol definition must exist in `lib_symbols`.

Prefer standard KiCad library symbols such as:

```text
Device:R
Device:C
Device:LED
Connector_Generic:Conn_01x02
```

Use custom symbol definitions only when necessary.

Keep the `Footprint` property empty unless a specific footprint is known and intentionally assigned.

## Placed symbol instances

A placed symbol contains information including:

- library identifier
- position and orientation
- unit
- UUID
- properties
- pin UUID mappings
- project instance information

Example:

```text
(symbol
  (lib_id "Device:R")
  (at 100 100 0)
  (unit 1)
  (in_bom yes)
  (on_board yes)
  (uuid "...")
  (property "Reference" "R1" ...)
  (property "Value" "10k" ...)
  (property "Footprint" "" ...)
  (pin "1" (uuid "..."))
  (pin "2" (uuid "..."))
  (instances
    (project "my_project"
      (path "/" (reference "R1") (unit 1))
    )
  )
)
```

Preserve the relationship between library pins, instance pin UUIDs, and project instances.

## Wiring and connectivity

A wire is a straight segment:

```text
(wire
  (pts
    (xy 100 100)
    (xy 120 100)
  )
  (stroke (width 0) (type default))
  (uuid "...")
)
```

Use junctions when appropriate:

```text
(junction
  (at 120 100)
  (diameter 0)
  (color 0 0 0 0)
  (uuid "...")
)
```

Connectivity can be established by:

- a wire terminating at a symbol pin
- wires sharing an endpoint
- an explicit junction
- appropriate labels
- hierarchical/global labels where applicable

Do not assume that visually crossing wires are connected. Verify the actual connectivity representation.

## Labels

- `label` — local sheet net label.
- `global_label` — project-wide/global net naming.
- `hierarchical_label` — interface between hierarchical sheets.

Use consistent names for power and signal nets, for example:

```text
VCC
GND
+3V3
SCL
SDA
UART_TX
UART_RX
```

Avoid creating multiple names for the same electrical net unless there is a deliberate reason.

## Workflow

### 1. Understand the design

Determine:

- main IC/MCU
- power rails and voltage levels
- interfaces
- connectors
- required components
- known component values
- hierarchical structure, if any

If requirements are incomplete, make reasonable assumptions and clearly identify them.

### 2. Inspect the existing project

Before modifying an existing schematic:

- inspect the project structure
- identify the root schematic
- inspect existing symbols and references
- understand existing net naming
- preserve established conventions

Do not unnecessarily rewrite or regenerate an existing schematic.

### 3. Determine the symbol list

Use standard KiCad libraries where possible.

Verify that the requested symbol actually exists before inventing a library identifier.

### 4. Place symbols

Prefer a clean, readable arrangement:

- power toward the top
- ground toward the bottom
- signal flow generally left-to-right
- consistent spacing
- aligned components
- readable references and values

Use the schematic grid consistently.

### 5. Define connectivity

Connect pins using wires and appropriate labels.

Use junctions for intentional multi-wire connections.

Use `no_connect` markers for deliberately unused pins.

### 6. Assign references

References must be unique:

```text
R1, R2, R3
C1, C2
U1
J1
```

Preserve existing references when editing unless renumbering is explicitly required.

### 7. Handle footprints carefully

Do not invent a physical footprint merely because a symbol exists.

If the package is unknown, leave the footprint unset/TBD.

### 8. Validate

Prefer KiCad tooling for validation when available.

For schematic electrical validation, use:

```bash
kicad-cli sch erc ...
```

For live inspection or interaction with the running KiCad application, use `kicad-python`.

After modifying a schematic:

1. Check the file structure.
2. Check references and UUID consistency.
3. Check connectivity.
4. Run ERC where practical.
5. Open the schematic in KiCad for visual verification when appropriate.

## Validation checklist

Before handoff:

- `.kicad_sch` structure is syntactically valid.
- Format version is appropriate for the installed KiCad version.
- All UUIDs are unique.
- Symbol library identifiers are valid.
- Required embedded symbol definitions exist.
- Symbol pin mappings are consistent.
- Reference designators are unique.
- Wires connect the intended pins.
- Junctions occur at intentional connection points.
- Labels are named consistently.
- Power nets are connected correctly.
- Intentional unused pins have appropriate `no_connect` markers.
- No accidental dangling wires or disconnected pins remain.
- ERC has been run when practical.

## Working with KiCad IPC

Use IPC when the task benefits from inspecting or manipulating the **currently running KiCad application** rather than merely editing files.

Examples include:

- inspecting the currently open schematic context
- querying schematic objects through the API
- making programmatic changes through the KiCad API
- reading back objects after modification

Use the project-provided `kicad-python` command for these operations.

Do not use deprecated SWIG-based KiCad Python bindings for new automation.

If IPC cannot connect, check that KiCad is running and that its IPC/API server is enabled according to the project setup.

## Direct file editing vs. KiCad API

Prefer **direct file editing** when:

- creating a known schematic structure
- making deterministic textual changes
- working on `.kicad_sch` data that can be validated afterward

Prefer **`kicad-cli`** when:

- running ERC
- exporting
- upgrading
- performing other supported command-line operations

Prefer **`kicad-python` / IPC** when:

- interacting with a live KiCad session
- inspecting objects through the API
- performing operations that are cumbersome or error-prone through raw s-expressions

Choose the simplest reliable method for the task.

## Safety and responsibilities

- This skill produces and modifies **schematic data**.
- Do not claim that a schematic has been electrically or physically validated merely because the file parses.
- ERC is not a substitute for engineering review.
- Do not fabricate component specifications, footprints, or electrical characteristics.
- High-voltage, safety-critical, medical, aerospace, or regulatory designs require qualified engineering review.
- Prefer opening the result in KiCad for visual inspection before fabrication or release.
