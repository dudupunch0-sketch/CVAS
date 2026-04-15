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

This installs:

- `pytest` for the regression test suite
- `clang` Python bindings for `--analysis-mode full`

## Optional Full Analysis Backend

The default `fast` mode does not need any extra runtime package.

If you want `--analysis-mode full`, install the Python bindings from the same file:

```bash
source ../.venv/bin/activate
pip install -r requirements.txt
```

This installs:

- `clang` Python bindings

`full` mode also requires a working system `libclang`. Install it with your OS package manager.

### RHEL 8.10 example

On RHEL 8.10, install the LLVM toolset that provides Clang and `libclang`:

```bash
sudo dnf module install -y llvm-toolset
```

Then verify that Python can load the bindings:

```bash
python -c "from clang import cindex; cindex.Index.create(); print('clang ok')"
```

If Python still cannot find `libclang`, set `LIBCLANG_PATH` to the directory that contains `libclang.so`.

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
