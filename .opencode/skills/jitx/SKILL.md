---
name: jitx
description: Specialist reference for JITX syntax, component definitions, schematic modules, and net-first connectivity. Use when reading, parsing, or translating JITX code into schematic generators — especially when targeting the KiCad schematic IR or emitting `.kicad_sch` files through the kicad-schematic skill.
version: 0.1.0
---

# JITX Language Specialist

You understand JITX source code — a domain-specific language built on Stanza for electronic design automation. You help specialists write schematic generators that translate JITX descriptions into KiCad schematic files (via the project's JITX-inspired IR and emitter).

## When to use this skill

- Parsing or reading JITX source (`.stanza`, `.jitx`) to extract component definitions, modules, and connectivity.
- Translating JITX `pcb-component` and `pcb-module` definitions into the project's net-first schematic IR (`tools/schematic_ir.py`).
- Building a generator that consumes JITX and emits KiCad `.kicad_sch` files.
- Understanding JITX pin kinds, net declarations, instances, ports, and constraints so they map correctly to KiCad symbols, wires, and labels.
- Needing to know the exact syntax and semantics of JITX constructs before writing a transpiler or adapter.

## What JITX is

JITX is a hardware description language. It treats connectivity as a first-class, declarative construct: nets are named and explicitly declared, not inferred from graphical wire geometry.

- Built on **Stanza** — a statically typed, garbage-collected language with s-expression-like syntax.
- Source files typically use `.stanza` or `.jitx` extensions.
- Key paradigms: **net-first connectivity**, **component reuse via instances**, **hierarchical modules with ports**, and **design constraints**.

## Top-level program structure

A JITX design is a Stanza program that imports libraries and defines components and modules:

```stanza
defpackage my-design:
  import jitx
  import jitx/commands
  import ocdb/utils/landpatterns
  import ocdb/utils/symbols

; --- component definitions ---

pcb-component my-led :
  ...

; --- module definitions ---

pcb-module my-circuit :
  ...

; --- main entry point ---

public defn main () :
  val board = my-circuit()
  view-schematic(board)
```

The generator specialist is mainly concerned with `pcb-component` and `pcb-module` bodies, because those contain the circuit structure.

---

## Component definition (`pcb-component`)

A component is a reusable part definition. It contains pins, properties, and references to symbol/footprint resources.

```stanza
pcb-component my-resistor :
  name = "1k 0603 Resistor"
  description = "1k ohm, 1%, 0603 thick film resistor"
  manufacturer = "Yageo"
  mpn = "RC0603FR-071KL"
  reference-prefix = "R"

  pin p[1]
  pin p[2]

  val sym = resistor-sym()
  val lp  = resistor-lp(0603)

  symbol = sym(p[1] => sym.p[1], p[2] => sym.p[2])
  landpattern = lp(p[1] => lp.p[1], p[2] => lp.p[2])
```

### Fields and properties

| Field | Type / Purpose |
|---|---|
| `name` | Human-readable component name. |
| `description` | Datasheet-style description. |
| `manufacturer` | Manufacturer string. |
| `mpn` | Manufacturer part number. |
| `reference-prefix` | Reference designator prefix (`R`, `C`, `U`, `J`, ...). |
| `pin <id>` | Declares a logical pin. Can be a symbol or index: `pin a`, `pin p[1]`. |
| `symbol = ...` | Assigns a schematic symbol and maps component pins to symbol pins. |
| `landpattern = ...` | Assigns a PCB landpattern (footprint) and maps component pins to landpattern pads. |
| `property(key, value)` / `properties` | Arbitrary key-value metadata. |

### Pin declarations

Pins define the electrical interface of the component.

```stanza
pin a
pin b
pin p[1] through p[8]   ; pin array
pin gnd : power         ; pin with electrical type annotation
pin vcc : power
pin signal : i/o
```

Pin electrical types (the generator should map these to KiCad pin types):

| JITX pin type | Typical meaning | KiCad equivalent |
|---|---|---|
| (no annotation) | `passive` | `passive` |
| `power` | power pin | `power_in` / `power_out` depending on context |
| `i/o` | bidirectional signal | `bidirectional` |
| `input` | digital input | `input` |
| `output` | digital output | `output` |
| `analog` | analog pin | `passive` or `unspecified` |

**Important:** JITX `power` pins are context-sensitive. A regulator's output `power` pin behaves as `power_out`, while a load's `power` pin behaves as `power_in`. The generator must infer direction from net-level connectivity or from annotations if present.

### Symbol and landpattern mapping

```stanza
symbol = resistor-sym()(p[1] => p[1], p[2] => p[2])
```

The `=>` operator maps component pins to symbol pins or landpattern pads. The generator needs to resolve:
- Symbol pin numbers / names (for KiCad `lib_symbols`).
- Pin geometry offsets (for schematic IR `Pin.offset`).
- Landpattern names (for KiCad `Footprint` property).

---

## Module definition (`pcb-module`)

A module is a schematic sheet. It contains instances, nets, ports, and optionally sub-modules (hierarchy).

```stanza
pcb-module my-led-circuit :
  inst R1 : my-resistor
  inst LED1 : my-led
  inst J1 : my-connector

  net VCC (J1.p[1], R1.p[1])
  net GND (J1.p[2], LED1.cathode)
  net LED_NET (R1.p[2], LED1.anode)
```

### Instances (`inst`)

An instance places a component or sub-module into the module.

```stanza
inst R1 : my-resistor          ; single instance
inst R2 : my-resistor
inst U1 : my-mcu
inst sub : my-sub-module       ; hierarchical instance
```

- Reference designators may be explicit or auto-generated.
- In JITX they are often auto-assigned during compilation, but source may contain explicit names.
- For the generator: treat the instance name as the reference unless overridden.

### Nets (`net`)

Nets are the heart of JITX. They are declared explicitly by name and connect pin endpoints.

```stanza
net VCC (U1.VDD, R1.p[1], C1.p[1])
net GND (U1.VSS, C1.p[2])
net UART_TX (U1.TX, J1.p[3])
```

- Same net name = same electrical net (global within the module).
- Ports and hierarchical labels expand the visibility across module boundaries.
- Unnamed point-to-point connections may use `make-net` or wire expressions.

**Generator rule:** every `net` statement becomes one entry in the IR `Net` list (or one `net()` call in the IR builder).

#### Anonymous nets

JITX allows connections without an explicit net name:

```stanza
net (U1.TX, J1.p[3])           ; auto-generated net name
```

The generator should assign stable generated names such as `Net-(U1-TX)` so golden diffs remain deterministic.

### Ports (`port`)

Ports expose a module's interface to its parent.

```stanza
pcb-module sub-oscillator :
  port clk-out : pin
  port enable  : pin
  inst xtal : my-crystal
  inst inv  : my-inverter
  net (inv.in, xtal.out)
  net (inv.out, clk-out)
```

- Ports are pins on the module boundary.
- In KiCad hierarchical terms, a `port` becomes a `hierarchical_label` (or a sheet pin).
- The generator must track port direction if annotated: `pin` is generic, but `input`, `output`, or `power` may be specified.

### Hierarchy

A module instantiated inside another module becomes a hierarchical sheet.

```stanza
pcb-module top :
  inst power : power-supply-module
  inst logic : logic-module
  net VCC (power.vcc, logic.vcc)
  net GND (power.gnd, logic.gnd)
```

In KiCad:
- Each module instance with internal structure becomes a `.kicad_sch` sub-sheet.
- Ports become hierarchical labels / sheet pins.
- Nets that cross the boundary are linked by matching hierarchical label names.

---

## Connectivity and constraints

### `supports` — component variation

JITX components can declare `supports` clauses for parametric selection or variant design:

```stanza
pcb-component my-capacitor :
  pin a
  pin b
  supports capacitance:
    100.0e-9 => { name = "100nF" }
    1.0e-6   => { name = "1uF" }
```

The generator should treat a resolved instance as a concrete component with a specific value. Unresolved `supports` require the generator to pick a default or error.

### `require` — constraints in modules

```stanza
require cap-voltage >= 16.0 from C1
```

These are design-time constraints. They affect part selection, not connectivity. The generator may ignore pure `require` constraints unless it is also doing BOM generation.

### `place` and physical constraints

```stanza
place(C1) at loc(10.0, 20.0) on Top
place(U1) relative-to R1 at loc(5.0, 0.0)
```

Placement directives are layout/PCB concerns. For schematic generation they can be ignored, unless the generator wants to hint at instance positions in the schematic IR (`Instance.at`).

---

## Mapping JITX to the project KiCad schematic IR

The project provides a JITX-inspired intermediate representation (`tools/schematic_ir.py`) and an emitter (`tools/schematic_emitter.py`) that renders to `.kicad_sch`. Use this mapping when building a translator.

### Component → `Component`

| JITX | IR (`schematic_ir.py`) |
|---|---|
| `pcb-component <name>` | `Component.lib_id` (map to KiCad lib identifier, e.g. `"Device:R"`) |
| `pin p[N]` | `Pin(num=N, name=..., kind=..., offset=...)` |
| `symbol = ...` | Source for pin offsets and property positions (parsed from `.sym` template or KiCad lib) |
| `reference-prefix` | Used to auto-prefix reference designators if the source does not specify them explicitly |
| `mpn` / `manufacturer` | Can become `Component` metadata or KiCad properties if desired |

### Instance → `Instance`

| JITX | IR |
|---|---|
| `inst R1 : my-resistor` | `Instance(ref="R1", lib_id="Device:R", value="100R", ...)` |
| Instance name | `Instance.ref` (must be unique across the design) |
| Component value | `Instance.value` (resolved from `supports`, explicit `property`, or annotation) |

### Net → `Net`

| JITX | IR |
|---|---|
| `net VCC (R1.p[1], U1.VDD)` | `Net(name="VCC", endpoints=[("R1","1"),("U1","VDD")])` |
| Anonymous `net (...)` | Generate stable name: `Net-(<ref1>-<pin1>)` |
| Power rail nets (`VCC`, `GND`, `+5V`, `+3V3`) | Keep name as-is; IR checker auto-adds `PWR_FLAG` if the net has `power_in` but no `power_out` |

### Module → `Sheet` / `Design`

| JITX | IR / KiCad |
|---|---|
| `pcb-module <name>` | `Sheet(name="<name>")` |
| `inst sub : my-sub-module` | Hierarchical sheet instance; each unique sub-module type gets its own `.kicad_sch` file |
| `port <name> : pin` | `hierarchical_label` with direction inferred from usage or annotation |
| Top-level module | `Design(sheets=[...])` root sheet |

### Pin kind mapping (critical for IR `check()`)

| JITX source hint | IR `Pin.kind` | KiCad ERC implication |
|---|---|---|
| `pin ... : power` on a load / connector | `power_in` | Net needs a `power_out` source or `PWR_FLAG` |
| `pin ... : power` on a regulator / source | `power_out` | Drives the net |
| No annotation | `passive` | Standard passive connection |
| `input` | `input` | Driver expected |
| `output` | `output` | Drives signal |
| `i/o` | `bidirectional` | Either direction |

---

## Step-by-step translation workflow

When writing a JITX → KiCad schematic generator:

1. **Parse** the JITX source (Stanza s-expressions) into an AST.
2. **Resolve** each `pcb-component`:
   - Extract pins, their names, and electrical types.
   - Resolve symbol/landpattern references to the project's `templates/lib_symbols/*.sym` or an external KiCad library via the `Component` adapter.
3. **Build the IR `Component` map** (`parts: lib_id → Component`).
4. **Walk module instances**:
   - For each `inst`, create an `Instance` with a unique reference.
   - If the instance's component is another `pcb-module`, create a hierarchical `Sheet` instead of a symbol instance.
5. **Walk net declarations**:
   - For each `net`, create a `Net` with named endpoints `(ref, pin)`.
   - Merge nets that share the same name.
   - Auto-name anonymous nets deterministically.
6. **Infer pin kinds** for power pins if not explicitly annotated (use net-level heuristics: a net named `VCC`/`GND`/`+5V` connected to a generic `power` pin implies `power_in` unless the component is known to be a source).
7. **Run `check(design, parts)`** (from `schematic_ir.py`) before emission to catch duplicates, dangling pins, missing power sources, etc.
8. **Emit** via `schematic_emitter.py` (or the project's equivalent) to produce a deterministic `.kicad_sch`.
9. **Validate** with `verify_schematic.sh` (ERC + golden netlist diff) from the `kicad-schematic` skill.

---

## Key syntax reference

### Stanza/JITX literals and types

```stanza
; Numbers
42          ; Int
3.14        ; Double
100.0e-9    ; Double (scientific)

; Strings
"hello"     ; String literal

; Vectors / tuples
[1 2 3]     ; Vector of Int
{1 2 3}     ; Tuple

; Records / structs
{ a = 1, b = 2 }

; Function calls
my-function(arg1, arg2)
```

### Common JITX operators and keywords

| Construct | Meaning |
|---|---|
| `=>` | Pin mapping (component pin → symbol pin / landpattern pad). |
| `through` | Range generator, used in pin arrays: `p[1] through p[8]`. |
| `net` | Declares a named net and its connected endpoints. |
| `make-net` | Creates a net object programmatically. |
| `inst` | Instantiates a component or module. |
| `port` | Declares a module-level port (hierarchical interface). |
| `place` | Physical placement constraint (PCB layout). |
| `require` | Design constraint assertion. |
| `supports` | Component parameter / variant declaration. |
| `view-schematic` | JITX built-in to visualize the module. |
| `loc(x, y)` | Location value for placement. |

---

## Safety and responsibilities

- JITX source may contain unresolved parametric components (`supports`). A generator must either resolve them to concrete values or emit a clear error.
- Do not invent KiCad library identifiers (`Device:R`, `Device:C`, ...). Verify they exist or add the corresponding `.sym` template to `templates/lib_symbols/`.
- Power semantics matter: `power_in` without `power_out` causes ERC failures. The IR checker catches this before KiCad sees the file.
- Hierarchical designs require consistent port names across sheet boundaries. A mismatch between a child's `port clk-out` and the parent's `net` reference will break connectivity.
- The IR and emitter produce deterministic output (uuid5 seeds). Do not mix non-deterministic UUID generation into the pipeline.
- This skill covers syntax and translation. Electrical validation, ERC, and manufacturing review are handled by the `kicad-schematic` skill and human engineers.

---

## Relationship to other skills

- **`kicad-schematic`** — consumes the emitted `.kicad_sch` and provides ERC, verification, and live KiCad interaction. The JITX skill feeds into it.
- **`schematic_ir.py`** / **`schematic_emitter.py`** — the project's reference IR and emitter. JITX constructs map directly to these types.
- **`ir-design.md`** — design rationale for the IR. Read it to understand why JITX's net-first model was adopted.

## Generator checklist

Before handoff of a JITX → KiCad generator:

- [ ] All `pcb-component` definitions are parsed and mapped to IR `Component` objects.
- [ ] All `pin` declarations produce correct `Pin` entries with `kind`, `num`, and `name`.
- [ ] Power pin directions are inferred or explicitly annotated (`power_in` vs `power_out`).
- [ ] All `inst` statements produce unique `Instance.ref` values.
- [ ] All `net` declarations are captured as IR `Net` objects with `(ref, pin)` endpoints.
- [ ] Anonymous nets receive stable generated names.
- [ ] Hierarchical modules (`pcb-module` containing `port`) map to sub-sheets and hierarchical labels.
- [ ] `check(design, parts)` passes with zero errors before emission.
- [ ] Emitted `.kicad_sch` is byte-stable across multiple runs.
- [ ] `verify_schematic.sh` (lint + ERC + golden diff) passes.
