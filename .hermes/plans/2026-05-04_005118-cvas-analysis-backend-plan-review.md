# CVAS Analysis Backend Shift Plan Review

> Implementation update: the user explicitly asked to proceed with this review/addendum plan. The high-priority and medium-priority hardening items tracked below have been implemented in the follow-up refactor, except for external validation with a real GCC 10.2 binary.

Goal: Re-check the completed `docs/plans/2026-05-04-analysis-backend-shift.md` refactoring plan against the current branch and identify follow-up corrections needed before this is considered production-ready.

Current status: The original plan direction is still sound: public modes should stay `fast` and `full`; `full` should not require clang/libclang; `full` should use optional tree-sitter structure discovery, pycparser/text fallback, and non-fatal GCC dump metadata. The current branch passes the regression suite, but the audit found several plan/code/doc gaps that should be addressed in a follow-up refactor.

Follow-up implementation status:
- GCC dump compile DB/config failures are non-fatal and reported as `gcc_dump.status = "failed"`.
- Entry-region tree-sitter discovery receives `source_path=entry_file` for `.cpp`/`.hpp` language inference.
- Public full-mode compile DB tests assert GCC dump behavior without `require_clang()`.
- Neutral aliases `--compile-arg` and `--compile-db` are available; legacy `--clang-*` names remain supported.
- Early-return outputs include `analysis_mode`, `analysis_backend`, and full-mode `gcc_dump` metadata.
- Tree-sitter partial results are merged with pycparser/regex fallback by function name.
- GNU asm normalization covers common `asm volatile`, inline `__asm__`, and `__asm__ __volatile__` statement forms.

Validated during review:
- Branch: `hermes/hermes-466a5a2b`
- HEAD: `fe0461b`
- Test command: `../../../.venv/bin/python -m pytest -q`
- Test result: `34 passed in 3.87s`
- Codex CLI review attempt was blocked in this runtime because `codex` depends on `node`, and `node` is not on PATH.

---

## High-priority findings

### 1. GCC dump is not fully non-fatal when compile DB resolution fails

Evidence:
- `src/cvas_gcc_dump.py` calls `resolve_clang_config(...)` before its subprocess error handling.
- A malformed `compile_commands.json` currently produces a traceback and exit code 1 in `--analysis-mode full`.

Why this matters:
- The plan says GCC dump metadata should be non-fatal.
- Compile DB parse/load problems should become `gcc_dump.status = "failed"` diagnostics, not crash the whole model build.

Follow-up test:
1. Create a malformed `compile_commands.json` fixture in a temp dir.
2. Run `src/cvas_cli.py model.c --analysis-mode full --clang-compile-db bad.json -o out.json`.
3. Expect CLI success and `model["gcc_dump"]["status"] == "failed"` with a diagnostic mentioning compile DB parsing.

Likely files:
- Modify: `src/cvas_gcc_dump.py`
- Possibly modify: `src/cvas_analysis.py` or `src/cvas_compile_db.py` if normalization errors need typed handling
- Test: `tests/test_regression.py`

### 2. Full-mode tree-sitter entry parsing loses source_path/language inference

Evidence:
- `src/cvas_pipeline.py` passes only the extracted CVAS `region` to `find_function_definitions(...)` in non-clang paths.
- It does not pass `source_path=entry_file` for the entry-region discovery path.
- `src/cvas_treesitter.py` resolves language as `language or infer_language_from_path(source_path) or "c"`.

Observed effect:
- For a `.cpp` entry file without `--language c++`, full-mode tree-sitter entry discovery defaults to C grammar instead of C++ grammar.

Why this matters:
- Optional `tree_sitter_cpp` will not be used reliably for normal `.cpp` entry files.
- The plan promised optional tree-sitter C/C++ structure discovery inside `full` mode.

