# CVAS Environment Setup

Run all commands from the `CVAS/` repository root.
The shared virtual environment lives one level up at `../.venv`.

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

This installs `pytest` for the regression test suite. The default `fast` analysis mode uses `pycparser` when available in the workspace environment and text fallback otherwise.

## Optional Analysis Backends

### Full mode: optional tree-sitter + GCC dump

`--analysis-mode full` uses optional tree-sitter C/C++ grammars for structural function discovery when these packages are installed:

```bash
pip install tree_sitter tree_sitter_c tree_sitter_cpp
```

If they are not installed, CVAS falls back to the existing fast pycparser/text path.

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
