# Repository Guidelines

## Project Structure & Module Organization

The main CLI entrypoints live in `src/`:

- `src/cvas_cli.py`: direct CLI frontend
- `src/cvas_mvp.py`: compatibility wrapper for legacy entrypoints

Core analysis orchestration is in `src/cvas_pipeline.py`, with function-level passes in `src/cvas_passes.py`. Shared analysis/model modules include `src/cvas_model.py`, `src/cvas_index.py`, `src/cvas_expr.py`, `src/cvas_cfg.py`, `src/cvas_callgraph.py`, `src/cvas_source.py`, `src/cvas_text.py`, and `src/cvas_serialize.py`.

Viewer generation is handled by `json_to_html.py`, and end-to-end execution by `cvas_wrapper.py`. Regression tests live in `tests/` with fixtures under `tests/fixtures/`. Viewer assets such as `elk.bundled.js` live in `viewer/assets/`. Project docs and checked-in HTML artifacts live in `docs/`, including:

- `docs/cvas_project_overview.html`
- `docs/test_examples_output.html`
- `docs/test_examples_output.json`

Helper tooling such as the function IO generator lives in `tools/`.

## Build, Test, and Development Commands

Run commands from the repository root unless noted otherwise.

- `python src/cvas_cli.py model.c -o output.json`: parse the CVAS region and emit JSON
- `python src/cvas_mvp.py model.c -o output.json`: compatibility entrypoint for the same analysis path
- `python src/cvas_cli.py model.c --analysis-mode fast -o output.json`: explicitly use the default `pycparser` analysis backend
- `python src/cvas_cli.py model.c --analysis-mode full --clang-arg=-Iinclude -o output.json`: use optional tree-sitter structure parsing, fast fallback, and non-fatal GCC dump metadata with extra compatible compile flags when needed
- `python json_to_html.py output.json output.html`: convert JSON to a standalone HTML viewer
- `python cvas_wrapper.py test_examples.c docs/test_examples_output.html --output-json docs/test_examples_output.json`: refresh the checked-in sample HTML/JSON artifacts
- `python -m py_compile src/cvas_mvp.py src/cvas_cli.py src/cvas_pipeline.py src/cvas_passes.py src/cvas_callgraph.py src/cvas_source.py src/cvas_analysis.py src/cvas_gcc_dump.py src/cvas_treesitter.py src/c_ast_utils.py json_to_html.py tools/generate_function_io.py`: quick syntax check
- `../.venv/bin/python -m pytest -q`: run regression tests from `CVAS/` using the workspace-local virtualenv at `/home/dudupunch0/company/cvas/.venv`; from `.worktrees/<name>/`, use `../../../.venv/bin/python -m pytest -q`
- `python tools/generate_function_io.py test_examples.c --llm-provider none`: generate the rule-based `function_io.json`
- `python tools/generate_function_io.py test_examples.c --llm-provider codex-cli --codex-danger-full-access --codex-timeout-sec 60`: run the Codex-assisted function IO refinement path

## Coding Style & Naming Conventions

Use 4-space indentation and descriptive snake_case names. Keep new code ASCII unless the file already uses Unicode. Preserve the existing JSON schema unless a schema change is intentional and documented.

## Testing Guidelines

Tests use `pytest` and snapshot-style fixture comparisons in `tests/test_regression.py`. Prefer the workspace-local virtualenv at `/home/dudupunch0/company/cvas/.venv` when running from `CVAS/`, which means using `../.venv/bin/python`, so the dependency is present even if the base shell image lacks `pytest`. Keep fixture pairs aligned (`*.c` + `*.expected.json`). When viewer behavior changes, refresh the checked-in sample output in `docs/` and manually verify the Diagram and Sequence tabs.

## Commit & Pull Request Guidelines

Use concise imperative commit messages. When behavior changes, include the validation commands you ran. For viewer or documentation updates, mention the affected HTML artifacts explicitly.

## Agent Notes

Prefer `src/cvas_cli.py` when testing the direct CLI path and keep `src/cvas_mvp.py` as a compatibility layer. Use `--analysis-mode fast` for baseline regressions and `--analysis-mode full` when optional tree-sitter structure parsing plus GCC dump metadata should be exercised without requiring clang/libclang. Treat the Diagram tab as the primary operation-flow block diagram, and use CFG / Sequence / call graph views as supporting perspectives.
