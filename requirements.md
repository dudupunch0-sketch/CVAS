# CVAS Environment Setup

Run commands from the `CVAS/` repository root unless noted otherwise.
The shared virtual environment normally lives one level up at `../.venv`. If you are inside a Git worktree under `CVAS/.worktrees/<name>`, use `../../../.venv/bin/python` for verification commands.

## Base Development Environment

Create and activate a virtual environment:
```bash
cd ..
python -m venv .venv
cd CVAS
source ../.venv/bin/activate
../.venv/bin/python -m pip install --upgrade pip
```

Install the Python dependencies listed in `requirements.txt`:
```bash
pip install -r requirements.txt
```

This installs `pycparser` for AST-backed C parsing, tree-sitter C/C++ packages for full-mode structural discovery, `pytest` for the regression test suite, and `jsonschema` for schema validation tests. The default `fast` analysis mode uses `pycparser` first and text fallback otherwise.

## Analysis Backends

### Full mode: tree-sitter + GCC dump

`--analysis-mode full` uses the tree-sitter C/C++ grammars installed from `requirements.txt` for structural function discovery. If tree-sitter cannot parse useful functions for a given input, CVAS falls back to the existing fast pycparser/text path.

### Full mode: GCC dump

`--analysis-mode full` no longer requires Python `clang` bindings or system `libclang`. It runs the normal fast analysis and augments the JSON with non-fatal `gcc_dump` metadata.

Install a system GCC toolchain if `gcc`/`g++` are missing.

RHEL 8.10 example:
```bash
sudo dnf groupinstall -y "Development Tools"
```

Ubuntu/Debian example:
```bash
sudo apt-get update
sudo apt-get install build-essential
```

The legacy `--clang-arg` and `--clang-compile-db` option names are still accepted for compatibility; include/define/std flags from them are reused by the GCC dump pass.

## Verification Commands

After the virtual environment is active:
```bash
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
```

## Quick Smoke Tests

Fast mode:
```bash
../.venv/bin/python src/cvas_cli.py test_examples.c --analysis-mode fast -o /tmp/cvas_fast.json
```

Full mode:
```bash
../.venv/bin/python src/cvas_cli.py test_examples.c --analysis-mode full -o /tmp/cvas_full.json
```
