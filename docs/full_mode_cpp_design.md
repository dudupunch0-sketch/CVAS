# CVAS Full-Mode C++ Design

## 1. Building

This design adds reliable C and C++ support to CVAS `--analysis-mode full` by making clang configuration explicit, reusable, and build-context aware.

The immediate objective is:

- accept `.c`, `.cc`, `.cpp`, and `.cxx` as full-mode entry files
- parse C++ sources in C++ mode instead of forcing C mode
- apply per-file build flags from `compile_commands.json` when available
- preserve explicit user `--clang-arg` overrides
- hard-fail the overall run when the primary translation unit cannot be parsed by clang

The target codebase is known to be `c++11`, so the fallback C++ standard in this design is `c++11`, not `c++17`.

## 2. Not Building

This design does not attempt to build:

- fast-mode C++ support through `pycparser`
- guaranteed support for template-heavy or compiler-extension-heavy C++
- header files as top-level CVAS entry files
- a full compiler driver replacement
- linker-aware or whole-build semantic analysis
- JSON schema changes

## 3. Problem Statement

Current full mode always constructs clang arguments as C:

- `-xc`
- `-std=c11`
- C-flavored wrapper filenames such as `__cvas_region__.c`

That behavior is incompatible with C++ inputs and also ignores the build system as the primary source of truth for:

- include paths
- preprocessor defines
- forced includes
- language standard
- target-specific compile flags

The design therefore needs to solve two distinct problems:

- language correctness
- build-context correctness

## 4. Approach

Introduce a shared resolved clang configuration object that is created once per full-mode run and then passed through every clang-backed analysis path.

That configuration must represent:

- entry file
- resolved language
- resolved standard
- compile database path and matched entry, if any
- merged clang arguments
- wrapper filename suffix
- diagnostic metadata used for failure reporting

This keeps the system aligned around one source of truth instead of allowing each helper path to guess language and flags independently.

## 5. Architecture

```text
CLI
  -> AnalysisOptions (user intent)
  -> resolve_clang_config(entry_file, project_root, compile_db, clang_args)
  -> ResolvedClangConfig
       -> cvas_source clang parse
       -> cvas_callgraph clang wrapper parse
       -> cvas_passes condition wrapper parse
       -> cvas_pipeline error reporting
```

Component responsibilities:

- `cvas_cli.py`
  - collect user intent
  - reject impossible entry-file cases early
  - build `AnalysisOptions`
- `cvas_analysis.py`
  - define analysis-time configuration types
  - hold user intent and resolved clang state
- `cvas_compile_db.py`
  - discover and load `compile_commands.json`
  - match a file to its compile command
  - normalize compile flags for libclang
- `cvas_clang.py`
  - build translation units from `ResolvedClangConfig`
  - classify primary parse failure vs helper parse failure
  - expose consistent diagnostics
- `cvas_source.py`, `cvas_callgraph.py`, `cvas_passes.py`
  - stop inventing their own clang mode
  - use the shared config

## 6. Data Model

### AnalysisOptions

`AnalysisOptions` remains the user-facing configuration object. It should carry raw intent, not resolved state.

Fields to add:

- `language_override: Optional[str]`
- `compile_db_path: Optional[str]`

Fields to keep:

- `mode`
- `clang_args`

Rationale:

- raw user input should remain serializable and easy to test
- resolved decisions should not be recomputed at every callsite

### ResolvedClangConfig

Add a dedicated resolved configuration type. This can live in `cvas_analysis.py` unless it becomes too large, in which case it can move to a dedicated module later.

Fields:

- `entry_file: Path`
- `language: str`
- `standard: str`
- `wrapper_suffix: str`
- `compile_db_path: Optional[Path]`
- `compile_db_command_file: Optional[Path]`
- `compile_db_raw_args: Tuple[str, ...]`
- `user_clang_args: Tuple[str, ...]`
- `final_clang_args: Tuple[str, ...]`
- `flag_sources: Dict[str, str]`

The important split is:

- raw compile-db args for debugging
- user override args for precedence tracing
- final args for libclang execution

## 7. Language Resolution

Resolution order:

1. `--language` override
2. entry-file suffix inference
3. compile-db `-x` hint if the file extension is ambiguous

Rules:

- `.c` resolves to `c`
- `.cc`, `.cpp`, `.cxx` resolve to `c++`
- `.h` and `.hpp` are not valid full-mode entry files in this phase
- wrapper suffix follows the resolved language:
  - `.c` for C
  - `.cpp` for C++

Why wrapper suffix matters:

- it keeps libclang diagnostics and internal language assumptions aligned
- it avoids helper wrappers accidentally slipping back into C mode

## 8. Standard Resolution

Resolution order:

1. explicit user `--clang-arg=-std=...`
2. matched compile-db `-std=...`
3. inferred default

Defaults:

- `c11` for C
- `c++11` for C++

This precedence allows an explicit user override to repair or override the build configuration when needed, while still treating the compile database as the default source of build truth.

If both compile-db and explicit CLI args specify `-std=`, the user-supplied `--clang-arg` wins and the diagnostic report should show that an override happened.

## 9. Compile Database Design

### Discovery

Compile database discovery order:

1. explicit `--clang-compile-db`
2. `project_root/compile_commands.json`
3. nearest parent search upward from the entry file directory

If a database is found but the file is not present in it:

- continue with inferred language and fallback defaults
- record that no matching compile command existed

If no database is found:

- continue with inferred defaults
- hard-fail only if clang still cannot parse the primary translation unit

### File matching

Match using normalized absolute paths first. If that fails, use normalized relative paths from the compile-db working directory as a secondary strategy.

