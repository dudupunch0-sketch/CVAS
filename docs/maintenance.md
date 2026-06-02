# CVAS Documentation Maintenance

This page records the repository-local maintenance flow for docs and generated
sample outputs.

## Sample Output Refresh

The checked-in sample output files are generated from `test_examples.c` and
should match the current analyzer and viewer code:

- `docs/test_examples_output_fast.json`
- `docs/test_examples_output_fast.html`
- `docs/test_examples_output_full.json`
- `docs/test_examples_output_full.html`

Before replacing them, move the previous files into a dated backup folder:

```bash
backup_dir=docs/backup/YYYY-MM-DD-before-output-refresh
mkdir -p "$backup_dir"
mv docs/test_examples_output_fast.html "$backup_dir/"
mv docs/test_examples_output_fast.json "$backup_dir/"
mv docs/test_examples_output_full.html "$backup_dir/"
mv docs/test_examples_output_full.json "$backup_dir/"
```

Then regenerate the current outputs from the repository root:

```bash
../.venv/bin/python cvas_wrapper.py test_examples.c docs/test_examples_output_fast.html \
  --output-json docs/test_examples_output_fast.json \
  --cvas-args --analysis-mode fast
../.venv/bin/python cvas_wrapper.py test_examples.c docs/test_examples_output_full.html \
  --output-json docs/test_examples_output_full.json \
  --cvas-args --analysis-mode full
```

`cvas_wrapper.py` also copies the offline viewer asset to
`docs/assets/elk.bundled.js` when needed.

## Verification

Run these checks before opening or updating a docs PR:

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
  src/cvas_text.py \
  src/c_ast_utils.py \
  json_to_html.py \
  tools/generate_function_io.py \
  tools/function_io_contract.py
git diff --check
```

Some tests can leave `function_io.rule.json` in the repository root. Remove it
before staging unless the test contract intentionally changes:

```bash
rm -f function_io.rule.json
```

## Review Notes

- The generated JSON/HTML files are large. Review the semantic diff first, then
  spot-check the embedded HTML data only as needed.
- The sample viewer should still open the Diagram and Sequence tabs after a
  refresh. For Sequence changes, check Call order, Dependency order, and
  Pipeline stage order.
- Keep old output backups in `docs/backup/` only when the previous generated
  state is useful for review or comparison.