Follow-up test:
1. Monkeypatch `cvas_source.find_function_definitions_with_tree_sitter`.
2. Call `build_model(..., entry_file=Path("model.cpp"), AnalysisOptions(mode="full"))`.
3. Assert the hook receives `source_path=model.cpp` or otherwise receives/resolves `language="c++"`.

Likely files:
- Modify: `src/cvas_pipeline.py`
- Possibly modify: `src/cvas_source.py`
- Test: `tests/test_regression.py`

### 3. CLI help still advertises clang semantics

Evidence from `src/cvas_cli.py --help`:
- `--clang-arg`: "Additional clang argument used only in full analysis mode"
- `--language`: "Override source language used by clang in full analysis mode"
- `--clang-compile-db`: "Path to compile_commands.json used to reconstruct clang flags in full analysis mode"

Why this matters:
- The option names can remain for compatibility, but the help text should explain that these are legacy aliases reused for compile flags / GCC dump metadata.
- User-facing CLI help currently contradicts the new clang-not-required direction.

Follow-up test:
1. Unit-test or smoke-test parser help text if practical.
2. At minimum, run `../../../.venv/bin/python src/cvas_cli.py --help` and inspect the changed text.

Likely files:
- Modify: `src/cvas_cli.py`
- Test/docs: `tests/test_regression.py`, `README.md`

### 4. `docs/full_mode_cpp_design.md` is stale and conflicts with the new plan

Evidence:
- It still describes full mode as clang-configuration-centric.
- It says primary translation unit clang parse failures should hard-fail the run.
- It says header entry files are not supported in that phase.

Why this matters:
- This document can mislead future agents/developers into reverting the clang-free full-mode direction.

Follow-up options:
1. Rewrite it as historical context with a warning banner, or
2. Replace it with a new `full_mode_gcc_dump_design.md`, or
3. Move old clang/libclang content into an explicitly deprecated appendix.

Likely files:
- Modify: `docs/full_mode_cpp_design.md`
- Possibly create: `docs/full_mode_gcc_dump_design.md`

### 5. Compile DB tests are still tied to clang availability and do not strongly verify GCC dump behavior

Evidence:
- `tests/test_regression.py::test_full_mode_compile_db_auto_discovery_supplies_include_path` calls `require_clang()`.
- `tests/test_regression.py::test_full_mode_explicit_compile_db_path_is_honored` calls `require_clang()`.
- These tests do not assert that `gcc_dump.status == "ok"` when compile DB include paths are supplied.

Why this matters:
- Compile DB behavior is now relevant to GCC dump, even without clang.
- The plan explicitly said new full-mode behavior tests should not require clang.

Follow-up tests:
1. Remove `require_clang()` from full-mode compile DB behavior tests that are no longer clang-specific.
2. Add assertions that compile DB include paths make `gcc_dump.status == "ok"`.
3. Keep truly low-level clang config tests only around `resolve_clang_config(...)` or `cvas_clang.py`, clearly separated from public full-mode behavior.

Likely files:
- Modify: `tests/test_regression.py`

---

## Medium-priority findings

### 6. Fresh install reproducibility: `requirements.txt` does not list `pycparser`

Evidence:
- `requirements.txt` currently lists `pytest`, but not `pycparser`.
- Tests include `test_pycparser_normalization_handles_common_compiler_extensions`, which expects `parse_translation_unit(...)` to return a pycparser result.

Why this matters:
- A fresh environment created from `requirements.txt` may fail regression tests or silently exercise weaker text fallback behavior.

Follow-up:
- Add `pycparser` to `requirements.txt` unless the intentional contract is "pycparser optional in production but required for tests". If so, split runtime/test requirements explicitly.

Likely files:
- Modify: `requirements.txt`
- Modify: `requirements.md`

### 7. Empty/no-region full-mode outputs omit analysis metadata

Evidence:
- Early returns in `src/cvas_pipeline.py` for no CVAS region or no functions omit `analysis_mode`, `analysis_backend`, and `gcc_dump`.

