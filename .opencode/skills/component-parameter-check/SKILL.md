---
name: component-parameter-check
description: Use when checking whether the components used in a KiCad design — schematic (.kicad_sch) and/or PCB (.kicad_pcb) — carry all parameters Aisler.net needs for part selection (MPN or Smart Match value), plus the DigiKey order number and Origin (country of origin) fields. A focused PCBA-readiness check of the components actually used in the design, not of library symbols; the kicad-library skill covers the same fields but with the broader goal of adding/managing library parts.
version: 0.1.0
---

# Component Parameter Check (Aisler.net / DigiKey / Origin)

You check the **parameters on the components actually used in a design** to
decide whether it is ready for PCBA ordering via Aisler.net. This is a
verification skill: you inspect the properties on the placed components in the
schematic and/or the PCB, report which required parameters are present/correct,
and flag the ones that Aisler would not be able to assign or that are missing
from the purchasing fields.

It is deliberately **narrower** than `kicad-library`: that skill adds and
manages parts in the NiksKiCadLibrary and happens to set the same fields at
add time. This skill only **checks the components used in the design**
(schematic + PCB) — **not library source symbols** (`*.kicad_sym`). Do not
reach for `kicad-library` just to run a completeness check; keep this skill
scoped to verification. If a used component fails the check because its
library symbol was never set up correctly, that is a `kicad-library` task —
hand it off.

## What is checked

For every component (symbol or footprint) three things are verified:

### 1. Aisler.net part-selection parameters

Aisler auto-assigns parts on upload when it can match the component. The rules
for a match (from the Aisler integration maintained in `kicad-library`):

- Aisler reads the component property named **`MPN`**, **`Mpn`**, **`mpn`**,
  or **`AISLER_MPN`** — any of the four spelling variants works. KiCad copies
  symbol properties into the PCB file and Aisler reads them on upload.
- Matching is **100% exact**: the property value must be the manufacturer's
  exact part number for the specific version/package. Existence of an MPN
  field alone does not guarantee a catalog match — you must sanity-check it
  against the datasheet/order code when you can.
- If no MPN field is present, Aisler falls back to the `Value` field.
- For **generic parts** (SMD resistors, capacitors, inductors and signal LEDs
  — reference prefixes `R`, `C`, `L`, `LED`) the value can instead be a
  **Smart Match string**: `"<value> <package> <rating>..."`, e.g.
  `"100R 0603 1% 100mW"` or `"0.1uF 50V X7R 0402"`. Smart Match only applies
  where Aisler lists it (R/C/L/signal LEDs), and an explicit MPN always takes
  priority. Note the resistor syntax is `100R`, not `100E`.

### 2. DigiKey order number

The **`DigiKey`** property should hold the distributor part (order) number for
the exact component, e.g. `535-13583-1-ND` or `311-10.0KCT-ND`. This is the
reference that goes into the BOM for purchasing. A value that looks like a
DigiKey number but does not end in the typical `-ND` suffix should be flagged
for confirmation.

### 3. Country of origin

The **`Origin`** property should hold the country of origin of the exact
component as an **ISO 3166 country code** (e.g. `CN`, `MY`, `TH`, `US`, `DE`),
as listed on the distributor product page or the datasheet/box. It matters for
customs and purchasing documentation.

### Optional / complementary fields (informational)

The following are also recognized and reported but not required:
`MFG` (manufacturer name), `Status` (`Preview`/`Active`/`End of Life`),
`Order Code` (distributor code), and `Datasheet` (link).

## Where the parameters live

Only the design files that carry the components actually used are checked:

- **`.kicad_sch`** — placed symbol instances; `(property "Reference" "R1" ...)`,
  `(property "Value" ...)`, `(property "MPN" ...)`, `(property "DigiKey" ...)`,
  `(property "Origin" ...)`.
- **`.kicad_pcb`** — footprints carry the fields as `(property "Name" "Value" ...)`
  plus `(fp_text reference ...)` / `(fp_text value ...)`. This is the layer
  Aisler actually reads on upload, so checking the board is the most
  authoritative check of what Aisler will see.

Aisler reads symbol properties from the PCB file on upload, so a mismatch
between the schematic and the board is meaningful: the board is the source
of truth for what Aisler receives.

Library source symbols (`*.kicad_sym`) are **not** checked — the skill is about
the components used in the design, not the library.

## Tooling

The skill ships a self-contained checker in `tools/check_component_params.py`.
Run it on one or more files or directories; directories are searched
recursively for `*.kicad_sch` and `*.kicad_pcb`.

```bash
python3 "<skill_dir>/tools/check_component_params.py" \
  board.kicad_sch board.kicad_pcb
python3 "<skill_dir>/tools/check_component_params.py" ./       # whole project
```

