# TODO Plan: Clang Full-Mode C++ Support

## Goal

Make CVAS `--analysis-mode full` work correctly for real C++ inputs instead of forcing all clang parsing through C mode. The immediate target is `.cpp`, `.cc`, and `.cxx` entry files, plus project-mode indexing that can see related `.h` and `.hpp` files through normal include resolution.

This plan assumes:

- Full mode must use clang successfully or fail clearly.
- A clang parse failure in full mode is a hard error for the overall run.
- Build flag auto-discovery is part of scope, not an optional follow-up.
- `--clang-arg` remains supported and still matters.

## Problem Summary

Current full mode hardcodes `-xc -std=c11` in [src/cvas_clang.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_clang.py:157), and the helper wrappers also use C-flavored unsaved filenames such as `__cvas_region__.c` and `__cvas_calls__.c`. That means `.cpp` inputs are sent to clang as C code, which is incompatible with C++ syntax such as namespaces, member definitions, templates, `std::` types, and many macro environments.

As a result, the system currently fails for the wrong reason:

- clang itself can parse C++
- CVAS currently tells clang to parse C
- actual compile flags and include paths are not being reconstructed well enough

The first fix is language correctness. The second fix is build-context correctness.

## Building

Build a clang configuration path that is aware of:

- source language: `c` or `c++`
- language standard: inferred or explicit
- per-file compile flags from `compile_commands.json`
- additional user-supplied `--clang-arg` flags
- header/include context required for successful parsing

The full-mode parser, wrapper parser, condition extractor, and call analyzer must all share the same resolved clang configuration.

## Not Building

Out of scope for this plan:

- fast mode C++ support via `pycparser`
- template-heavy or metaprogramming-heavy C++ as a guaranteed supported class
- semantic analysis that requires full project builds or linking
- automatic support for header files as the top-level CVAS entry file
- changes to JSON schema
- viewer changes

Headers remain important, but as included files in the clang translation unit, not as standalone entry files in this first pass.

## Recommended Approach

Implement a shared clang configuration layer for full mode, then route all clang-backed analysis through it. This is the smallest change that fixes the real bug while keeping rollback straightforward.

Why this is the right scope:

- It addresses the confirmed root issue: CVAS forces C mode today.
- It keeps the existing architecture intact: `cvas_source`, `cvas_callgraph`, and `cvas_passes` still delegate to `cvas_clang`.
- It improves correctness for real-world C++ by adding compile database support instead of treating `--clang-arg` as the only source of truth.
- It supports hard-failure behavior without making the system opaque, because diagnostics can surface the resolved file, language, standard, and build flags.

## Key Decisions

### 1. Entry-file support

Support `.c`, `.cc`, `.cpp`, and `.cxx` as full-mode entry files.

Headers `.h` and `.hpp` are included in project indexing and compile-flag context, but not treated as the primary entry file for this change. If a header is passed as the entry in full mode, fail early with a clear message.

Reasoning:

- The current pipeline extracts the CVAS region from the entry file text.
- Supporting header-as-entry would require a different execution model and is not needed to fix the current bug.

### 2. Language selection

Add explicit language resolution in full mode:

- `--language c|c++` overrides everything
- otherwise infer from entry-file suffix
- wrapper translation units inherit the resolved language

Reasoning:

- Extension-based inference is convenient and correct for most runs.
- Explicit override is necessary for edge cases and reproducibility.

### 3. Standard selection

Standard selection is important and cannot be ignored.

Resolution order:

- user-supplied `--clang-arg=-std=...`
- file-specific `-std=` from compile database
- default inferred standard

Default inferred standard:

- `c11` for C
- `c++11` for C++

Reasoning:

- A valid C++ source may fail to parse under the wrong standard.
- The current target codebase is confirmed to use `c++11`, so the fallback default should match that reality instead of assuming a newer standard.
- The compile database remains the main build-context source, but explicit user overrides must still win when the user is intentionally correcting or overriding that context.

### 4. Build flag auto-discovery

Add compile database support in full mode. This is not only for debugging. It is required for correctness when real source files depend on:

- include directories
- preprocessor defines
- forced includes
- target flags
- language or standard flags set by the build system

Discovery order:

- explicit CLI path to `compile_commands.json` if provided
- `project_root/compile_commands.json`
- nearest parent directory search from entry file upward

For a matched file entry:

- extract compiler arguments
- remove output-only flags and driver-only options that break libclang parsing when needed
- merge with explicit `--clang-arg`
- let explicit user flags win on conflicts

If no compile database is found:

- continue with inferred language and defaults
- still allow full mode to run
- still hard-fail if clang parse fails

### 5. Failure policy

In full mode, if clang cannot successfully parse the entry translation unit, the run must fail with a hard error.

Fallback behavior is narrower:

- entry translation-unit parse failure: hard error
- helper wrapper parse failure for secondary analysis: use current fallback only if the main TU already parsed successfully and the fallback does not hide the primary failure

Reasoning:

- The user explicitly wants full mode to mean clang-backed analysis, not silent degradation.
- Secondary wrapper failure is sometimes recoverable, but the primary file parse is not.

## Implementation Plan

### Phase 1: Configuration plumbing

Files:

- [src/cvas_analysis.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_analysis.py)
- [src/cvas_cli.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_cli.py)

Tasks:

