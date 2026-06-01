# Sample C-model Syntax Coverage Handoff Plan

> Status: original handoff plus follow-up completion notes. The unresolved
> artifact synchronization and full-mode determinism items were completed after
> this handoff by normalizing `gcc_dump.command` metadata, regenerating the
> checked-in fast/full sample artifacts, and browser-checking the Diagram and
> Sequence tabs.

**Goal:** Expand the checked-in CVAS sample inputs so they exercise a deeper, more realistic C-model pipeline and add a viewer layout that can show stage-named helpers as parallel pipeline lanes.

**Base:** `origin/main` at `b2fb224 feat: add sequence call-order viewer controls (#62)`.

**Working branch:** `feat/sample-cmodel-syntax-coverage`.

## Follow-up completion notes

- Full-mode JSON determinism policy: `src/cvas_gcc_dump.py` still executes GCC
  with the real local argv, but serializes `gcc_dump.command` as a normalized
  diagnostic display string with `command_path_policy: "normalized"`. GCC dump
  temp directories and local source checkout paths no longer enter checked-in
  full-mode JSON/HTML sample artifacts.
- Sample artifacts were regenerated from the final generator:
  `docs/test_examples_output_fast.html`,
  `docs/test_examples_output_fast.json`,
  `docs/test_examples_output_full.html`, and
  `docs/test_examples_output_full.json`.
- Contract coverage now includes a checked-in full-sample assertion that rejects
  `/tmp/cvas-gcc-dump-*` and local repo paths in `gcc_dump.command`.
- The remaining pipeline layout boundary is intentional: `Pipeline stage order`
  remains a static name-derived visualization, not a runtime, HLS, or
  cycle-accurate schedule.

---

## Current design intent

### 1. Keep the main sample C-compatible

`test_examples.c` was expanded from a small BPC sample into a larger C-only BPC-like pipeline. The intent is to keep the default sample parseable by the normal fast path while covering more C syntax and datapath-like structure.

Key sample properties now present:

- typedefs, structs, enums, anonymous enum constants, fixed-size arrays, and 2D arrays
- function-pointer-style array parameter syntax such as `int (*window)[BPC_KERNEL_SIZE]`
- bitwise operators and compound assignment: `&=`, `|=`, `^`, `~`, `<<`, `>>`
- casts, `sizeof`, `NULL`, nested ternary expressions, `for`, `while`, `do/while`, `continue`, `return`
- standard-library-style calls declared as externs: `printf`, `fprintf`, `sprintf`, `fopen`, `fclose`
- six stage-named groups, `bpc_stage1_*` through `bpc_stage6_*`, with lane/helper functions plus join/final helpers

The design goal is not numerical BPC correctness. It is a stable C-model fixture that makes the Sequence and Diagram views reveal pipeline structure, parallel lanes, calls, reads/writes, and syntax coverage.

### 2. Put C++ syntax stress in a separate fixture

A new fixture, `tests/fixtures/syntax/cpp_syntax_coverage.cpp`, covers C++ constructs without contaminating the default C sample:

- templates
- classes and inheritance
- virtual methods and destructors
- static members and local statics
- references and `const` references
- `new[]` / `delete[]`
- pointer-to-array and pointer-to-multidimensional-array parameters

The fixture intentionally avoids HLS/SystemC markers such as `ac_int`, `sc_uint`, `#pragma HLS`, and `.range(`. The maintained target is ordinary C++ cmodel coverage: CVAS should discover qualified class/member blocks, normalize C++ reference and pointer-to-array parameters into readable inputs, connect direct/template/member calls into the call graph and Sequence view, and keep HLS/SystemC semantic modeling out of scope.

### 3. Add a Pipeline stage order to the Sequence board

`json_to_html.py` now builds a third Sequence execution layout in addition to the existing Call order and Dependency order:

- `call`: root/caller-left layout, with caller-local callee order expanded left-to-right
- `dependency`: static dependency/data producer layout
- `pipeline`: heuristic stage-number layout based on function names like `bpc_stage3_*`

The pipeline layout extracts stage numbers with this naming convention:

```python
(?:^|_)stage(\d+)(?:_|$)
```

