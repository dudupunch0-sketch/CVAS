# Server GCC 10.2 Validation Task

## Status

Open, non-blocking server validation task.

PR #52 completed the full-mode backend shift and validated the implementation in local and Docker environments. The Docker validation used Debian GCC 14.2.0, not GCC 10.2. This task remains for the target server/RHEL-style environment where GCC 10.2 is available.

This task does not reopen the PR #52 implementation plan. It is a deployment-readiness check for the server environment.

## Goal

Verify that CVAS `--analysis-mode full` works on a real GCC 10.2 toolchain and still reports GCC dump metadata successfully.

Expected full-mode backend contract:

```text
analysis_backend = tree-sitter+pycparser+gcc-dump
gcc_dump.status = ok
```

Tree-sitter packages are optional. If they are absent, full mode should still fall back to pycparser/text analysis and run GCC dump metadata when `gcc`/`g++` are available.

## Environment Assumptions

- Target is a Linux server, likely RHEL 8.x or similar.
- GCC/G++ 10.2 or the server-provided GCC 10.x toolchain is available.
- Python can create or use a virtual environment.
- External/public LLM access is not required for this validation.

## Validation Commands

Run from the repository root.

If running from the main checkout:

```bash
python --version
gcc --version
g++ --version

python -m venv ../.venv
../.venv/bin/python -m pip install --upgrade pip
../.venv/bin/python -m pip install -r requirements.txt

../.venv/bin/python -m pytest -q
../.venv/bin/python -m py_compile \
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

../.venv/bin/python src/cvas_cli.py test_examples.c --analysis-mode fast -o /tmp/cvas_fast_gcc102.json
../.venv/bin/python src/cvas_cli.py test_examples.c --analysis-mode full -o /tmp/cvas_full_gcc102.json
../.venv/bin/python - <<'PY'
import json
from pathlib import Path
model = json.loads(Path('/tmp/cvas_full_gcc102.json').read_text(encoding='utf-8'))
print('analysis_backend=' + str(model.get('analysis_backend')))
print('gcc_dump_status=' + str(model.get('gcc_dump', {}).get('status')))
print('gcc_dump_backend=' + str(model.get('gcc_dump', {}).get('backend')))
print('gcc_dump_language=' + str(model.get('gcc_dump', {}).get('language')))
print('gcc_dump_standard=' + str(model.get('gcc_dump', {}).get('standard')))
for line in model.get('gcc_dump', {}).get('diagnostics', [])[:10]:
    print('diagnostic: ' + line)
assert model.get('analysis_backend') == 'tree-sitter+pycparser+gcc-dump'
assert model.get('gcc_dump', {}).get('status') == 'ok'
PY
```

If running inside a worktree under `CVAS/.worktrees/<name>`, replace `../.venv/bin/python` with `../../../.venv/bin/python`.

## Expected Result

- `pytest` passes.
- `py_compile` passes.
- Fast-mode smoke test writes `/tmp/cvas_fast_gcc102.json`.
- Full-mode smoke test writes `/tmp/cvas_full_gcc102.json`.
- Full-mode JSON reports:
  - `analysis_backend = tree-sitter+pycparser+gcc-dump`
  - `gcc_dump.backend = gcc`
  - `gcc_dump.status = ok`
  - `gcc_dump.language = c`
  - `gcc_dump.standard = c11`
- GCC diagnostics may include warnings. Warnings are acceptable when the return code is zero and `gcc_dump.status` remains `ok`.

## Failure Triage

- `gcc` or `g++` missing:
  - Install the server development toolchain, for example RHEL Development Tools or the approved internal package set.
- Python dependency missing:
  - Re-run `python -m pip install -r requirements.txt` in the active venv.
- `gcc_dump.status = unavailable`:
  - Check PATH and the selected compiler name.
- `gcc_dump.status = failed`:
  - Inspect `gcc_dump.command`, `gcc_dump.returncode`, and `gcc_dump.diagnostics` in `/tmp/cvas_full_gcc102.json`.
  - If the failure is caused by a GCC 10.2 flag incompatibility, create a bugfix task because the implementation promise is GCC 10.2-compatible command construction.
- Tree-sitter import failures:
  - Do not treat them as task failure unless full-mode model generation fails. Tree-sitter is optional and should degrade to fallback analysis.

## Completion Criteria

Mark this task complete when a real GCC 10.2 server run records:

1. Exact `python --version`, `gcc --version`, and `g++ --version` output.
2. `pytest` and `py_compile` results.
3. Fast/full CLI smoke outputs.
4. `gcc_dump.status = ok` from `/tmp/cvas_full_gcc102.json`.

If validation fails, keep this task open and create a focused bugfix task with the captured `gcc_dump.command` and diagnostics.
