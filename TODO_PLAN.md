# TODO Plan: Full-Mode GCC Dump Hardening

This file supersedes the older clang-hard-fail C++ full-mode plan. The public CVAS direction is now:

- `fast` = pycparser/text fallback analysis
- `full` = optional tree-sitter structural discovery + fast fallback + non-fatal GCC dump metadata
- clang/libclang is not required for public `full` mode
- `tolerant` is not a public mode

The detailed current design is in `docs/full_mode_cpp_design.md` and the implementation history is in `docs/plans/2026-05-04-analysis-backend-shift.md`.

## Completed Hardening Items

1. GCC dump metadata is fully non-fatal for compile DB/config resolution failures.
   - Malformed/unreadable `compile_commands.json` produces `gcc_dump.status = "failed"`, not a traceback.
   - Regression coverage: invalid compile DB JSON.

2. C++ language inference is preserved for full-mode tree-sitter entry discovery.
   - The entry-region `find_function_definitions(...)` call receives `source_path=entry_file`.
   - Regression coverage: `.cpp` entry file without `--language c++`.

3. Full-mode compile DB tests no longer depend on clang availability.
   - Public full-mode compile DB tests do not call `require_clang()`.
   - They assert that include paths from compile DB make `gcc_dump.status == "ok"`.

4. Neutral CLI aliases are available.
   - `--compile-arg` and `--compile-db` are clearer aliases.
   - `--clang-arg` and `--clang-compile-db` remain for compatibility.

5. Early-return JSON metadata policy is implemented.
   - No-region/no-function outputs include `analysis_mode` and `analysis_backend`.
   - In `full` mode, they also include `gcc_dump` when the metadata pass can run.

6. Tree-sitter partial-result merge policy is implemented.
   - Tree-sitter results are preferred, and pycparser/regex fallback fills missing function names.

7. pycparser normalization handles common GNU asm variants.
   - Covered forms include `asm volatile (...)`, inline `__asm__`, and `__asm__ __volatile__` statement forms.

## Remaining Follow-Ups

1. Run a real GCC 10.2 smoke test.
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
