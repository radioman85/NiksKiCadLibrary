# KiCad Schematic Environment

## Required capabilities

The `kicad-schematic` skill expects access to:

- KiCad
- `kicad-cli`
- Python with the `kicad-python` package when IPC interaction is required
- KiCad IPC when live KiCad interaction is required

## Preferred commands

Use these commands when provided by the project:

    kicad
    kicad-cli
    kicad-python

Do not assume that the normal `python` command provides the KiCad Python
bindings.

## Environment verification

Verify the available tooling before relying on it:

    which kicad
    which kicad-cli
    which kicad-python

Verify the KiCad CLI:

    kicad-cli --version

Verify the KiCad Python bindings:

    kicad-python -c "import kipy; print('kipy OK')"

For IPC operations, verify that KiCad is running and then test:

    kicad-python -c "from kipy import KiCad; k=KiCad(); print(k.get_version())"

## Environment-specific setup

The repository's reference setup for VS Code + Flatpak KiCad is documented
in:

    ../../docs/environments/vscode-flatpak-kicad.md

That document describes one implementation of the required environment.

Other environments may provide the same capabilities differently.

## Tool selection

Use `kicad-cli` for command-line and batch operations such as:

- validation
- ERC
- exports
- imports
- rendering
- automated project checks
- other operations supported by the installed KiCad CLI

Use `kicad-python` for operations that require the KiCad Python API or
live interaction with a running KiCad instance through IPC.

Use direct `.kicad_sch` file manipulation when it is appropriate for
deterministic structural changes and when doing so preserves KiCad's data
model.

## Python environment

When KiCad Python functionality is required, prefer:

    kicad-python

Do not substitute:

    python

unless the project explicitly establishes that the normal Python
interpreter contains the required KiCad Python bindings.

The `kicad-python` command may be a project- or environment-specific
wrapper around a host Python virtual environment.

## KiCad IPC

KiCad IPC operations require a running KiCad instance.

Before attempting IPC operations:

1. Verify that `kicad-python` is available.
2. Verify that `kipy` can be imported.
3. Ensure that KiCad is running.
4. Establish the IPC connection.
5. Verify the KiCad version when useful.

Do not assume that IPC is available merely because `kicad-python` is
installed.

## Flatpak environment

The reference development environment uses VS Code and KiCad as Flatpaks.

In that environment, commands such as:

    kicad
    kicad-cli
    kicad-python

may be wrappers that use `flatpak-spawn --host`.

The skill must not depend on the Flatpak implementation itself.

Treat Flatpak wrappers as environment adapters rather than as components
of the skill.

## Version handling

Do not assume a specific KiCad or Python version unless the project
explicitly requires one.

Always prefer the installed and project-supported version.

The reference environment may document a version that was used for testing,
but that does not automatically make it a hard requirement.

When behavior may depend on the KiCad version, inspect the installed
version before proceeding.

## Project-specific tooling

Before performing significant work, inspect the project for additional
KiCad tooling.

Look for directories and files such as:

    tools/
    scripts/
    bin/
    Makefile
    README.md
    AGENTS.md
    CONTRIBUTING.md

If the project provides wrappers, validation scripts, generators, or other
KiCad utilities, prefer those tools when they are appropriate.

Project-specific instructions take precedence over generic assumptions in
this document.

## Environment versus skill

This file defines the environment capabilities required by the
`kicad-schematic` skill.

It does not define how the AI should design or modify schematics.

The responsibilities are separated as follows:

    ENVIRONMENT.md
        ↓
    Defines available KiCad capabilities and prerequisites
        ↓
    SKILL.md
        ↓
    Defines how the AI should work with KiCad schematics
        ↓
    tools/
        ↓
    Provides reusable executable utilities
        ↓
    Project
        ↓
    Contains the actual KiCad design

## Important rules

- Do not assume that KiCad is installed as a Flatpak.
- Do not assume that `python` is the KiCad Python environment.
- Do not assume that KiCad IPC is available without a running KiCad
  instance.
- Do not hardcode environment-specific paths when a project-provided
  command or wrapper is available.
- Inspect the project environment before using KiCad tooling.
- Prefer project-provided tooling over environment-specific assumptions.
- Validate the environment before performing operations that depend on
  specific KiCad capabilities.