#!/usr/bin/env python3
"""Main CVAS analysis pipeline.

This module holds the C-model lowering, control analysis, call graph
construction, and JSON model generation. The historic `src/cvas_mvp.py`
entry point now delegates here as a thin compatibility wrapper.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from cvas_analysis import AnalysisOptions
from cvas_callgraph import build_call_graph, build_call_sequence, find_function_calls
from cvas_gcc_dump import run_gcc_dump
from cvas_index import build_project_symbol_index
from cvas_model import (
    Block,
    CycleRules,
    Flow,
    Operation,
    Signal,
)
from cvas_passes import FunctionAnalysisResult, analyze_function, expand_simple_function_macros
from cvas_serialize import serialize_block, serialize_flow
from cvas_source import extract_cvas_region, find_cvas_region_bounds, find_function_definitions
from cvas_model import (
    BasicBlock,
    Block,
    CallGraph,
    CallGraphNode,
    ControlFlowGraph,
    CycleRules,
    Flow,
    LoopInfo,
    OpSummary,
    Operation,
    Signal,
)

# ============================================================================
# Constants
# ============================================================================

MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"

# ============================================================================
# Model Building - Enhanced
# ============================================================================


def build_model(
    source: str,
    rules: CycleRules,
    project_sources: Optional[List[Tuple[Path, str]]] = None,
    entry_file: Optional[Path] = None,
    analysis_options: AnalysisOptions = AnalysisOptions(),
) -> Dict[str, object]:
    """Build complete enhanced model with P1+P2 features."""

    source = expand_simple_function_macros(source)

    region, found = extract_cvas_region(source)
    if not found:
        print(
            f"WARNING: {MARKER_START} ~ {MARKER_END} region not found", file=sys.stderr
        )
        return {
            "blocks": [],
            "operations": [],
            "signals": [],
            "flow": {"execution_order": [], "parallelism": "unknown"},
            "diagram_hint": {"layout": "TBD by drawing tool"},
            "note": f"{MARKER_START}/{MARKER_END} region not found or empty",
        }

    if analysis_options.backend == "clang":
        region_bounds = find_cvas_region_bounds(source)
        region_functions = find_function_definitions(
            source,
            analysis_options=analysis_options,
            source_path=entry_file,
            region_bounds=region_bounds,
            required=True,
        )
    else:
        region_functions = find_function_definitions(
            region,
            analysis_options=analysis_options,
        )
    if not region_functions:
        print("WARNING: No functions found in CVAS region", file=sys.stderr)
        return {
            "blocks": [],
            "operations": [],
            "signals": [],
            "flow": {"execution_order": [], "parallelism": "unknown"},
            "diagram_hint": {"layout": "TBD by drawing tool"},
            "note": "No functions found in CVAS region",
        }

    entry_file_str = str(entry_file) if entry_file else "input"

    if project_sources:
        function_defs, duplicate_functions, symbol_index = build_project_symbol_index(
            project_sources,
            analysis_options=analysis_options,
        )
    else:
        function_defs = {
            name: (ret, name, params, body, entry_file_str)
            for ret, name, params, body in region_functions
        }
        duplicate_functions = []
        symbol_index = {}

    # Always prioritize the explicit CVAS region definitions from entry file.
    for ret, name, params, body in region_functions:
        function_defs[name] = (ret, name, params, body, entry_file_str)

    known_functions = set(function_defs.keys())
    seed_order = [name for _, name, _, _ in region_functions]

    analyzed_names: List[str] = []
    visited: Set[str] = set()
    queue: List[str] = list(seed_order)
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        func = function_defs.get(current)
        if func is None:
            continue
        visited.add(current)
        analyzed_names.append(current)
        _, _, _, body, _ = func
        calls, _ = find_function_calls(
            body,
            known_functions,
            analysis_options=analysis_options,
            source_path=Path(func[4]),
        )
        for callee_name, _, _ in calls:
            if callee_name not in visited:
                queue.append(callee_name)

    functions = [function_defs[name] for name in analyzed_names]
    block_ids = {name: f"B{idx + 1}" for idx, name in enumerate(analyzed_names)}

    blocks: List[Block] = []
    operations: List[Operation] = []
    signals: List[Signal] = []
    unresolved_calls: List[Dict[str, object]] = []
    external_symbols: List[Dict[str, object]] = []
    function_def_meta: Dict[str, Dict[str, object]] = {}
    analysis_results: Dict[str, FunctionAnalysisResult] = {}

    for ret_type, name, params, body, source_file in functions:
        block_id = block_ids[name]
        result = analyze_function(
            name=name,
            ret_type=ret_type,
            params=params,
            body=body,
            source_file=source_file,
            block_id=block_id,
            known_functions=known_functions,
            symbol_index=symbol_index,
            rules=rules,
            analysis_options=analysis_options,
        )
        analysis_results[name] = result
        blocks.append(result.block)
        operations.extend(result.operations)
        signals.extend(result.signals)
        unresolved_calls.extend(result.unresolved_calls)
        external_symbols.extend(result.external_symbols)
        function_def_meta[name] = result.function_def_meta

    # Analyze function calls for inter-block signals
    for _, caller_name, _, _, _ in functions:
        caller_id = block_ids[caller_name]
        calls = analysis_results[caller_name].calls

        for callee_name, args, assigned in calls:
            if callee_name not in block_ids:
                continue
            callee_id = block_ids[callee_name]

            for arg in args:
                signals.append(
                    Signal(
                        source_id=caller_id,
                        source_type="block",
                        destination_id=callee_id,
                        destination_type="block",
                        signal_name=arg or "unknown",
                        direction="in",
                        comment="argument flow",
                    )
                )

            if assigned:
                signals.append(
                    Signal(
                        source_id=callee_id,
                        source_type="block",
                        destination_id=caller_id,
                        destination_type="block",
                        signal_name=assigned,
                        direction="out",
                        comment="return flow",
                    )
                )

    # P2: Build call graph
    call_graph = build_call_graph(
        functions,
        block_ids,
        blocks,
        analysis_options=analysis_options,
    )
    call_sequence = build_call_sequence(
        functions,
        known_functions,
        analysis_options=analysis_options,
    )

    # Enhanced flow with call graph
    flow = Flow(
        execution_order=[block.block_id for block in blocks],
        parallelism="sequential",  # Can be enhanced with dependency analysis
        call_graph=call_graph,
        call_sequence=call_sequence,
        function_defs=function_def_meta,
        unresolved_calls=unresolved_calls,
        external_symbols=external_symbols,
    )

    analysis_note = "Enhanced with P1+P2: complete data flow, CFG, call graph"
    if duplicate_functions:
        analysis_note += (
            f"; duplicate function definitions detected: {len(duplicate_functions)}"
        )

    model: Dict[str, object] = {
        "blocks": [serialize_block(block) for block in blocks],
        "operations": [asdict(operation) for operation in operations],
        "signals": [asdict(signal) for signal in signals],
        "flow": serialize_flow(flow),
        "diagram_hint": {"layout": "TBD by drawing tool"},
        "note": analysis_note,
        "analysis_version": "2.0",
        "analysis_mode": analysis_options.mode,
        "analysis_backend": analysis_options.backend,
        "project_mode": bool(project_sources),
        "duplicate_functions": duplicate_functions,
    }
    if analysis_options.mode == "full":
        model["gcc_dump"] = run_gcc_dump(
            source,
            source_path=entry_file,
            analysis_options=analysis_options,
        )
    return model
