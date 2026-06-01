# Viewer E2E, Pipeline Metadata, and Edge Density Plan

## Goal

Turn the follow-up ideas from the sample C-model handoff into testable viewer
design contracts:

1. Browser E2E automation can verify Sequence import/export without relying on
   native file pickers or download dialogs.
2. Pipeline stage order can consume explicit JSON stage metadata when available,
   while keeping the current function-name heuristic as a fallback.
3. Large Sequence boards can reduce edge clutter in pipeline mode with density
   presets and an optional selected-stage focus.

## TDD Contract

### 1. Viewer E2E hooks

Add a small, browser-only DOM test hook when the viewer is opened with
`?test-hooks=1`:

- `#cvas-test-map-input`
- `#cvas-test-map-output`
- `#cvas-test-export-map`
- `#cvas-test-import-map`

The production UI still uses file download and file picker controls. The hooks
let Browser/Playwright tests exercise the same serialization and import code
paths using textarea JSON payloads and normal button clicks. A DOM hook is used
instead of a `window` object because some browser automation contexts run with
non-extensible global objects.
For browser runners without a virtual clipboard, the import button accepts the
output textarea as a fallback payload when the input textarea is empty, enabling
an export-then-import round trip with button clicks only.

### 2. Explicit pipeline stage metadata

Support optional `flow.pipeline_stages.items[]` entries:

- `block_id` or `function`
- `stage` or `stage_number`
- optional `label` or `stage_label`
- optional `role` or `lane_role` (`lane`, `join`, `final`, `utility`)

The viewer uses explicit metadata first and falls back to function names such as
`bpc_stage3_*`. This keeps existing sample behavior unchanged while allowing
future generators to emit stable stage facts.

### 3. Edge density controls

Add Sequence edge visibility state to the exported map:

- `sequence_edge_density_mode`: `all`, `stage_local`, or `selected_stage`
- `sequence_stage_filter`: `all` or a stage number string

In pipeline order:

- `all` keeps the existing behavior.
- `stage_local` shows only edges whose endpoints are in the same explicit or
  inferred pipeline stage.
- `selected_stage` shows only edges touching the selected stage.

Non-pipeline layouts keep existing edge behavior.

## Implementation Steps

1. Add failing viewer compatibility tests for the hooks, explicit metadata, and
   serialized edge-density state.
2. Extend `build_sequence_execution_model()` to read
   `flow.pipeline_stages.items[]` and annotate pipeline steps with stage source,
   label, and lane role.
3. Add JS state, controls, filtering helpers, and test hooks in
   `json_to_html.py`.
4. Run targeted viewer tests, regenerate checked-in sample HTML artifacts, then
   run full validation.

## Boundaries

This is still a static visualization layer. Explicit stage metadata improves
layout stability, but it does not by itself imply a runtime schedule, HLS
schedule, initiation interval proof, or cycle-accurate pipeline.