Options:
- `--ignore-digikey` — do not require the `DigiKey` property.
- `--ignore-origin` — do not require the `Origin` property.

The exit status is the number of components that failed a required check
(0 = everything is in order). The report marks each component per column as
`OK`, `WARN`, or `FAIL`:

- **Aisler** — `OK` = exact `MPN` present, or `Value` is a plausible Smart
  Match string for a generic `R`/`C`/`L`/`LED`; `WARN` = only a `Value`
  fallback exists (Aisler will use it, but add an exact MPN or a Smart Match
  string); `FAIL` = neither MPN nor Value.
- **DigiKey** — `OK` = present with a DigiKey-style number; `WARN` = present
  but the format looks off; `FAIL` = missing.
- **Origin** — `OK` = present as an uppercase ISO 3166 2-letter code;
  `WARN` = present but malformed/not uppercase; `FAIL` = missing.

`WARN` counts as a pass for the exit status; `FAIL` does not.

## Workflow

### 1. Gather inputs

Collect the files that define the components used in the design: the
schematic (`.kicad_sch`) and the board (`.kicad_pcb`). For a full picture,
check the schematic **and** the board — the board is what Aisler sees. Do not
scan library source symbols (`*.kicad_sym`); only placed/used components are
in scope.

### 2. Run the checker

Invoke `tools/check_component_params.py` on the gathered paths. Do not invent
data; work from what the files actually contain.

### 3. Interpret the results

Walk the report and classify the findings:

- **Aisler `FAIL` / `WARN`** — the component has no exact MPN and no Smart
  Match value. This means manual assignment on aisler.net after upload.
  For generic `R`/`C`/`L`/`LED` a Smart Match string in `Value` is acceptable.
- **DigiKey `FAIL`** — no distributor reference for the BOM. Look up the
  exact component's Digi-Key part number (search scoped to its MPN, accept a
  result only on an exact MPN/package/spec match) and propose adding it. Do
  not substitute a similar part.
- **Origin `FAIL`** — no country of origin. Take it from the same product
  page or the datasheet/box. If it is genuinely not listed for the exact part,
  say so — do not guess.
- Check the board vs. schematic: if a property exists on the schematic symbol
  but is missing on the PCB footprint, flag that the board must be re-synced
  (footprint fields refreshed) before ordering.

### 4. Fix missing values

For each failing component, offer the concrete fix:

- Set `MPN` to the manufacturer's exact part number (any of the four accepted
  spellings); it may be a hidden symbol property.
- Or, for generic `R`/`C`/`L`/`LED`, set `Value` to a Smart Match string like
  `"100R 0603 1% 100mW"`.
- Set `DigiKey` to the Digi-Key part number found for this exact component.
- Set `Origin` to the ISO 3166 code found for this exact component.

Symbol properties set in the library or on the schematic instance get copied
into the PCB footprints when the netlist/footprints are updated in KiCad — make
sure the fix is reflected in the board file, since that is what gets uploaded.

### 5. Report

Summarize per component (or as a table) which of `Aisler`, `DigiKey`,
`Origin` pass, and explicitly list every missing value with the exact field
name to add. Report the exit status / count of failing components. Note any
component where the MPN exists but an exact catalog match could not be
verified on Aisler — the user must confirm those manually.

## Rules and boundaries

- **Do not guess**: an MPN, DigiKey number, or Origin code is always taken
  from the component's own datasheet/product page or the files themselves.
  Never invent or copy a value from a similar part.
- **Exact only**: for non-generic parts a `Value` fallback is not enough for
  Aisler auto-assignment — only an exact MPN works (Smart Match is only valid
  for generic `R`/`C`/`L`/`LED`).
- **Verification, not library management**: this skill checks the readiness of
  the components used in the design. If a used component fails because its
  library symbol was never set up correctly, that is a `kicad-library` task —
  hand off rather than expanding this skill's scope. Library symbols are never
  the target; only schematic/PCB components are.
- The tool's parse is not a substitute for a human check of whether an MPN
  actually resolves in the Aisler catalog.

## Verification checklist

Before handoff:

- Ran the checker over the schematic and (if present) the board.
- Each component has `Aisler` OK (exact `MPN`, or a Smart Match `Value` for a
  generic `R`/`C`/`L`/`LED`), `DigiKey` OK, and `Origin` OK — or the missing
  ones were reported explicitly with the exact fix (field name + value).
- Any DigiKey/Origin value was sourced for the exact component, not assumed.
- Board-level findings were reconciled against the schematic (PCB is the
  source of truth for Aisler).
- Checked only used components (schematic/PCB); no library symbols scanned.
- Reported the count/exit status of failing components to the user.