---
name: kicad-library
description: Adds and manages components (symbols, footprints, 3D models) in the NiksKiCadLibrary designator-based KiCad library. Use when a user provides a new part folder or zip (vendor / SnapEDA KiCad files) and wants it added to the library, asks to add a symbol (.kicad_sym), footprint (.kicad_mod), or 3D model (.step/.stp), or asks about the designator-based symbol library layout, reference designator prefixes, or library tables (sym-lib-table).
version: 0.1.0
---

# KiCad Library Specialist (NiksKiCadLibrary)

You add, verify, and organize components in the user's **NiksKiCadLibrary**.
The library is split into one symbol library **per reference-designator prefix**
plus a single footprint library and a single 3D-models folder. New parts go
into the designator library matching the component type — never into the old
monolithic file.

## Library location and layout

Root: `/home/nik/kDrive/Electronics/PCB_Projects/NiksKiCadLibrary`

```text
NiksKiCadLibrary/
├── <DES>/<DES>.kicad_sym          # one symbol library per designator (26 exist)
├── NiksKiCadLibrary.pretty/       # single footprint library (*.kicad_mod)
├── NiksKiCadLibrary.3dshapes/     # 3D models, referenced by bare filename
├── NiksKiCadLibrary.kicad_sym     # OBSOLETE monolith — kept as reference only.
│                                  #   DO NOT add new parts here.
└── TPSM86837RCGR/                 # vendor data — leave alone
```

The designator libraries are registered globally in KiCad as `Niks_<DES>` in
`~/.var/app/org.kicad.KiCad/config/kicad/10.0/sym-lib-table`. The footprint
library is registered as `NiksKiCadLibrary` in the same directory's
`fp-lib-table`. The `.3dshapes` folder needs no registration.

## Reference designators (the 26 libraries)

| DES | Kind | DES | Kind | DES | Kind | DES | Kind |
|-----|------|-----|------|-----|------|-----|------|
| R | Resistor | Q | Transistor (BJT/FET/MOSFET) | SW | Switch (incl. tactile) | LED | LED |
| C | Capacitor | D | Diode (Zener, LED) | K | Relay | DS | Display |
| L | Inductor | U | IC / MCU | M | Motor | LAMP | Lamp/Bulb |
| FB | Ferrite Bead | TR | Transformer | J | Connector/Jack | RT | Thermistor |
| POT | Potentiometer | | | X | Crystal/Resonator | S | Sensor |
| F | Fuse | | | TP | Test Point | MOD | Module |
| | | | | B | Battery | FL | Filter |
| | | | | P | Plug/Pin | ANT | Antenna |

**Normalization rules** (apply when a source file has a non-standard prefix):
`IC` → `U`; `MOSFET` → `Q`; `?`/`U?` → `U`; `S` on a switch → `SW`;
rotary encoders → `SW`; `TP2`/labeled test points → `TP`. When in doubt, host
of the ambiguous cases are switches (`SW`) or ICs (`U`).

## Aisler.net (PCBA) parameters

Parts are ordered through **Aisler.net** for Assembly. Aisler needs the correct
**MPN** to auto-assign parts from their catalog; a component without a matching
MPN must be assigned manually on their site after upload.

Rules that apply to every symbol we maintain:

- Aisler reads the MPN from the symbol property named **`MPN`**, **`Mpn`**,
  **`mpn`**, or **`AISLER_MPN`** (all four are accepted). KiCad copies symbol
  properties into the PCB file and Aisler reads them on upload.
- Matching is **100% exact** — the property value must be the manufacturer's
  exact part number for the specific version/package. If there is no exact
  catalog match Aisler leaves the part unassigned.
- If no MPN field is present, Aisler falls back to the `Value` field.
- For **generic parts** (SMD resistors, capacitors, inductors and signal LEDs)
  the **value can be a Smart Match string**. Aisler then picks a suitable part
  from a trusted manufacturer. Pattern: `"<value> <package> <rating>..."`
  e.g. `"100R 0603 1% 100mW"` or `"0.1uF 50V X7R 0402"`. Note the resistor
  syntax is `100R`, not `100E`. Smart Match only applies where Aisler lists it
  (R/C/L/signal LEDs) — an explicit MPN still takes priority.
