#!/usr/bin/env python3
"""Main CVAS analysis pipeline.

This module holds the C-model lowering, control analysis, call graph
construction, and JSON model generation. The historic `src/cvas_mvp.py`
entry point now delegates here as a thin compatibility wrapper.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from cvas_analysis import AnalysisOptions
from cvas_callgraph import build_call_graph, find_function_calls
from cvas_index import build_project_symbol_index
from cvas_model import (
    Block,
    CallArgument,
    CallAssignment,
    CallInstance,
    CycleRules,
    Flow,
    Operation,
    SequenceTimelineStep,
    Signal,
)
from cvas_passes import FunctionAnalysisResult, analyze_function, expand_simple_function_macros
from cvas_serialize import serialize_block, serialize_flow, serialize_signal
from cvas_source import extract_cvas_region, find_cvas_region_bounds, find_function_definitions
from cvas_text import parse_params, split_top_level_commas

# ============================================================================
# Constants
# ============================================================================

MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"
SCHEMA_VERSION = "3.0"
SCHEMA_METADATA = {
    "name": "cvas-analysis",
    "version": SCHEMA_VERSION,
    "compatibility": {
        "preserves_v2_fields": True,
    },
}
EXECUTION_ORDER_META = {
    "kind": "static_block_order",
    "source": "analysis_queue",
    "description": "Static function/block order used for visualization; not a dynamic runtime trace.",
}

# ============================================================================
# Schema v3 helpers
# ============================================================================


def _schema_header() -> Dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "schema": SCHEMA_METADATA,
    }


def _empty_flow(function_io: Optional[Dict[str, object]] = None) -> Flow:
    return Flow(
        execution_order=[],
        parallelism="unknown",
        execution_order_meta=EXECUTION_ORDER_META,
        call_sequence=[],
        call_instances=[],
        sequence_timeline=[],
        function_io=function_io or {"source": "rule-based", "functions": {}},
        dependencies={"inter_block": [], "call_instance": []},
        unresolved_calls=[],
        external_symbols=[],
    )


def _empty_model(
    *,
    note: str,
    analysis_options: AnalysisOptions,
    project_mode: bool,
    function_io: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    model = {
        **_schema_header(),
        "blocks": [],
        "operations": [],
        "signals": [],
        "flow": serialize_flow(_empty_flow(function_io)),
        "diagram_hint": {"layout": "TBD by drawing tool"},
        "note": note,
        "analysis_version": "2.0",
        "analysis_mode": analysis_options.mode,
        "analysis_backend": analysis_options.backend,
        "project_mode": project_mode,
        "duplicate_functions": [],
    }
    return model


def _parse_param_specs(params: str) -> List[str]:
    params = params.strip()
    if not params or params == "void":
        return []
    return [item.strip() for item in split_top_level_commas(params) if item.strip()]


def _param_name_from_spec(spec: str) -> Optional[str]:
    compact = " ".join(spec.strip().split())
    if not compact:
        return None
    match = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?$", compact)
    if match:
        return match.group(1)
    return None


def _build_rule_function_io(
    functions: List[Tuple[str, str, str, str, str]]
) -> Dict[str, object]:
    function_io: Dict[str, Dict[str, object]] = {}
    for _, name, params, _, _ in functions:
        reads: List[str] = []
        writes: List[str] = []
        for spec in _parse_param_specs(params):
            param_name = _param_name_from_spec(spec)
            if not param_name:
                continue
            compact = " ".join(spec.split())
            is_pointer_like = "*" in compact or "[" in compact
            is_const = "const" in compact.split()
            reads.append(param_name)
            if is_pointer_like and not is_const:
                writes.append(param_name)
        function_io[name] = {
            "reads": sorted(set(reads)),
            "writes": sorted(set(writes)),
            "provenance": {
                "source": "rule-based",
                "confidence": "medium",
            },
        }
    return {"source": "rule-based", "functions": function_io}


def normalize_function_io(
    raw_function_io: Optional[Dict[str, object]],
    functions: List[Tuple[str, str, str, str, str]],
) -> Dict[str, object]:
    """Normalize optional function IO input into the schema v3 envelope."""
    if raw_function_io is None:
        return _build_rule_function_io(functions)

    source = "embedded_file"
    if isinstance(raw_function_io.get("source"), str):
        source = str(raw_function_io["source"])

    raw_functions = raw_function_io.get("functions")
    if not isinstance(raw_functions, dict):
        raw_functions = raw_function_io

    normalized: Dict[str, Dict[str, object]] = {}
    if isinstance(raw_functions, dict):
        for name, value in raw_functions.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                continue
            reads = value.get("reads", [])
            writes = value.get("writes", [])
            normalized[name] = {
                "reads": sorted(str(item) for item in reads) if isinstance(reads, list) else [],
                "writes": sorted(str(item) for item in writes) if isinstance(writes, list) else [],
                "provenance": value.get(
                    "provenance",
                    {
                        "source": source,
                        "confidence": "medium",
                    },
                ),
            }

    return {"source": source, "functions": normalized}


def _infer_internal_signal_kind(signal: Signal) -> str:
    comment = (signal.comment or "").lower()
    if signal.destination_type == "block" and signal.direction == "out":
        return "function_return"
    if "store" in comment:
        return "internal_store"
    if "copy" in comment:
        return "internal_copy"
    if "return" in comment:
        return "function_return"
    if "operand" in comment or "load" in comment or "const" in comment:
        return "internal_operand"
    return "unknown"


def _infer_internal_signal_role(signal: Signal) -> str:
    kind = signal.kind or _infer_internal_signal_kind(signal)
    if kind in {"internal_store", "function_return"}:
        return "write"
    if kind in {"internal_operand", "internal_copy"}:
        return "read"
    return "unknown"


def _enrich_internal_signals(result: FunctionAnalysisResult) -> None:
    for index, signal in enumerate(result.signals, start=1):
        if signal.signal_id is None:
            signal.signal_id = f"S_{result.block.block_id}_{index:04d}"
        if signal.kind is None:
            signal.kind = _infer_internal_signal_kind(signal)
        if signal.role is None:
            signal.role = _infer_internal_signal_role(signal)
        if signal.source_function is None:
            signal.source_function = result.name
        if signal.destination_function is None:
            signal.destination_function = result.name
        if signal.provenance is None:
            signal.provenance = {
                "source": "static",
                "confidence": "medium",
            }


def _call_source_text(callee_name: str, args: List[str], assigned: Optional[str]) -> str:
    call_text = f"{callee_name}({', '.join(args)})"
    if assigned:
        return f"{assigned} = {call_text}"
    return call_text


def build_call_instances(
    functions: List[Tuple[str, str, str, str, str]],
    block_ids: Dict[str, str],
    analysis_results: Dict[str, FunctionAnalysisResult],
) -> List[CallInstance]:
    """Build stable schema v3 call instances from analyzed function calls."""
    params_by_name = {
        name: parse_params(params) for _, name, params, _, _ in functions
    }
    call_instances: List[CallInstance] = []

    for _, caller_name, _, _, source_file in functions:
        caller_id = block_ids[caller_name]
        ordinal = 0
        for callee_name, args, assigned in analysis_results[caller_name].calls:
            if callee_name not in block_ids:
                continue
            ordinal += 1
            call_id = f"C_{caller_id}_{ordinal:04d}"
            callee_params = params_by_name.get(callee_name, [])
            call_args = [
                CallArgument(
                    arg_index=index,
                    param=callee_params[index] if index < len(callee_params) else None,
                    expr=arg,
                    signal_id=f"S_{call_id}_ARG_{index}",
                )
                for index, arg in enumerate(args)
            ]
            assigned_obj = (
                CallAssignment(target=assigned, signal_id=f"S_{call_id}_RET")
                if assigned
                else None
            )
            call_instances.append(
                CallInstance(
                    call_id=call_id,
                    caller_block_id=caller_id,
                    caller_function=caller_name,
                    callee_block_id=block_ids.get(callee_name),
                    callee_function=callee_name,
                    ordinal_in_caller=ordinal,
                    args=call_args,
                    assigned=assigned_obj,
                    source={
                        "file": source_file,
                        "line": None,
                        "column": None,
                        "text": _call_source_text(callee_name, args, assigned),
                    },
                    provenance={
                        "source": "static",
                        "parser": "ast",
                        "confidence": "high",
                    },
                )
            )

    return call_instances


def build_legacy_call_sequence(
    functions: List[Tuple[str, str, str, str, str]],
    call_instances: List[CallInstance],
) -> List[Dict[str, object]]:
    """Project v3 call instances back to the legacy grouped call_sequence."""
    params_by_name = {
        name: parse_params(params) for _, name, params, _, _ in functions
    }
    by_caller: Dict[str, List[CallInstance]] = {}
    for call in call_instances:
        by_caller.setdefault(call.caller_function, []).append(call)

    sequence: List[Dict[str, object]] = []
    for _, function_name, _, _, _ in functions:
        calls = []
        for call in by_caller.get(function_name, []):
            calls.append(
                {
                    "callee": call.callee_function,
                    "args": [arg.expr for arg in call.args],
                    "assigned": call.assigned.target if call.assigned else None,
                    "callee_params": params_by_name.get(call.callee_function, []),
                    "call_id": call.call_id,
                }
            )
        sequence.append({"function": function_name, "calls": calls})
    return sequence


def build_call_signals(call_instances: List[CallInstance]) -> List[Signal]:
    """Build enriched inter-block call argument and return signals."""
    signals: List[Signal] = []
    for call in call_instances:
        if call.callee_block_id is None:
            continue
        for arg in call.args:
            signals.append(
                Signal(
                    source_id=call.caller_block_id,
                    source_type="block",
                    destination_id=call.callee_block_id,
                    destination_type="block",
                    signal_name=arg.expr or "unknown",
                    direction="in",
                    comment="argument flow",
                    signal_id=arg.signal_id,
                    kind="call_argument",
                    role="read",
                    call_id=call.call_id,
                    arg_index=arg.arg_index,
                    param=arg.param,
                    expr=arg.expr,
                    source_function=call.caller_function,
                    destination_function=call.callee_function,
                    provenance={
                        "source": "static",
                        "confidence": "high",
                    },
                )
            )

        if call.assigned:
            signals.append(
                Signal(
                    source_id=call.callee_block_id,
                    source_type="block",
                    destination_id=call.caller_block_id,
                    destination_type="block",
                    signal_name=call.assigned.target,
                    direction="out",
                    comment="return flow",
                    signal_id=call.assigned.signal_id,
                    kind="call_return",
                    role="write",
                    call_id=call.call_id,
                    expr=call.assigned.target,
                    target=call.assigned.target,
                    source_function=call.callee_function,
                    destination_function=call.caller_function,
                    provenance={
                        "source": "static",
                        "confidence": "high",
                    },
                )
            )
    return signals


def _compact_item(data: Dict[str, object]) -> Dict[str, object]:
    return {key: value for key, value in data.items() if value is not None}


def _signal_summary_item(signal: Signal, other_block_id: str, other_function: Optional[str]) -> Dict[str, object]:
    return _compact_item(
        {
            "signal_id": signal.signal_id,
            "call_id": signal.call_id,
            "kind": signal.kind,
            "role": signal.role,
            "signal_name": signal.signal_name,
            "expr": signal.expr,
            "target": signal.target,
            "param": signal.param,
            "arg_index": signal.arg_index,
            "other_block_id": other_block_id,
            "other_function": other_function,
        }
    )


def build_sequence_timeline(
    blocks: List[Block],
    call_instances: List[CallInstance],
    signals: List[Signal],
) -> List[SequenceTimelineStep]:
    """Build one schema v3 timeline step per static execution-order block."""
    function_by_block = {block.block_id: block.block_name for block in blocks}
    calls_as_caller: Dict[str, List[str]] = {block.block_id: [] for block in blocks}
    calls_as_callee: Dict[str, List[str]] = {block.block_id: [] for block in blocks}

    for call in call_instances:
        calls_as_caller.setdefault(call.caller_block_id, []).append(call.call_id)
        if call.callee_block_id:
            calls_as_callee.setdefault(call.callee_block_id, []).append(call.call_id)

    timeline: List[SequenceTimelineStep] = []
    for order_index, block in enumerate(blocks):
        incoming_signal_ids: List[str] = []
        outgoing_signal_ids: List[str] = []
        read_write_summary: Dict[str, List[Dict[str, object]]] = {
            "reads_from_other": [],
            "read_by_other": [],
            "writes_to_other": [],
            "written_by_other": [],
        }

        for signal in signals:
            if not signal.signal_id:
                continue
            if signal.destination_id == block.block_id:
                incoming_signal_ids.append(signal.signal_id)
            if signal.source_id == block.block_id:
                outgoing_signal_ids.append(signal.signal_id)

            if signal.kind == "call_argument":
                if signal.destination_id == block.block_id:
                    read_write_summary["reads_from_other"].append(
                        _signal_summary_item(
                            signal,
                            signal.source_id,
                            function_by_block.get(signal.source_id),
                        )
                    )
                if signal.source_id == block.block_id:
                    read_write_summary["read_by_other"].append(
                        _signal_summary_item(
                            signal,
                            signal.destination_id,
                            function_by_block.get(signal.destination_id),
                        )
                    )
            elif signal.kind == "call_return":
                if signal.source_id == block.block_id:
                    read_write_summary["writes_to_other"].append(
                        _signal_summary_item(
                            signal,
                            signal.destination_id,
                            function_by_block.get(signal.destination_id),
                        )
                    )
                if signal.destination_id == block.block_id:
                    read_write_summary["written_by_other"].append(
                        _signal_summary_item(
                            signal,
                            signal.source_id,
                            function_by_block.get(signal.source_id),
                        )
                    )

        timeline.append(
            SequenceTimelineStep(
                step_id=f"T_{order_index:04d}_{block.block_id}",
                order_index=order_index,
                block_id=block.block_id,
                function=block.block_name,
                call_ids_as_caller=calls_as_caller.get(block.block_id, []),
                call_ids_as_callee=calls_as_callee.get(block.block_id, []),
                incoming_signal_ids=incoming_signal_ids,
                outgoing_signal_ids=outgoing_signal_ids,
                read_write_summary=read_write_summary,
            )
        )

    return timeline


def build_dependencies(signals: List[Signal]) -> Dict[str, List[Dict[str, object]]]:
    """Build lightweight dependency indexes for v3 consumers."""
    inter_block: List[Dict[str, object]] = []
    call_instance: List[Dict[str, object]] = []
    for signal in signals:
        if not signal.signal_id:
            continue
        if signal.source_type == "block" and signal.destination_type == "block":
            inter_block.append(
                _compact_item(
                    {
                        "signal_id": signal.signal_id,
                        "source_id": signal.source_id,
                        "destination_id": signal.destination_id,
                        "kind": signal.kind,
                        "role": signal.role,
                        "call_id": signal.call_id,
                    }
                )
            )
        if signal.call_id:
            call_instance.append(
                _compact_item(
                    {
                        "call_id": signal.call_id,
                        "signal_id": signal.signal_id,
                        "kind": signal.kind,
                        "role": signal.role,
                    }
                )
            )
    return {"inter_block": inter_block, "call_instance": call_instance}


# ============================================================================
# Model Building - Enhanced
# ============================================================================


def build_model(
    source: str,
    rules: CycleRules,
    project_sources: Optional[List[Tuple[Path, str]]] = None,
    entry_file: Optional[Path] = None,
    analysis_options: AnalysisOptions = AnalysisOptions(),
    function_io: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Build complete enhanced model with P1+P2 features and schema v3 facts."""

    source = expand_simple_function_macros(source)

    region, found = extract_cvas_region(source)
    if not found:
        print(
            f"WARNING: {MARKER_START} ~ {MARKER_END} region not found", file=sys.stderr
        )
        return _empty_model(
            note=f"{MARKER_START}/{MARKER_END} region not found or empty",
            analysis_options=analysis_options,
            project_mode=bool(project_sources),
            function_io=function_io,
        )

    if analysis_options.mode == "full":
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
        return _empty_model(
            note="No functions found in CVAS region",
            analysis_options=analysis_options,
            project_mode=bool(project_sources),
            function_io=function_io,
        )

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
        _enrich_internal_signals(result)
        analysis_results[name] = result
        blocks.append(result.block)
        operations.extend(result.operations)
        signals.extend(result.signals)
        unresolved_calls.extend(result.unresolved_calls)
        external_symbols.extend(result.external_symbols)
        function_def_meta[name] = result.function_def_meta

    call_instances = build_call_instances(functions, block_ids, analysis_results)
    signals.extend(build_call_signals(call_instances))

    # P2: Build call graph
    call_graph = build_call_graph(
        functions,
        block_ids,
        blocks,
        analysis_options=analysis_options,
    )
    call_sequence = build_legacy_call_sequence(functions, call_instances)
    normalized_function_io = normalize_function_io(function_io, functions)
    sequence_timeline = build_sequence_timeline(blocks, call_instances, signals)
    dependencies = build_dependencies(signals)

    # Enhanced flow with call graph and schema v3 timeline facts.
    flow = Flow(
        execution_order=[block.block_id for block in blocks],
        parallelism="sequential",  # Can be enhanced with dependency analysis
        execution_order_meta=EXECUTION_ORDER_META,
        call_graph=call_graph,
        call_sequence=call_sequence,
        call_instances=call_instances,
        sequence_timeline=sequence_timeline,
        function_io=normalized_function_io,
        dependencies=dependencies,
        function_defs=function_def_meta,
        unresolved_calls=unresolved_calls,
        external_symbols=external_symbols,
    )

    analysis_note = "Enhanced with P1+P2 and schema v3 sequence timeline"
    if duplicate_functions:
        analysis_note += (
            f"; duplicate function definitions detected: {len(duplicate_functions)}"
        )

    return {
        **_schema_header(),
        "blocks": [serialize_block(block) for block in blocks],
        "operations": [asdict(operation) for operation in operations],
        "signals": [serialize_signal(signal) for signal in signals],
        "flow": serialize_flow(flow),
        "diagram_hint": {"layout": "TBD by drawing tool"},
        "note": analysis_note,
        "analysis_version": "2.0",
        "analysis_mode": analysis_options.mode,
        "analysis_backend": analysis_options.backend,
        "project_mode": bool(project_sources),
        "duplicate_functions": duplicate_functions,
    }
