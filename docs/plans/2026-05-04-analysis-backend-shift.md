# CVAS Analysis Backend Shift Implementation Plan

> For Hermes: implement with TDD. User chose three priorities: tree-sitter C/C++ backend, stronger pycparser normalization, and GCC dump. The public CLI should expose only `fast` and `full` modes.

Goal: Replace clang-centered full mode with a deployable two-mode strategy: `fast` = pycparser/text fallback, `full` = optional tree-sitter structural discovery + pycparser fast analysis + non-fatal GCC dump metadata.

Architecture: Keep the existing JSON schema stable and add optional metadata rather than rewriting model generation. Clang code remains in-tree for now but is no longer required by CLI full mode. Tree-sitter is optional inside full mode: use it when installed, otherwise degrade to pycparser/regex. GCC dump should use GCC 10.2-compatible flags only.

Tech Stack: Python stdlib, pycparser, optional tree_sitter/tree_sitter_c/tree_sitter_cpp, GCC/G++ 10.2-compatible dump flags.

---

### Task 1: Update analysis mode semantics

Objective: Allow only fast/full modes and report backends accurately.

Files:
- Modify: src/cvas_analysis.py
- Modify: src/cvas_cli.py
- Test: tests/test_regression.py

Steps:
1. Add tests asserting AnalysisOptions(mode='fast').backend == 'pycparser', AnalysisOptions(mode='full').backend == 'tree-sitter+pycparser+gcc-dump', and AnalysisOptions(mode='tolerant') raises ValueError.
2. Run the tests and verify RED.
3. Update AnalysisOptions.__post_init__ and backend property.
4. Remove CLI tolerant choice and full-mode clang availability preflight.
5. Run targeted tests and verify GREEN.

### Task 2: Add optional tree-sitter function-definition backend inside full mode

Objective: full mode should try tree-sitter before pycparser/regex, without exposing a third user-facing mode.

Files:
- Create: src/cvas_treesitter.py
- Modify: src/cvas_source.py
- Test: tests/test_regression.py

Steps:
1. Add a monkeypatch-based test that full mode returns functions from find_function_definitions_with_tree_sitter when that function is available.
2. Run test and verify RED.
3. Implement cvas_treesitter.py with optional imports and a find_function_definitions_with_tree_sitter(source, language) API.
4. Wire cvas_source.find_function_definitions to call it in full mode and then fall back.
5. Run targeted test and verify GREEN.

### Task 3: Strengthen pycparser normalization

Objective: fast/full pycparser fallback should parse more real-world C by stripping common compiler extensions while preserving line positions where practical.

Files:
- Modify: src/c_ast_utils.py
- Test: tests/test_regression.py

Steps:
1. Add tests for __extension__, __inline__/__restrict__, _Static_assert, pragma lines, and asm blocks inside CVAS region.
2. Run tests and verify RED.
3. Extend normalize_c_source to blank/remove these constructs safely.
4. Run targeted tests and verify GREEN.

### Task 4: Add GCC 10.2-compatible dump metadata for full mode

Objective: full mode runs fast/full structural analysis and augments JSON with GCC dump diagnostics/metadata when possible.

Files:
- Create: src/cvas_gcc_dump.py
- Modify: src/cvas_pipeline.py
- Test: tests/test_regression.py

Steps:
1. Add CLI-level test: --analysis-mode full succeeds without clang and reports analysis_backend tree-sitter+pycparser+gcc-dump plus gcc_dump metadata.
2. Add command-construction test ensuring only GCC 10.2-compatible flags are used: -c, -o, -fdump-tree-cfg, -x, -std, -I/-D/-U/-isystem, and CVAS marker defines. Do not use newer/nonportable flags such as -fanalyzer or -fdiagnostics-format=json.
3. Run tests and verify RED.
4. Implement cvas_gcc_dump.run_gcc_dump(source, source_path, analysis_options) using gcc/g++ with temporary output and compile-db include/define args when available.
5. In build_model, attach gcc_dump metadata only for full mode.
6. Run targeted tests and verify GREEN.

### Task 5: Update stale clang/tolerant-centered tests/docs minimally

Objective: Align tests and docs with the two-mode user decision while avoiding large unrelated doc churn.

Files:
- Modify: tests/test_regression.py
- Modify: README.md
- Modify: requirements.md
- Modify: requirements.txt
- Modify: AGENTS.md

Steps:
1. Replace clang-backend assertions with new full-mode backend assertions.
2. Replace tolerant-mode docs/tests with full-mode tree-sitter integration assertions.
3. Keep low-level clang config tests if existing modules remain, but remove require_clang from new full-mode behavior tests.
4. Run full regression suite using /home/dudupunch0/company/cvas/.venv/bin/python -m pytest -q.
5. Run py_compile for changed modules.
6. Commit with validation commands in the message/body if appropriate.