This matters because many compile databases record:

- relative source paths
- generated build directory paths
- symlinked paths

### Flag normalization

The compile-db command should be normalized into libclang-safe arguments.

Preserve:

- `-I`
- `-isystem`
- `-include`
- `-D`
- `-U`
- `-std=`
- target and sysroot flags when valid for libclang
- language-driver flags that affect parsing

Strip or ignore when constructing the parse command:

- `-c`
- output flags such as `-o`
- dependency-generation flags
- flags whose only purpose is emission or linking
- the source file argument itself
- the compiler executable path

Normalization has to be explicit because `compile_commands.json` is driver-oriented, while libclang parse arguments are frontend-oriented.

## 10. Argument Merge Policy

Argument assembly order:

1. inferred language marker `-x...`
2. resolved standard if not already represented by a winning higher-priority source
3. normalized compile-db args
4. explicit user `--clang-arg`

Conflict rule:

- user `--clang-arg` wins

Diagnostic rule:

- every overridden `-std`, `-x`, include path, or define conflict should be traceable in the resolved config

This gives users a way to intentionally repair broken compile databases without hiding what happened.

## 11. Parsing API Design

The current `_parse_translation_unit(source, filename, clang_args)` shape is too weak because it hides language and standard decisions inside a flat arg list.

The new parsing API should receive `ResolvedClangConfig` directly for all clang-backed entry points.

Target shape:

- primary TU parse
- function definition parse
- wrapper statement parse
- wrapper call parse

All of them should call one central helper that:

- assembles final parse args
- chooses the wrapper filename suffix
- runs libclang
- returns diagnostics in a consistent shape

This removes duplicated configuration logic and makes failure policy enforceable.

## 12. Failure Model

### Primary translation unit

If the main entry file cannot be parsed by clang in full mode, the run must fail immediately.

This is a hard error because full mode without a valid clang translation unit is semantically untrustworthy.

### Helper wrappers

If a helper wrapper parse fails after the primary TU has already parsed successfully:

- helper analysis may fall back to the existing non-clang behavior
- the limitation must be recorded in metadata
- the fallback must not hide a previously detected primary failure

This preserves utility without violating the meaning of full mode.

## 13. Diagnostics Design

Every primary full-mode failure should report:

- entry file path
- resolved language
- resolved standard
- compile-db path used, if any
- whether a compile-db entry matched the file
- final clang argument summary
- de-duplicated clang diagnostics

Recommended stderr layout:

- one summary line
- one configuration block
- one diagnostics block

The purpose is operational clarity. Users need to distinguish:

- wrong language mode
- wrong standard
- missing include paths
- missing generated headers
- bad compile database entries
- unsupported source constructs

## 14. CLI Behavior

Add:

- `--language c|c++`
- `--clang-compile-db <path>`

Behavior:

- these options are meaningful only in `--analysis-mode full`
- in fast mode they should be rejected or ignored with a clear warning
- a header entry file in full mode should fail before analysis starts

## 15. File-Level Design Changes

### `src/cvas_analysis.py`

- extend `AnalysisOptions`
- define `ResolvedClangConfig`
- expose a shared resolver entry point or host the data structures used by the resolver

### `src/cvas_cli.py`

- parse new CLI flags
- resolve entry-file language constraints
- create the resolved clang config once
- attach it to full-mode analysis flow

### `src/cvas_compile_db.py`

- discover compile database
- parse command or arguments arrays
- normalize file match rules
- produce normalized libclang args and provenance

### `src/cvas_clang.py`

- remove hardcoded C-only parse defaults
- centralize arg construction from `ResolvedClangConfig`
- separate primary parse errors from helper-wrapper errors
- expose shared diagnostics helpers

### `src/cvas_source.py`

- stop calling clang with bare `clang_args`
- use resolved clang config for function discovery

### `src/cvas_callgraph.py`

- use resolved clang config for wrapper-based call extraction

### `src/cvas_passes.py`

- use resolved clang config for condition extraction helpers

### `src/cvas_pipeline.py`

- propagate primary parse failures as hard full-mode failures
- include resolved diagnostic metadata in error reporting

## 16. Test Design

Required tests:

- minimal `.cpp` fixture parses in full mode
- minimal `.cc` fixture parses in full mode
- default fallback standard is `c++11`
- explicit `--clang-arg=-std=c++17` overrides the default
- compile database is discovered from project root
- compile database is discovered by upward search
- explicit compile-db path is honored
- compile-db file found but no matching source entry is reported clearly
- required include path missing causes hard failure in full mode
- header entry file causes early hard failure
- existing `.c` full-mode behavior remains valid

The test suite should verify both:

- success behavior
- failure messaging

## 17. Rollback and Risk

Rollback cost is low because:

- output JSON is unchanged
- changes are confined to configuration and parse orchestration
- C full-mode remains on the same architecture

The main risk is flag normalization being either too permissive or too aggressive. If it strips too much, valid projects fail. If it keeps too much, libclang may reject driver-only flags. This is why diagnostics and test coverage around compile-db ingestion are central to the design.

## 18. Unknowns

These items are intentionally deferred but should be handled during implementation review:

- exact allowlist vs denylist strategy for compile-db flag normalization
- whether resolved clang config should be exposed in JSON output or stderr only
- whether compile-db lookup results should be cached across helper parses

## 19. Approval Target

Implementation should start only after the following design decisions are accepted:

- full mode uses a shared resolved clang configuration
- C++ fallback standard is `c++11`
- compile database support is first-class
- primary clang parse failure is a hard error
- helper wrapper failure may fall back only after primary parse success