- Recommended complementary properties (optional): `MFG` (manufacturer name),
  `Status` (`Preview`/`Active`/`End of Life`), `Order Code` (distributor code),
  and the standard `Datasheet` link.

The `tools/add_part.py` tool supports setting these at add time:

- `--mpn <EXACT-PART-NUMBER>` — sets the `MPN` property. If the source already
  has an MPN alias (`MPN`/`Mpn`/`mpn`/`AISLER_MPN`) its value is updated in
  place, otherwise a hidden `MPN` property is inserted.
- `--mfr <MANUFACTURER>` — sets the `MFG` property.
- `--assume-mpn` — inserts `MPN` = part-number symbol name when the source has
  no MPN field (only do this when the symbol name really is the exact MPN).

When in doubt after adding a part, verify an MPN field exists and matches the
manufacturer datasheet/order code exactly.

## Distributor data (DigiKey)

Every part we add should carry the **Digi-Key part (order) number** in a
`DigiKey` property (e.g. `535-13583-1-ND`). This is the distributor reference
that goes into the BOM for purchasing.

Lookup procedure — this search is scoped to **exactly the component being
added**, not a broad catalog query:

1. Use the component's exact **MPN** (the same value that goes in the `MPN`
   field) as the only search term. Do not add generic keywords.
2. Search DigiKey (e.g. the product search at `digikey.com`, or the DigiKey
   Product Search API with the user's key if available/the user prefers it).
   Prefer the stable product-search URL form
   `https://www.digikey.com/en/products/result?keywords=<MPN>`.
3. From the results, accept a part **only if its manufacturer part number
   equals ours exactly** (identical variant, package, and specs). Do not pick
   the first row blindly.
4. Take that part's **Digi-Key Part Number** (DK #, usually ending in `-ND`,
   e.g. `535-13583-1-ND`) and set it as the `DigiKey` property value. Note the
   **Country of Origin** shown on the same product page for the `Origin`
   property (see the *Country of origin* section).
5. If no exact match exists (end-of-life part, or not stocked by DigiKey),
   leave the `DigiKey` field unset and tell the user the part could not be
   found on DigiKey — do not substitute a similar part without asking.

The `tools/add_part.py` tool stores the reference with `--digikey <DK#>`
(e.g. `--digikey 535-13583-1-ND`). It sets or updates the hidden `DigiKey`
property and reports it after the run.

### Building a Digi-Key order list (upload file)

`tools/digikey_order.py` turns a design into a Digi-Key upload file, so the user
never has to assemble the order list by hand:

```bash
python3 "<skill_dir>/tools/digikey_order.py" board.kicad_pcb --out-dir order/
```

It reads the components actually used in the design (PCB footprints; or
`--source sch` for the placed symbols of every sheet in the hierarchy), takes
the `DigiKey` property (any spelling: `DigiKey`, `Digi-Key PN`,
`Digi-Key Part Number`, …), merges equal parts into one order line with the
summed quantity and writes four files:

| File | Purpose |
|------|---------|
| `digikey_upload.csv` | Digi-Key list upload — `Digi-Key Part Number,Customer Reference,Quantity` (`--with-mpn` adds manufacturer columns) |
| `digikey_list.txt` | one part number per line, for pasting into the cart |
| `digikey_bom.csv` | full BOM incl. Value, Footprint, MPN, Origin |
| `digikey_missing.csv` | parts with **no** Digi-Key number — the work list for step 4 of the lookup procedure above |

Skipped: footprints/symbols flagged *exclude from BOM*, DNP symbols, power
symbols and references like `REF**`. Options: `--generic-map FILE` fills
generic R/C/L parts from a `Value,Footprint,DigiKey` CSV, `--multiplier 2`
doubles quantities for two boards, `--strict` exits non-zero while parts are
missing, `--source pcb|sch|auto` picks the input.

The tool never invents a part number: parts without a `DigiKey` property are
only reported, and their DK numbers have to be looked up and written back into
the symbol/footprint (then re-run).

## Country of origin (`Origin`)

Every part we add should also carry the **country of origin** in an `Origin`
property — an ISO 3166 country code (e.g. `CN`, `MY`, `TH`, `US`, `DE`) as
listed on the distributor product page (DigiKey shows *Country of Origin* on
the product detail page) or on the manufacturer datasheet/box. This is
relevant for customs and purchasing documentation.