Why this matters:
- Downstream consumers may expect these metadata fields in all outputs, especially after the plan says backend reporting should be accurate.

Decision needed:
- Either add metadata to early-return models, or document that metadata is present only for successful model builds.

Likely files:
- Modify: `src/cvas_pipeline.py`
- Test: `tests/test_regression.py`

### 8. Tree-sitter partial-result fallback/merge policy is not defined

Evidence:
- `src/cvas_source.py` returns tree-sitter functions immediately if the list is non-empty.

Why this matters:
- If tree-sitter returns some functions but misses others, pycparser/regex fallback never fills gaps.

Decision needed:
- Decide whether tree-sitter is authoritative on non-empty result, or whether CVAS should merge fallback results by function name.

Likely files:
- Modify: `src/cvas_source.py`
- Test: `tests/test_regression.py`

### 9. pycparser normalization still misses common GNU asm variants

Evidence:
- Current normalization handles a line-start `__asm__ volatile (...) ;` shape.
- It does not handle `asm volatile (...)`, inline statement suffixes after another statement, or `__asm__ __volatile__ (...)`.

Why this matters:
- The plan says normalization should handle asm blocks inside the CVAS region.

Follow-up tests:
- Add cases for bare `asm volatile`, inline `__asm__`, and `__asm__ __volatile__`.

Likely files:
- Modify: `src/c_ast_utils.py`
- Test: `tests/test_regression.py`

### 10. README JSON example does not show new optional full-mode metadata

Evidence:
- The README output JSON example does not include `analysis_mode`, `analysis_backend`, or `gcc_dump`.

Why this matters:
- The core schema remains stable, but optional metadata should be documented so users know where GCC diagnostics appear.

Likely files:
- Modify: `README.md`

---

## Lower-priority technical debt

### 11. Legacy clang naming is still widespread internally

Examples:
- `ResolvedClangConfig`
- `resolve_clang_config`
- `clang_args`
- `--clang-arg`
- `--clang-compile-db`

Recommendation:
- Keep old public flags as aliases for compatibility, but consider adding neutral names later:
  - `--compile-arg`
  - `--compile-db`
- Internally, consider a later rename from `ResolvedClangConfig` to a neutral `ResolvedCompileConfig` once behavior stabilizes.

### 12. Plan granularity was good for this completed refactor, but not yet ideal for handoff implementation

The original plan is understandable and TDD-oriented, but several tasks are larger than the 2-5 minute bite-sized standard. If a future agent executes the follow-up work, split each finding above into RED/GREEN/REFACTOR tasks with exact tests and expected failures.

---

## Recommended follow-up execution order

1. Add failing regression tests for the five high-priority findings.
2. Fix non-fatal GCC dump error handling around compile DB resolution.
3. Fix full-mode tree-sitter entry language/source_path propagation.
4. Untangle full-mode compile DB tests from clang availability and assert `gcc_dump` success/failure behavior.
5. Update CLI help text and stale `docs/full_mode_cpp_design.md`.
6. Add `pycparser` to dependency docs or explicitly split optional runtime vs required test dependencies.
7. Decide and implement early-return metadata policy.
8. Decide tree-sitter partial-result merge policy.
9. Extend asm normalization tests and implementation.
10. Refresh README JSON metadata example.

## Verification after follow-up

Run from this worktree:

```bash
../../../.venv/bin/python -m pytest -q
../../../.venv/bin/python -m py_compile \
  src/cvas_mvp.py \
  src/cvas_cli.py \
  src/cvas_pipeline.py \
  src/cvas_passes.py \
  src/cvas_callgraph.py \
  src/cvas_source.py \
  src/cvas_analysis.py \
  src/cvas_gcc_dump.py \
  src/cvas_treesitter.py \
  src/c_ast_utils.py \
  json_to_html.py \
  tools/generate_function_io.py
../../../.venv/bin/python src/cvas_cli.py --help
```