Non-stage functions go in an `Entry / utility` column. Stage helpers go in `Stage N` columns. Functions containing `join` or `final` are sorted below the lane helpers in the same stage column.

Important boundary: this is still static visualization. It must not be documented as a cycle-accurate runtime, HLS schedule, or guaranteed hardware pipeline schedule.

### 4. Split checked-in sample artifacts into fast/full outputs

The branch removes the old ambiguous default sample artifacts:

- `docs/test_examples_output.html`
- `docs/test_examples_output.json`

It uses explicit mode-specific artifacts instead:

- `docs/test_examples_output_fast.html`
- `docs/test_examples_output_fast.json`
- `docs/test_examples_output_full.html`
- `docs/test_examples_output_full.json`

README, AGENTS, and the project overview docs were updated to point to the explicit fast/full artifact names.

---

## Current changed files

Source/viewer:

- `test_examples.c`
- `json_to_html.py`

Tests/fixtures:

- `tests/test_sample_cmodel_contract.py`
- `tests/fixtures/syntax/cpp_syntax_coverage.cpp`
- `tests/test_viewer_schema_compat.py`

Docs/artifacts:

- `AGENTS.md`
- `README.md`
- `docs/cvas_datapath_pipeline_design.md`
- `docs/cvas_project_overview.html`
- `docs/test_examples_output_fast.html`
- `docs/test_examples_output_fast.json`
- `docs/test_examples_output_full.html`
- `docs/test_examples_output_full.json`
- deleted `docs/test_examples_output.html`
- deleted `docs/test_examples_output.json`

---

## Validation already run

Run from `/home/dudupunch0/company/cvas/CVAS/.worktrees/sample-cmodel-syntax-coverage`:

```bash
../../../.venv/bin/python -m py_compile \
  json_to_html.py \
  src/cvas_mvp.py \
  src/cvas_cli.py \
  src/cvas_pipeline.py \
  src/cvas_passes.py

../../../.venv/bin/python -m pytest -q \
  tests/test_sample_cmodel_contract.py \
  tests/test_viewer_schema_compat.py
# 28 passed

../../../.venv/bin/python -m pytest -q
# 85 passed
```

A wrapper regeneration comparison was also run into `/tmp/cvas-sample-cmodel-eval-7768`:

```bash
../../../.venv/bin/python cvas_wrapper.py test_examples.c /tmp/cvas-sample-cmodel-eval-7768/test_examples_output_fast.html \
  --output-json /tmp/cvas-sample-cmodel-eval-7768/test_examples_output_fast.json \
  --cvas-args --analysis-mode fast

../../../.venv/bin/python cvas_wrapper.py test_examples.c /tmp/cvas-sample-cmodel-eval-7768/test_examples_output_full.html \
  --output-json /tmp/cvas-sample-cmodel-eval-7768/test_examples_output_full.json \
  --cvas-args --analysis-mode full
```

Observed comparison:

- `docs/test_examples_output_fast.json` matched the regenerated fast JSON.
- `docs/test_examples_output_fast.html` differed from regenerated HTML.
- `docs/test_examples_output_full.json` differed only in `gcc_dump.command` temp-path content in the inspected diff sample.
- `docs/test_examples_output_full.html` differed because it embeds the same full JSON temp-path content and generated viewer model.

---

## Handoff unfinished items / follow-up status

### 1. HTML sample artifacts were likely stale

Original handoff risk: the checked-in fast HTML did not contain the newly
generated embedded `Pipeline stage order` model when compared with a fresh
wrapper output.

Follow-up status: resolved by regenerating the checked-in fast/full HTML
artifacts from the final `json_to_html.py` generator.

### 2. Full-mode JSON had temp-path nondeterminism

Original handoff risk: the full-mode JSON embedded `gcc_dump.command`,
including a temporary directory path. Fresh regeneration changed that path and
therefore changed the JSON/HTML hash even when the semantic model was otherwise
unchanged.

Follow-up status: resolved with option 1. `gcc_dump.command` is now serialized
as a normalized diagnostic display command, and
`command_path_policy: "normalized"` records that policy. The subprocess still
receives the exact local argv.

### 3. Pipeline layout is heuristic