- Record the value for **exactly the component being added** — from its own
  product page or datasheet, not a similar part.
- If the country is not listed anywhere for the exact part, leave `Origin`
  unset and say so; do not guess.
- The `tools/add_part.py` tool stores it with `--origin <code>`
  (e.g. `--origin CN`).

## File format conventions

### Symbol library (`<DES>/<DES>.kicad_sym`)

Header is the current KiCad 10 generator header. Top-level symbols are
indented with **one tab**; everything in the repo uses **tabs**.

```text
(kicad_symbol_lib
	(version 20251024)
	(generator "kicad_symbol_editor")
	(generator_version "10.0")
	(symbol "PART_NUMBER"
		(property "Reference" "DES" ...)
		(property "Value" "PART_NUMBER" ...)
		(property "Footprint" "NiksKiCadLibrary:FOOTPRINT_NAME" ...)
		...
		(symbol "PART_NUMBER_0_0" ... pins/graphics ...)
	)
)
```

Rules:
- Symbol **names keep the part number** (e.g. `NRF5340-QKAA-R7`).
- `Reference` property = the designator of the library it lives in.
- `Footprint` property always points at **`NiksKiCadLibrary:<footprint>`** —
  the footprint library stays monolithic, it is not split by type.
- Multi-unit symbols use `PART_NUMBER_1_1`, `PART_NUMBER_2_1`, … sub-blocks.

### Footprint library (`NiksKiCadLibrary.pretty/<name>.kicad_mod`)

```text
(footprint "FOOTPRINT_NAME"
	(version 20241229)
	(generator "pcbnew")
	(descr "...")
	(attr smd | through_hole)
	(fp_text reference "REF**" ...)
	(fp_text value "FOOTPRINT_NAME" ...)
	(pad ...)
	(fp_line ...)
	(embedded_fonts no)
	(model "MODEL.step"
		(offset (xyz 0 0 0))
		(scale (xyz 1 1 1))
		(rotate (xyz 0 0 0))
	)
)
```

### 3D models (`NiksKiCadLibrary.3dshapes/`)

Copy the `.step`/`.stp` here and reference it from the footprint by **bare
filename** (no path). KiCad resolves it from the sibling `.3dshapes` folder,
so 3D previews work without configuring extra search paths.

## When to use this skill

- A vendor/SnapEDA download (`*.kicad_sym`, `*.kicad_mod`, `*.step`) needs to
  be added to the library.
- The user asks to add a new symbol, footprint, or 3D model.
- The user asks what designator a component should use, or how the library is
  organized.
- The user asks to fix the reference prefix of an existing symbol.
- The user wants to **order** the components of a design — run
  `tools/digikey_order.py` (see *Building a Digi-Key order list*) to produce the
  Digi-Key upload file and the list of parts still missing a Digi-Key number.

## Workflow: add a new part

### 1. Determine the designator

Identify what the component is (inductor, MCU, switch, …) and map it to a
designator using the table above. Apply the normalization rules. This decides
the target library `<DES>/<DES>.kicad_sym`.

### 2. Inspect the source files

List the folder the user gave. A typical part ships with one `.kicad_sym`,
one `.kicad_mod`, and one `.step`/`.stp`. Note the exact symbol name, footprint
name, and model name. Confirm no symbol with the same part number already
exists in the target library (duplicates are rejected by the tool).

### 3. Run the provided tool

Prefer the bundled `tools/add_part.py` for the mechanical file editing — it
handles old-format (KiCad 6/7) vendor files, converts indentation, updates the
`Reference` and `Footprint` properties, adds the `(model ...)` block, copies
the 3D model, registers a brand-new designator library in `sym-lib-table`, and
verifies balanced parentheses:

```bash
python3 "<skill_dir>/tools/add_part.py" \
  --lib /home/nik/kDrive/Electronics/PCB_Projects/NiksKiCadLibrary \
  --designator L \
  --mpn ASPI-0628-3R9M-T1 \
  --mfr Abracon \
  --digikey 535-13583-1-ND \
  --origin CN
  /home/nik/Downloads/ASPI_0628_3R9M_T1
```

