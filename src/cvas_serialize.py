from __future__ import annotations

from dataclasses import asdict
from typing import Dict

from cvas_model import Block, Flow


def serialize_block(block: Block) -> Dict[str, object]:
    """Serialize block with nested structures."""
    data = asdict(block)
    data["internal_ops_summary"] = asdict(block.internal_ops_summary)

    if block.cfg:
        data["cfg"] = {
            "function_name": block.cfg.function_name,
            "basic_blocks": [asdict(bb) for bb in block.cfg.basic_blocks],
            "entry_block": block.cfg.entry_block,
            "exit_blocks": block.cfg.exit_blocks,
            "loops": [asdict(loop) for loop in block.cfg.loops],
            "has_branches": block.cfg.has_branches,
            "max_nesting_depth": block.cfg.max_nesting_depth,
            "analysis_confidence": block.cfg.analysis_confidence,
            "analysis_coverage": block.cfg.analysis_coverage,
            "analysis_limitations": block.cfg.analysis_limitations,
        }

    return data


def serialize_flow(flow: Flow) -> Dict[str, object]:
    """Serialize flow with call graph."""
    data = {"execution_order": flow.execution_order, "parallelism": flow.parallelism}

    if flow.call_graph:
        data["call_graph"] = {
            "nodes": {
                name: asdict(node) for name, node in flow.call_graph.nodes.items()
            },
            "entry_functions": flow.call_graph.entry_functions,
            "call_chains": flow.call_graph.call_chains,
            "critical_path": flow.call_graph.critical_path,
            "max_depth": flow.call_graph.max_depth,
            "has_recursion": flow.call_graph.has_recursion,
            "analysis_confidence": flow.call_graph.analysis_confidence,
            "analysis_coverage": flow.call_graph.analysis_coverage,
            "analysis_limitations": flow.call_graph.analysis_limitations,
        }

    if flow.call_sequence is not None:
        data["call_sequence"] = flow.call_sequence

    if flow.function_defs is not None:
        data["function_defs"] = flow.function_defs

    if flow.unresolved_calls is not None:
        data["unresolved_calls"] = flow.unresolved_calls

    if flow.external_symbols is not None:
        data["external_symbols"] = flow.external_symbols

    return data