The stage layout currently depends on stage-number naming in function names. This is acceptable for the sample fixture, but should remain documented as a static, name-derived visualization mode. Do not generalize it as a scheduler without adding real scheduling metadata and tests.

### 4. Manual viewer QA is still needed

Original handoff risk: automated tests passed, but the Diagram and Sequence tabs
had not been manually inspected in a browser after the latest branch state.
Follow-up status: complete after browser QA of regenerated fast/full artifacts.
The checked items were:

- Order selector includes `Call order`, `Dependency order`, and `Pipeline stage order`.
- Pipeline mode groups `bpc_stage1_*` through `bpc_stage6_*` into stage columns.
- Join/final cards sit below lane/helper cards.
- Drag/reset controls work for all order modes. Export/import controls are
  present and enabled; Export was clicked without browser console errors.
  Browser automation cannot complete the native file-picker import path, so
  future changes to import handling should still be checked in a desktop
  browser.
- Diagram tab remains usable with the much larger sample.

### 5. Large generated artifact diff needs reviewer attention

The JSON/HTML sample artifacts can produce a large diff. Before future merges,
make sure generated files correspond to the intended source and viewer code.
Avoid hand-editing generated JSON/HTML unless the generator itself is also
updated.

---

## Original recommended continuation plan

The steps below are retained as the original handoff checklist. The completed
follow-up resolved the artifact regeneration and deterministic full-mode command
policy above.

### Task 1: Re-ground branch state

Run:

```bash
git fetch --prune origin
git status --short --branch -uall
git rev-list --left-right --count HEAD...origin/main
git diff --name-status origin/main...HEAD
```

Expected:

- branch is `feat/sample-cmodel-syntax-coverage`
- PR branch is based on current `origin/main` or is cleanly rebasable
- only the intended sample/viewer/test/doc files are in scope

### Task 2: Decide full-artifact determinism policy

Inspect `src/cvas_gcc_dump.py`, `src/cvas_pipeline.py`, and the JSON serialization path for `gcc_dump.command`.

Pick one explicit policy:

- normalize temp directories in `gcc_dump.command` before writing checked-in JSON;
- remove command from sample artifact if it is diagnostic-only and not part of the stable contract;
- or keep it and document that full sample output is not hash-stable across machines.

Add or adjust tests if code behavior changes.

### Task 3: Regenerate sample artifacts from the final generator

Run:

```bash
../../../.venv/bin/python cvas_wrapper.py test_examples.c docs/test_examples_output_fast.html \
  --output-json docs/test_examples_output_fast.json \
  --cvas-args --analysis-mode fast

../../../.venv/bin/python cvas_wrapper.py test_examples.c docs/test_examples_output_full.html \
  --output-json docs/test_examples_output_full.json \
  --cvas-args --analysis-mode full
```

Then inspect:

```bash
git diff --stat -- docs/test_examples_output_fast.* docs/test_examples_output_full.*
```

### Task 4: Browser-check the viewer

Open `docs/test_examples_output_fast.html` and `docs/test_examples_output_full.html` locally.

Verify the Sequence and Diagram acceptance checklist from the Known unfinished items section above.

### Task 5: Run final validation

Run:

```bash
../../../.venv/bin/python -m py_compile \
  src/cvas_mvp.py src/cvas_cli.py src/cvas_pipeline.py src/cvas_passes.py \
  src/cvas_callgraph.py src/cvas_source.py src/cvas_analysis.py \
  src/cvas_gcc_dump.py src/cvas_treesitter.py src/c_ast_utils.py \
  json_to_html.py tools/generate_function_io.py tools/function_io_contract.py

../../../.venv/bin/python -m pytest -q

git diff --check
git diff --cached --check
```

### Task 6: Mark the PR ready only after artifacts and manual QA are resolved

Until the above is complete, keep the PR draft or label it clearly as a handoff/WIP PR.

---

## PR handoff note

This branch is intentionally being pushed as a preserved handoff snapshot. It contains useful design and test work, and local automated tests pass, but the next LLM/agent should treat the generated artifact synchronization and full-mode temp-path determinism as the first follow-up decisions before requesting final review.
