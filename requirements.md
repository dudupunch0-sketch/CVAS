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

Install development and test dependencies:

```bash
pip install -r requirements-dev.txt
```

This installs:

- `pytest` for the regression test suite

## Optional Full Analysis Backend

The default `fast` mode does not need any extra runtime package.

If you want `--analysis-mode full`, install the Python bindings:

```bash
source ../.venv/bin/activate
pip install -r requirements-full.txt
```

This installs:

- `clang` Python bindings

`full` mode also requires a working system `libclang`. Install it with your OS package manager.

Example on Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install libclang-dev
```

If `libclang` is installed in a non-standard location, set `LIBCLANG_PATH` before running CVAS.

Example:

```bash
export LIBCLANG_PATH=/path/to/clang/native
```

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
  src/cvas_clang.py \
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
