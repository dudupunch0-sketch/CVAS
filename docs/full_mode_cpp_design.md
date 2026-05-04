# CVAS Full-Mode Analysis Design

This document describes the current `--analysis-mode full` contract after the backend shift away from a clang/libclang-required path.

The older clang-centered C++ design has been superseded. Clang-related helper modules remain in-tree for compatibility and historical low-level tests, but the public `full` mode no longer requires Python `clang` bindings or system `libclang`.

## Current Goal

Provide a deployable full analysis mode that works on restricted internal Linux servers:

- `fast` mode: pycparser-backed lightweight analysis with text fallback.
- `full` mode: optional tree-sitter structural discovery + fast analysis fallback + non-fatal GCC dump metadata.
- Keep the JSON schema stable and add optional metadata rather than requiring a viewer rewrite.
- Use GCC/G++ flags compatible with GCC 10.2-era environments.

## Not Building

The current full-mode design intentionally does not build:

- a full compiler-driver replacement
- clang/libclang as a required runtime dependency
- linker-aware or whole-build semantic analysis
- guaranteed support for template-heavy C++ semantics
- hard-failure behavior for ordinary compiler diagnostics
- a third public analysis mode such as `tolerant`

## Public Modes

### `fast`

`fast` is the baseline mode. It uses `pycparser` where possible and falls back to text/regex parsing when AST parsing is unavailable or incomplete.

Reported backend:

```text
pycparser
```

### `full`

`full` is an enrichment mode. It starts with structural discovery where possible, keeps the robust fast path as fallback, and adds GCC dump metadata to the output JSON.

Reported backend:

```text
tree-sitter+pycparser+gcc-dump
```

## Full-Mode Pipeline

```text
source file
  -> extract CVAS_START/CVAS_END region
  -> optional tree-sitter function discovery when tree_sitter packages are installed
  -> merge pycparser/text fallback discovery by function name
  -> pycparser/text fallback for statement analysis
  -> call graph / CFG / flow model generation
  -> gcc/g++ CFG dump metadata attachment
  -> JSON output
```

Tree-sitter packages are optional:

```bash
pip install tree_sitter tree_sitter_c tree_sitter_cpp
```

If they are absent, CVAS still runs full mode through the fast fallback path and records GCC dump metadata when GCC/G++ is available.

## GCC Dump Metadata

`full` mode calls `gcc` for C inputs and `g++` for C++ inputs. The dump pass is a metadata enrichment pass, not the authoritative model generator.

The command is intentionally conservative for GCC 10.2 compatibility. It uses:

- `-c`
- `-o <tmp>/cvas-gcc-dump.o`
- `-fdump-tree-cfg`
- `-x c` or `-x c++`
- `-std=<resolved-standard>`
- `-DCVAS_START=`
- `-DCVAS_END=`
- `-Wno-error=implicit-function-declaration` for C inputs, so GCC 14+ keeps historical implicit-call diagnostics as warnings during best-effort metadata generation
- compatible include/define flags reconstructed from CLI args or `compile_commands.json`

It intentionally avoids newer/non-portable diagnostics features such as:

- `-fanalyzer`
- `-fdiagnostics-format=json`

## CLI Flag Compatibility

The public CLI exposes neutral names:

- `--compile-arg`
- `--compile-db`

It also keeps the historical names as compatibility aliases:

- `--clang-arg`
- `--clang-compile-db`

In the current full-mode path, compatible compile flags from either spelling are reused for GCC dump reconstruction. The main preserved flag families are:

- `-I`
- `-D`
- `-U`
- `-isystem`
- language/standard hints used to resolve `-x` and `-std=`

## Output Contract

A normal successful model includes the existing core fields:

- `blocks`
- `operations`
- `signals`
- `flow`
- `diagram_hint`
- `note`
- `analysis_version`

It also includes backend metadata:

- `analysis_mode`
- `analysis_backend`
- `project_mode`
- `duplicate_functions`

In `full` mode, it also includes `gcc_dump`:

```json
{
  "backend": "gcc",
  "status": "ok",
  "language": "c",
  "standard": "c11",
  "dump_files": ["cvas-gcc-dump.c.015t.cfg"],
  "diagnostics": []
}
```

`gcc_dump.status` values:

- `ok`: compiler ran successfully
- `failed`: compiler returned a non-zero status, timed out, or raised an OS/subprocess error
- `unavailable`: expected compiler was not found on `PATH`

`diagnostics` can be non-empty even when `status` is `ok`; GCC warnings and dump chatter are preserved as metadata but do not make the enrichment pass fail unless the compiler returns a non-zero status.

Early-return outputs for a missing CVAS region or a region with no function definitions still include `analysis_mode` and `analysis_backend`. In `full` mode, they also include `gcc_dump` when the metadata pass can run.

## Failure Model

The intended policy is:

- Missing tree-sitter packages: non-fatal fallback to fast analysis.
- Missing GCC/G++: non-fatal `gcc_dump.status = "unavailable"`.
- GCC non-zero exit, timeout, or subprocess failure: non-fatal `gcc_dump.status = "failed"`.
- Compile DB/config resolution failure: non-fatal `gcc_dump.status = "failed"` with diagnostics.
- GCC warnings with return code 0: keep diagnostics metadata but leave `gcc_dump.status = "ok"`.
- pycparser parse failure: fallback to text parsing where possible.

## Language and Standard Resolution

CVAS resolves language and standard through `AnalysisOptions` and the legacy compile-flag resolver:

- explicit `--language` wins when supplied
- `-x` from user args or compile DB can provide language hints
- source suffix inference handles `.c`, `.cc`, `.cpp`, `.cxx`, `.hpp`, `.hh`, `.hxx`
- default standard is `c11` for C and `c++11` for C++
- explicit user `-std=` wins over compile DB `-std=`

Entry-region tree-sitter discovery receives `source_path=entry_file` even when the parsed text is the extracted CVAS region. That preserves `.cpp`/`.hpp` language inference without requiring `--language c++`.

## Testing Contract

Minimum validation commands:

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

When running from the main checkout instead of `.worktrees/<name>`, use `../.venv/bin/python`.

## Follow-Up Hardening Checklist

Implemented hardening items:

1. Malformed compile DB handling is non-fatal and reported under `gcc_dump`.
2. Entry source path is passed into full-mode tree-sitter entry discovery.
3. Public full-mode compile DB tests no longer depend on clang availability and assert GCC dump behavior directly.
4. Tree-sitter partial results are merged with pycparser/regex fallback results by function name.
5. GNU asm normalization covers common `asm volatile`, inline `__asm__`, and `__asm__ __volatile__` statement forms.
6. Early-return JSON includes `analysis_mode`, `analysis_backend`, and full-mode `gcc_dump` metadata.
7. Neutral CLI aliases `--compile-arg` and `--compile-db` are available while legacy names remain supported.

Remaining external validation:

- Run the separate server GCC 10.2 validation task documented in `docs/plans/server-gcc-10-2-validation.md`. Local and Docker validation has covered available GCC versions, including Docker GCC 14.2, but a real GCC 10.2 server run remains a deployment-readiness check rather than a blocker for the completed PR #52 implementation.