Options: `--footprint NAME` to force the footprint name, `--model FILE` to
pick a specific 3D model, `--sym-lib-table PATH` if the KiCad table is not at
the default location, `--mpn <exact MPN>` / `--mfr <manufacturer>` /
`--assume-mpn` for the Aisler fields, and `--digikey <DK#>` for the DigiKey
part number described below. The designator must match what the part is — e.g.
`--designator L` for an inductor, `--designator SW` for a tactile switch.

Look up the Digi-Key number yourself per the *Distributor data (DigiKey)*
section (search scoped to the exact MPN, exact-match check, no substitution)
and pass `--digikey` with the result. Pick up the *Country of Origin* from the
same product page and pass it with `--origin` (see the *Country of origin*
section); leave it out if it is not listed for the exact part.

### 4. Review the result

- The symbol is in `<DES>/<DES>.kicad_sym` with `Reference` = designator and
  `Footprint` = `NiksKiCadLibrary:<name>`.
- The footprint is in `NiksKiCadLibrary.pretty/` with a `(model "...")` block
  whose bare filename exists in `NiksKiCadLibrary.3dshapes/`.
- No duplicate part numbers; s-exprs are balanced.
- An **MPN** property exists (any of `MPN`/`Mpn`/`mpn`/`AISLER_MPN`) and holds
  the exact manufacturer part number, or a valid Smart Match string for generic
  R/C/L/LEDs — so Aisler.net can auto-assign the part for Assembly.
- A **`DigiKey`** property holds the Digi-Key part number found by searching
  for this exact component (unless the part is genuinely unavailable on
  DigiKey — then it was explicitly reported).
- An **`Origin`** property holds the country of origin (ISO country code) of
  this exact component, taken from its distributor page or datasheet, or is
  explicitly reported as not listed.
- If the source was unusual (multi-unit IC, power pins, non-standard graphics),
  open the touched files and sanity-check the pins and units before handoff.

### 5. Tell the user

KiCad caches library tables at startup. They must **restart KiCad** (or
reload the library in the Symbol/Footprint editor) to see the new parts.

## Manual fallback

When the tool is not a fit (e.g. hand-authored symbol with many units or
custom power-pin attributes, or the user explicitly wants manual edits), edit
the files directly following the conventions above. Work on the raw
s-expression text: preserve the existing header and tab indentation, insert
the new `\t(symbol "..." ...)` block before the library's final `)`, and keep
the `Reference`/`Footprint` properties correct. Then run the same verification.

## Creating a new designator library

Only for a genuinely new prefix (rare). Create `<NEW>/<NEW>.kicad_sym` with
the standard header, add the symbol(s), and register it in the global
`sym-lib-table` as `Niks_<NEW>` — the tool does this automatically when the
library file does not exist yet.

## Verification checklist

Before handoff:

- Target designator library exists or was created deliberately.
- Added symbol name is the part number; not duplicated anywhere else.
- `Reference` property equals the library designator.
- `Footprint` property is `NiksKiCadLibrary:<existing footprint>`.
- Footprint file is `NiksKiCadLibrary.pretty/<name>.kicad_mod`.
- 3D model file exists in `NiksKiCadLibrary.3dshapes/` and is referenced by
  bare filename in the footprint.
- MPN property is present and is the exact manufacturer part number (or a
  valid Aisler Smart Match string for generic R/C/L/LEDs).
- `DigiKey` property holds the Digi-Key part number found for this exact
  component (a genuinely unavailable part was reported to the user instead of
  substituting a similar one).
- `Origin` property holds the country of origin of this exact component, or
  the missing value was explicitly reported (no guessing).
- All touched files have balanced parentheses.
- Only touched what was necessary; the monolith `NiksKiCadLibrary.kicad_sym`
  is never modified.
- Reported to the user that KiCad must be restarted/reloaded to pick up the
  new parts.

## Safety

- Do not invent part numbers, footprints, or electrical characteristics —
  copy them from the vendor files.
- Do not modify the obsolete monolith; new parts only go into the designator
  libraries.
- File parsing (balanced parens) is not a substitute for opening the part in
  KiCad and visually checking pins, units, and 3D orientation.