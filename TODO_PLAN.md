# TODO Plan: Full-Mode GCC Dump Hardening

This file supersedes the older clang-hard-fail C++ full-mode plan. The public CVAS direction is now:

- `fast` = pycparser/text fallback analysis
- `full` = optional tree-sitter structural discovery + fast fallback + non-fatal GCC dump metadata
- clang/libclang is not required for public `full` mode
- `tolerant` is not a public mode

The detailed current design is in `docs/full_mode_cpp_design.md` and the implementation history is in `docs/plans/2026-05-04-analysis-backend-shift.md`.

## Highest Priority Follow-Ups

1. Make GCC dump metadata fully non-fatal.
   - Malformed/unreadable `compile_commands.json` should produce `gcc_dump.status = "failed"`, not a traceback.
   - Add a regression test with invalid compile DB JSON.

2. Preserve C++ language inference for full-mode tree-sitter entry discovery.
   - Pass `entry_file` or resolved language into the entry-region `find_function_definitions(...)` call.
   - Add a regression test for `.cpp` entry files without `--language c++`.

3. Strengthen full-mode compile DB tests without clang dependency.
   - Public full-mode compile DB tests should not call `require_clang()`.
   - Assert that include paths from compile DB affect `gcc_dump.status` and diagnostics.

4. Add neutral CLI aliases.
   - Keep `--clang-arg` and `--clang-compile-db` for compatibility.
   - Add `--compile-arg` and `--compile-db` as clearer aliases.

5. Decide JSON metadata policy for early returns.
   - Current no-region/no-function outputs are minimal.
   - Decide whether they should include `analysis_mode`, `analysis_backend`, and possibly `gcc_dump`.

## Medium Priority Follow-Ups

1. Define tree-sitter partial-result merge policy.
   - Current behavior treats any non-empty tree-sitter result as authoritative.
   - Consider merging fallback results by function name.

2. Expand pycparser normalization.
   - Add support for `asm volatile (...)`, inline `__asm__`, and `__asm__ __volatile__` forms.

3. Run a real GCC 10.2 smoke test.
   - Current CI/dev validation uses the available local GCC.
   - The command shape is intentionally GCC 10.2-compatible, but actual GCC 10.2 execution still needs environment validation.

## Validation Commands

From this worktree layout:

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

Use `src/cvas_cli.py` for direct CLI testing and keep `src/cvas_mvp.py` as the compatibility wrapper.
