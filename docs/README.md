# CVAS Documentation Map

This directory keeps design docs, schema contracts, checked-in viewer outputs,
and historical plans for CVAS. Use this page as the index before opening the
larger README or generated HTML artifacts.

## Start Here

- `../README.md`: user-facing overview, quick start, CLI examples, JSON shape,
  troubleshooting, and roadmap.
- `../requirements.md`: environment setup, Python dependencies, GCC notes, and
  validation commands.
- `maintenance.md`: how to refresh checked-in sample outputs, where old outputs
  are backed up, and which verification commands should run before a PR.

## Current Design Contracts

- `cvas_datapath_pipeline_design.md`: live datapath, pipeline, JSON, and viewer
  contract for Schema v3.
- `full_mode_cpp_design.md`: current `fast` and `full` analysis-mode behavior,
  C/C++ full-mode boundaries, GCC dump metadata, and hardening checklist.
- `schema/cvas-schema-v3.md`: field-level notes for the Schema v3 JSON
  contract.
- `schema/cvas.schema.v3.json`: formal JSON Schema v3 file.

## Checked-In Outputs

The current sample artifacts are generated from `test_examples.c`:

- `test_examples_output_fast.json`
- `test_examples_output_fast.html`
- `test_examples_output_full.json`
- `test_examples_output_full.html`

These files are generated artifacts but intentionally checked in because they
serve as reviewable examples and offline viewer fixtures. Do not hand-edit
them. Regenerate them with the commands in `maintenance.md`.

Older generated outputs live under `backup/`. Each backup folder should explain
which refresh it preserves.

## Historical Plans

The `plans/` directory records implementation plans and handoff notes. Treat
these as history unless a plan explicitly says it is still active. Current
behavior should be verified against `README.md`, `cvas_datapath_pipeline_design.md`,
`full_mode_cpp_design.md`, tests, and the checked-in sample outputs.
