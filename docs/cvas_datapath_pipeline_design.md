# CVAS Datapath Pipeline Design

## 1. Goal

CVAS is a C-model analysis tool for hardware-oriented datapath design.
Its primary output is a machine-readable JSON IR, and its secondary output is
a standalone HTML viewer for interactive inspection.

The first hardware target is:

- datapath-centric pipeline architecture
- control-flow aware
- initiation interval `II = 1`
- latency is allowed
- throughput should be one useful result per cycle after fill

## 2. System Boundary

CVAS should not be treated as a generic C compiler frontend.
It should instead act as an analysis pipeline that converts source code into
hardware-relevant facts:

- functions and call relations
- scalar and memory dependencies
- control flow and branch structure
- operation summaries and rough cycle cost
- pipeline candidates and hazards
- confidence and provenance for all derived facts

## 3. Output Contract

### JSON

The JSON output is the canonical contract between analysis, CLI automation, and
the HTML viewer.

Core fields:

- `blocks`
- `operations`
- `signals`
- `flow`

Recommended future extensions:

- `schema_version`
- `analysis_version`
- `static_analysis`
- `llm_annotations`
- `merged_analysis`
- `pipeline`
- `confidence`
- `conflicts`
- `assumptions`

### HTML

The HTML output is a consumer of the JSON contract.
It should visualize the model, not re-derive the semantics.

Viewer responsibilities:

- Diagram view for block / operation / signal structure
- Sequence view for function call order and dependency hints
- local/offline operation
- stable rendering from generated JSON

## 4. Analysis Architecture

### Stage A: Source Discovery

Responsibilities:

- extract the `CVAS_START` / `CVAS_END` region
- discover functions
- index project-level symbols when project mode is enabled
- normalize source enough for stable parsing

### Stage B: Static Analysis

Static analysis is the source of truth for facts.

Responsibilities:

- expression lowering
- operation classification
- control-flow extraction
- call graph construction
- data-flow edges
- unresolved/external symbol reporting
- basic cycle estimation

### Stage C: LLM Enrichment

LLM is optional and should be used as a semantic assistant.

Recommended uses:

- summarize function intent
- propose pipeline stage boundaries
- classify ambiguous memory intent
- annotate control intent
- suggest dependency candidates for review

LLM should not be the final authority for:

- exact data dependence
- aliasing decisions
- hazard existence
- cycle counts
- II=1 validity

### Stage D: Merge and Validation

Merge static facts and LLM annotations with static precedence.

Rules:

- static analysis wins on conflicts
- LLM output is treated as hypothesis unless verified
- final JSON should preserve provenance
- confidence should be explicit

### Stage E: Rendering

The HTML renderer should consume the merged JSON and provide:

- diagram rendering
- sequence rendering
- manual inspection of provenance/confidence
- offline usage with bundled assets

## 5. Hardware Interpretation

For the first target architecture, CVAS should answer:

- what the datapath is
- which operations can be staged in a pipeline
- where control decisions occur
- whether memory behavior threatens II=1
- what the likely critical path is
- which parts are unresolved and need user guidance

This is more useful than attempting a full RTL compiler on day one.

## 6. CLI Execution Model

The CLI only needs to make the tool usable in batch mode:

- input C model
- output JSON
- optional output HTML
- optional LLM enrichment step

Internal LLM execution can happen through:

- Codex CLI during development/testing
- OpenAI-compatible API in release environments
- a future CLI agent wrapper if needed

The user-facing CLI should remain file-oriented and deterministic in its contract.

## 7. Refactoring Plan

### Phase 1: Contract Extraction

Completed in the current refactor:

- move shared IR dataclasses into `src/cvas_model.py`
- move project indexing helpers into `src/cvas_index.py`
- move control-flow analysis into `src/cvas_cfg.py`
- move call-graph analysis into `src/cvas_callgraph.py`
- move expression lowering helpers into `src/cvas_expr.py`
- move serialization helpers into `src/cvas_serialize.py`
- move shared function-discovery helpers into `src/cvas_source.py`
- move shared statement/string helpers into `src/cvas_text.py`
- keep JSON output unchanged
- keep HTML output unchanged

### Phase 2: Pipeline Separation

- keep `src/cvas_mvp.py` as a thin CLI wrapper
- move CLI argument parsing / file I/O into `src/cvas_cli.py`
- move function-level preprocessing/lowering into `src/cvas_passes.py`
- keep `src/cvas_pipeline.py` as orchestration and model assembly
- isolate project indexing, source lowering, CFG, call graph, and serialization
- introduce a dedicated pipeline object that can carry static and LLM artifacts

### Phase 3: LLM Orchestration

Next step:

- define provider-neutral LLM interfaces
- support Codex CLI, OpenAI-compatible API, and later CLI-agent execution
- store provenance for each LLM-derived annotation

### Phase 4: Pipeline Scheduling

Next step:

- add a pipeline model for `II = 1`
- represent control, data, and memory hazards explicitly
- use the model to explain where throughput is preserved or broken

## 8. Open Questions

- Which C subset should be considered supported by default?
- How strict should the tool be when aliasing is unclear?
- Should the first release prioritize throughput analysis or control accuracy?
- What should be the minimum confidence threshold for LLM-derived annotations?