- Extend `AnalysisOptions` to carry resolved clang configuration inputs:
  - `language`
  - `standard`
  - `compile_db`
  - existing `clang_args`
- Add CLI flags:
  - `--language c|c++`
  - `--clang-compile-db <path>` or a similar explicit compile database path flag
- Update option loading so full mode resolves:
  - entry-file suffix
  - explicit overrides
  - user `--clang-arg`

Acceptance criteria:

- Full mode can represent `c` vs `c++` explicitly in memory.
- Standard resolution order is defined and testable.

### Phase 2: Compile database loading

Files:

- new helper module, likely `src/cvas_compile_db.py`
- [src/cvas_cli.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_cli.py)
- [src/cvas_analysis.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_analysis.py)

Tasks:

- Load `compile_commands.json`
- Match the entry file to the best compile command
- Normalize flags for libclang consumption
- Preserve include paths, defines, forced includes, and `-std=`
- Merge with `--clang-arg`
- Record the resolved flag source for diagnostics

Acceptance criteria:

- Full mode can print or surface which compile database entry was used.
- A project with required include paths can be parsed without manually passing all flags.

### Phase 3: Clang invocation fix

Files:

- [src/cvas_clang.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_clang.py)

Tasks:

- Replace hardcoded `-xc -std=c11` with language-aware argument assembly
- Use C++ wrapper filenames when in C++ mode
- Ensure all helper wrapper entry points use the same resolved language and standard
- Centralize clang argument construction in one helper

Acceptance criteria:

- `.cpp` input no longer gets forced through C mode
- condition extraction, function definition extraction, and call extraction all use the same language config

### Phase 4: Full-mode failure semantics

Files:

- [src/cvas_clang.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_clang.py)
- [src/cvas_source.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_source.py)
- [src/cvas_pipeline.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_pipeline.py)
- [src/cvas_cli.py](/home/dudupunch0/company/cvas/CVAS/src/cvas_cli.py)

Tasks:

- Distinguish primary TU failure from helper-wrapper failure
- Raise a clear full-mode error when the main file cannot be parsed by clang
- Include in the message:
  - resolved language
  - resolved standard
  - whether compile database was used
  - the most relevant clang diagnostics
- Prevent silent fallback to regex or text mode after primary TU failure in full mode

Acceptance criteria:

- A broken or under-configured C++ full-mode run exits non-zero with a specific error message.
- A correctly configured run does not silently drop to text analysis.

### Phase 5: Testing

Files:

- [tests/test_regression.py](/home/dudupunch0/company/cvas/CVAS/tests/test_regression.py)
- new fixtures under [tests/fixtures](/home/dudupunch0/company/cvas/CVAS/tests/fixtures)

Add tests for:

- minimal `.cpp` fixture in full mode
- `.cc` entry support
- default `c++11` behavior when no standard is given
- explicit `--clang-arg=-std=c++17` override
- compile database discovered automatically
- compile database path provided explicitly
- hard error when compile database is absent and required includes are missing
- hard error when a header is passed as the entry file
- existing `.c` full-mode regression remains green

Acceptance criteria:

- New C++ tests pass
- Existing C regression tests remain intact

## Diagnostics Requirements

Every full-mode failure should make debugging faster, not slower.

Minimum diagnostics on failure:

- entry file path
- resolved language
- resolved standard
- compile database path used, if any
- final clang argument summary
- top clang diagnostics after de-duplication

This matters because compile-database support is not just for convenience. It lets users tell the difference between:

- wrong language mode
- wrong standard
- missing include path
- missing generated header
- unsupported source construct

## Attack Review

### Dependency failure

If `compile_commands.json` is missing, the system must still attempt inference and explicit flags. The run only fails if clang still cannot parse the source.

### Scale explosion

Parsing wrappers repeatedly may still be expensive on large files. This plan does not solve that. It does not materially worsen it either.

### Rollback cost

Rollback is low risk because the change is mostly option plumbing and clang invocation logic. JSON output schema remains unchanged.

### Premise collapse

The weakest assumption is that switching to C++ mode plus real build flags is enough for the current class of failing inputs. If the target code depends on advanced template or compiler-specific behavior outside libclang tolerance, full mode may still fail. That is acceptable for this phase as long as the diagnostics are explicit.

## Validation Checklist

Before implementation is considered complete:

- `.cpp` full-mode run succeeds on a representative fixture
- a compile database entry is correctly discovered and applied
- the system hard-fails on primary clang parse failure
- the error message tells the user what configuration was attempted
- existing C full-mode tests still pass
- no JSON schema changes are introduced

## Open Questions

These items do not block implementation but should be settled while coding:

- whether the explicit CLI flag should be named `--language` or `--clang-language`
- how aggressively to strip compile-db flags that libclang may reject
- whether to surface resolved clang config in output JSON, stderr only, or both

## Recommended Execution Order

1. Add `AnalysisOptions` and CLI support for language and compile database selection.
2. Implement compile database discovery and per-file flag resolution.
3. Refactor `cvas_clang.py` to build language-aware clang arguments centrally.
4. Enforce hard-failure behavior for main translation-unit parse errors.
5. Add regression coverage for C++ success and failure cases.

## Definition of Done

This work is done when:

- full mode no longer forces `.cpp` inputs through C mode
- C++ source files can be parsed with clang using discovered build flags
- primary clang parse failures stop the run immediately
- diagnostics clearly explain why full mode failed
- regression tests cover the new behavior
