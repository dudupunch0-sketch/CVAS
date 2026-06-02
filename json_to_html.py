#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = ["blocks", "operations", "signals", "flow"]
SEQUENCE_RENDERER_V3_TIMELINE = "v3_timeline"
SEQUENCE_RENDERER_LEGACY = "legacy"


def detect_schema_version(data: dict) -> str:
    if isinstance(data, dict):
        schema_version = data.get("schema_version")
        if isinstance(schema_version, str):
            return schema_version
        schema = data.get("schema")
        if isinstance(schema, dict) and isinstance(schema.get("version"), str):
            return schema["version"]
    return "2.x"


def normalize_function_io_map(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    functions = value.get("functions")
    if isinstance(functions, dict):
        return functions
    return value


def get_viewer_function_io_map(data: dict, state_function_io: object | None = None) -> dict:
    flow = data.get("flow") if isinstance(data, dict) else {}
    flow_io = flow.get("function_io") if isinstance(flow, dict) else None
    flow_map = normalize_function_io_map(flow_io)
    if flow_map:
        return flow_map
    return normalize_function_io_map(state_function_io)


def select_sequence_renderer(data: dict) -> str:
    flow = data.get("flow") if isinstance(data, dict) else {}
    timeline = flow.get("sequence_timeline") if isinstance(flow, dict) else None
    if isinstance(timeline, list) and timeline:
        return SEQUENCE_RENDERER_V3_TIMELINE
    return SEQUENCE_RENDERER_LEGACY


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _call_arg_expr_by_param(call: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    args = call.get("args") if isinstance(call, dict) else None
    if not isinstance(args, list):
        return mapping
    for index, arg in enumerate(args):
        if isinstance(arg, dict):
            param = arg.get("param")
            expr = arg.get("expr")
            if param is not None and expr is not None:
                mapping[str(param)] = str(expr)
        elif arg is not None:
            callee_params = call.get("callee_params")
            if isinstance(callee_params, list) and index < len(callee_params):
                mapping[str(callee_params[index])] = str(arg)
    return mapping


def _map_call_io_names(names: list[str], call: dict | None) -> list[str]:
    if not call:
        return names
    expr_by_param = _call_arg_expr_by_param(call)
    return [expr_by_param.get(name, name) for name in names]


def _timeline_io_item(
    function_io_map: dict,
    function_name: object,
    role: str,
    call: dict | None = None,
) -> dict | None:
    if not isinstance(function_name, str) or not function_name:
        return None
    io = function_io_map.get(function_name)
    if not isinstance(io, dict):
        return None
    contract_reads = _as_string_list(io.get("reads"))
    contract_writes = _as_string_list(io.get("writes"))
    item = {
        "role": role,
        "function": function_name,
        "reads": _map_call_io_names(contract_reads, call),
        "writes": _map_call_io_names(contract_writes, call),
        "contract_reads": contract_reads,
        "contract_writes": contract_writes,
    }
    if call:
        item["call_id"] = call.get("call_id")
        item["caller_function"] = call.get("caller_function")
        item["callee_function"] = call.get("callee_function")
        assigned = call.get("assigned")
        if isinstance(assigned, dict) and assigned.get("target") is not None:
            item["assigned"] = str(assigned["target"])
    provenance = io.get("provenance")
    if isinstance(provenance, dict):
        item["provenance"] = provenance
    return item


def summarize_timeline_function_io(
    data: dict,
    step: dict,
    state_function_io: object | None = None,
) -> list[dict]:
    """Return the v3 viewer's function_io summary for a timeline card."""
    function_io_map = get_viewer_function_io_map(data, state_function_io)
    if not function_io_map or not isinstance(step, dict):
        return []

    flow = data.get("flow") if isinstance(data, dict) else {}
    call_instances = flow.get("call_instances") if isinstance(flow, dict) else []
    call_by_id = {
        call.get("call_id"): call
        for call in call_instances
        if isinstance(call, dict) and call.get("call_id") is not None
    }

    summary: list[dict] = []
    step_item = _timeline_io_item(function_io_map, step.get("function"), "step_function")
    if step_item:
        summary.append(step_item)

    for call_id in step.get("call_ids_as_caller", []) or []:
        call = call_by_id.get(call_id)
        if not isinstance(call, dict):
            continue
        item = _timeline_io_item(
            function_io_map,
            call.get("callee_function"),
            "called_function",
            call,
        )
        if item:
            summary.append(item)

    for call_id in step.get("call_ids_as_callee", []) or []:
        call = call_by_id.get(call_id)
        if not isinstance(call, dict):
            continue
        item = _timeline_io_item(
            function_io_map,
            call.get("caller_function"),
            "caller_function",
            call,
        )
        if item:
            summary.append(item)

    return summary


def _as_sequence_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _sequence_label_for_signal(signal: dict, fallback: object) -> str:
    for key in ("signal_name", "target", "expr", "param", "signal_id"):
        value = signal.get(key)
        if value not in (None, ""):
            return str(value)
    if fallback not in (None, ""):
        return str(fallback)
    return "signal"


def _merge_sequence_labels(labels: list[str]) -> str:
    unique: list[str] = []
    for label in labels:
        if label and label not in unique:
            unique.append(label)
    if not unique:
        return "signal"
    if len(unique) <= 2:
        return ", ".join(unique)
    return f"{unique[0]} + {len(unique) - 1} more"


def build_sequence_execution_model(data: dict) -> dict:
    """Return a viewer-ready v3 Sequence execution board model.

    This is a static viewer model built from existing Schema v3 facts. It
    exposes three layouts over the same steps/edges:
    - call: root/caller-left view for human execution-flow intuition.
    - dependency: producer/consumer static dependency progression.
    - pipeline: dependency-constrained pipeline view. Explicit stage metadata
      or stage-like names can group lanes, but unnamed models fall back to
      dependency columns.

    Neither layout claims to be a dynamic runtime/cycle schedule.
    """

    order_modes = [
        {
            "id": "call",
            "label": "Call order",
            "order_kind": "root_call_layout",
            "description": "Root/caller-left layout: entry callers start on the left and caller-local callee sequence expands to the right.",
        },
        {
            "id": "dependency",
            "label": "Dependency order",
            "order_kind": "static_dependency_layout",
            "description": "Static dependency layout: data/dependency producers appear before their consumers when facts allow it.",
        },
        {
            "id": "pipeline",
            "label": "Pipeline stage order",
            "order_kind": "pipeline_stage_layout",
            "description": "Pipeline stage layout: dependency-constrained blocks are placed left-to-right; explicit or inferred stage groups can share a column with parallel lanes.",
        },
    ]
    empty_model = {
        "schema": "sequence-execution-diagram-v2",
        "default_order_mode": "call",
        "order_kind": "root_call_layout",
        "order_modes": order_modes,
        "layouts": {
            "call": {
                "order_kind": "root_call_layout",
                "steps": [],
                "columns": 0,
                "lanes": 0,
            },
            "dependency": {
                "order_kind": "static_dependency_layout",
                "steps": [],
                "columns": 0,
                "lanes": 0,
            },
            "pipeline": {
                "order_kind": "pipeline_stage_layout",
                "steps": [],
                "columns": 0,
                "lanes": 0,
                "column_labels": [],
            },
        },
        "steps": [],
        "edges": [],
        "columns": 0,
        "lanes": 0,
    }
    if not isinstance(data, dict):
        return empty_model

    flow = _as_sequence_dict(data.get("flow"))
    timeline_raw = flow.get("sequence_timeline")
    if not isinstance(timeline_raw, list) or not timeline_raw:
        return empty_model

    blocks = data.get("blocks") if isinstance(data.get("blocks"), list) else []
    block_by_id = {
        block.get("block_id"): block
        for block in blocks
        if isinstance(block, dict) and block.get("block_id") is not None
    }
    signals = data.get("signals") if isinstance(data.get("signals"), list) else []
    signal_by_id = {
        signal.get("signal_id"): signal
        for signal in signals
        if isinstance(signal, dict) and signal.get("signal_id") is not None
    }

    def step_sort_key(item: object) -> tuple[int, str]:
        if not isinstance(item, dict):
            return (10**9, "")
        order = item.get("order_index")
        try:
            order_int = int(order)
        except (TypeError, ValueError):
            order_int = 10**9
        return (order_int, str(item.get("block_id") or item.get("step_id") or ""))

    steps_raw = [step for step in timeline_raw if isinstance(step, dict)]
    steps_raw.sort(key=step_sort_key)
    order_index_by_block = {
        step.get("block_id"): index
        for index, step in enumerate(steps_raw)
        if step.get("block_id") is not None
    }
    step_by_block_id = {
        step.get("block_id"): step
        for step in steps_raw
        if step.get("block_id") is not None
    }
    step_block_ids = [str(step.get("block_id")) for step in steps_raw if step.get("block_id") is not None]
    step_block_set = set(step_block_ids)

    edge_accumulator: dict[tuple[str, str, str, str], dict] = {}
    raw_scheduling_edges: list[tuple[str, str]] = []

    dependencies = _as_sequence_dict(flow.get("dependencies"))
    inter_block = dependencies.get("inter_block")
    dependency_items = inter_block if isinstance(inter_block, list) and inter_block else []

    if not dependency_items:
        dependency_items = [
            {
                "signal_id": signal.get("signal_id"),
                "source_id": signal.get("source_id"),
                "destination_id": signal.get("destination_id"),
                "kind": signal.get("kind"),
                "role": signal.get("role"),
            }
            for signal in signals
            if isinstance(signal, dict)
            and signal.get("source_type") == "block"
            and signal.get("destination_type") == "block"
        ]

    for item in dependency_items:
        if not isinstance(item, dict):
            continue
        signal = signal_by_id.get(item.get("signal_id"), {})
        if not isinstance(signal, dict):
            signal = {}
        source_id = item.get("source_id") or signal.get("source_id")
        destination_id = item.get("destination_id") or signal.get("destination_id")
        if source_id not in step_by_block_id or destination_id not in step_by_block_id:
            continue
        if source_id == destination_id:
            continue

        role = str(item.get("role") or signal.get("role") or "unknown")
        raw_kind = str(item.get("kind") or signal.get("kind") or "data")
        visual_kind = "control" if role == "control" or raw_kind == "control" else "data"
        signal_id = item.get("signal_id") or signal.get("signal_id")
        label = _sequence_label_for_signal(signal, signal_id)
        source_order = order_index_by_block.get(source_id, 10**9)
        destination_order = order_index_by_block.get(destination_id, 10**9)
        is_feedback = destination_order <= source_order

        key = (visual_kind, str(source_id), str(destination_id), role)
        if key not in edge_accumulator:
            edge_accumulator[key] = {
                "id": f"seq_{visual_kind}_{len(edge_accumulator)}",
                "kind": visual_kind,
                "role": role,
                "source_block_id": str(source_id),
                "destination_block_id": str(destination_id),
                "signal_ids": [],
                "labels": [],
                "raw_kinds": [],
                "is_feedback": False,
                "is_critical": False,
            }
        edge = edge_accumulator[key]
        if signal_id is not None and str(signal_id) not in edge["signal_ids"]:
            edge["signal_ids"].append(str(signal_id))
        if label not in edge["labels"]:
            edge["labels"].append(label)
        if raw_kind not in edge["raw_kinds"]:
            edge["raw_kinds"].append(raw_kind)
        edge["is_feedback"] = bool(edge["is_feedback"] or is_feedback)

        if visual_kind == "data" and not is_feedback:
            raw_scheduling_edges.append((str(source_id), str(destination_id)))

    def apply_layout(column_by_block: dict[str, int], critical_blocks: set[str]) -> dict:
        lane_by_column: dict[int, int] = {}
        rendered_steps: list[dict] = []
        for step in steps_raw:
            block_id = str(step.get("block_id"))
            column = column_by_block.get(block_id, 0)
            lane = lane_by_column.get(column, 0)
            lane_by_column[column] = lane + 1
            block = block_by_id.get(step.get("block_id"), {})
            rendered_steps.append(
                {
                    "step_id": str(step.get("step_id") or f"T_{len(rendered_steps):04d}_{block_id}"),
                    "block_id": block_id,
                    "function": str(step.get("function") or block.get("block_name") or block_id),
                    "order_index": int(step_sort_key(step)[0] if step_sort_key(step)[0] != 10**9 else len(rendered_steps)),
                    "column": column,
                    "lane": lane,
                    "is_critical": block_id in critical_blocks,
                    "estimated_cycles": block.get("estimated_cycles"),
                    "call_ids_as_caller": _as_string_list(step.get("call_ids_as_caller")),
                    "call_ids_as_callee": _as_string_list(step.get("call_ids_as_callee")),
                    "incoming_signal_ids": _as_string_list(step.get("incoming_signal_ids")),
                    "outgoing_signal_ids": _as_string_list(step.get("outgoing_signal_ids")),
                    "read_write_summary": step.get("read_write_summary") if isinstance(step.get("read_write_summary"), dict) else {},
                }
            )
        max_column = max((step["column"] for step in rendered_steps), default=-1)
        max_lane = max((step["lane"] for step in rendered_steps), default=-1)
        return {
            "steps": rendered_steps,
            "columns": max_column + 1,
            "lanes": max_lane + 1,
        }

    pred_by_block: dict[str, list[str]] = {str(step.get("block_id")): [] for step in steps_raw}
    for source_id, destination_id in raw_scheduling_edges:
        if source_id not in pred_by_block.get(destination_id, []):
            pred_by_block.setdefault(destination_id, []).append(source_id)

    dependency_column_by_block: dict[str, int] = {}
    distance_by_block: dict[str, int] = {}
    parent_by_block: dict[str, str | None] = {}
    for step in steps_raw:
        block_id = str(step.get("block_id"))
        predecessors = pred_by_block.get(block_id, [])
        if predecessors:
            best_parent = max(predecessors, key=lambda item: distance_by_block.get(item, 0))
            dependency_column_by_block[block_id] = max(dependency_column_by_block.get(pred, 0) + 1 for pred in predecessors)
            distance_by_block[block_id] = distance_by_block.get(best_parent, 0) + 1
            parent_by_block[block_id] = best_parent
        else:
            dependency_column_by_block[block_id] = 0
            distance_by_block[block_id] = 0
            parent_by_block[block_id] = None

    dependency_critical_blocks: set[str] = set()
    if distance_by_block:
        end_block = max(distance_by_block, key=lambda item: distance_by_block.get(item, 0))
        if distance_by_block.get(end_block, 0) > 0:
            cursor: str | None = end_block
            while cursor:
                dependency_critical_blocks.add(cursor)
                cursor = parent_by_block.get(cursor)

    call_graph = _as_sequence_dict(flow.get("call_graph"))
    call_nodes = _as_sequence_dict(call_graph.get("nodes"))
    call_instances_raw = flow.get("call_instances")
    call_instances = call_instances_raw if isinstance(call_instances_raw, list) else []
    call_children: dict[str, list[str]] = {block_id: [] for block_id in step_block_ids}
    call_child_order: dict[tuple[str, str], tuple[int, int, str]] = {}
    incoming_call_blocks: set[str] = set()

    def append_call_child(caller_id: str, callee_id: str, order_key: tuple[int, int, str]) -> None:
        children = call_children.setdefault(caller_id, [])
        if callee_id not in children:
            children.append(callee_id)
        edge_key = (caller_id, callee_id)
        previous_order = call_child_order.get(edge_key)
        if previous_order is None or order_key < previous_order:
            call_child_order[edge_key] = order_key
        incoming_call_blocks.add(callee_id)

    for index, item in enumerate(call_instances):
        if not isinstance(item, dict):
            continue
        caller = item.get("caller_block_id")
        callee = item.get("callee_block_id")
        if caller not in step_by_block_id or callee not in step_by_block_id or caller == callee:
            continue
        caller_id = str(caller)
        callee_id = str(callee)
        ordinal_raw = item.get("ordinal_in_caller")
        if ordinal_raw is None:
            ordinal = index
        else:
            try:
                ordinal = int(ordinal_raw)
            except (TypeError, ValueError):
                ordinal = index
        append_call_child(caller_id, callee_id, (ordinal, index, callee_id))

    for fallback_index, node in enumerate(call_nodes.values(), start=len(call_child_order)):
        if not isinstance(node, dict):
            continue
        caller_block = node.get("block_id")
        callees = node.get("callees")
        if caller_block not in step_by_block_id or not isinstance(callees, list):
            continue
        caller_id = str(caller_block)
        for callee_name in callees:
            callee_node = call_nodes.get(callee_name)
            if not isinstance(callee_node, dict):
                continue
            callee_block = callee_node.get("block_id")
            if callee_block not in step_by_block_id or callee_block == caller_block:
                continue
            callee_id = str(callee_block)
            if (caller_id, callee_id) in call_child_order:
                continue
            append_call_child(
                caller_id,
                callee_id,
                (order_index_by_block.get(callee_block, 10**9), fallback_index, callee_id),
            )

    def order_block_ids(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda block_id: order_index_by_block.get(block_id, 10**9))

    def order_call_children(caller_id: str) -> list[str]:
        return sorted(
            call_children.get(caller_id, []),
            key=lambda child_id: call_child_order.get(
                (caller_id, child_id),
                (order_index_by_block.get(child_id, 10**9), 10**9, child_id),
            ),
        )

    roots: list[str] = []
    entry_functions = call_graph.get("entry_functions")
    if isinstance(entry_functions, list):
        for function_name in entry_functions:
            node = call_nodes.get(function_name)
            if isinstance(node, dict):
                block_id = node.get("block_id")
                if block_id in step_by_block_id and str(block_id) not in roots:
                    roots.append(str(block_id))
    if not roots and call_children:
        roots = [block_id for block_id in step_block_ids if block_id not in incoming_call_blocks]
    if not roots and step_block_ids:
        roots = [step_block_ids[0]]
    roots = order_block_ids(roots)

    call_column_by_block: dict[str, int] = {}

    def assign_call_columns(block_id: str, column: int, active: set[str]) -> None:
        if block_id not in step_block_set:
            return
        existing_column = call_column_by_block.get(block_id)
        if existing_column is not None and existing_column <= column:
            return
        call_column_by_block[block_id] = column
        if block_id in active:
            return

        active.add(block_id)
        for sibling_index, child in enumerate(order_call_children(block_id)):
            if child in active:
                continue
            assign_call_columns(child, column + sibling_index + 1, active)
        active.remove(block_id)

    for root in roots:
        assign_call_columns(root, 0, set())

    fallback_column = max(call_column_by_block.values(), default=-1) + 1
    for block_id in step_block_ids:
        if block_id not in call_column_by_block:
            call_column_by_block[block_id] = fallback_column

    call_critical_blocks: set[str] = set()
    critical_path = call_graph.get("critical_path")
    if isinstance(critical_path, list):
        for function_name in critical_path:
            node = call_nodes.get(function_name)
            if isinstance(node, dict):
                block_id = node.get("block_id")
                if block_id in step_by_block_id:
                    call_critical_blocks.add(str(block_id))
    if not call_critical_blocks:
        call_critical_blocks = dependency_critical_blocks.copy()

    edges = []
    for edge in edge_accumulator.values():
        edge["label"] = _merge_sequence_labels(edge.pop("labels"))
        edges.append(edge)

    execution_order = flow.get("execution_order")
    if isinstance(execution_order, list):
        for index in range(len(execution_order) - 1):
            source_id = execution_order[index]
            destination_id = execution_order[index + 1]
            if source_id in step_by_block_id and destination_id in step_by_block_id:
                edges.append(
                    {
                        "id": f"seq_exec_{index}",
                        "kind": "exec",
                        "role": "execution",
                        "source_block_id": str(source_id),
                        "destination_block_id": str(destination_id),
                        "signal_ids": [],
                        "label": "exec",
                        "is_feedback": False,
                        "is_critical": False,
                    }
                )

    for edge in edges:
        if edge["kind"] != "data":
            continue
        if edge["source_block_id"] in dependency_critical_blocks and edge["destination_block_id"] in dependency_critical_blocks:
            edge["is_critical"] = True

    call_layout = apply_layout(call_column_by_block, call_critical_blocks)
    call_layout["order_kind"] = "root_call_layout"
    dependency_layout = apply_layout(dependency_column_by_block, dependency_critical_blocks)
    dependency_layout["order_kind"] = "static_dependency_layout"

    def int_or_none(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    pipeline_stage_by_block: dict[str, dict[str, object]] = {}
    pipeline_stage_by_function: dict[str, dict[str, object]] = {}
    pipeline_stages = _as_sequence_dict(flow.get("pipeline_stages"))
    pipeline_stage_items = pipeline_stages.get("items")
    if isinstance(pipeline_stage_items, list):
        for item in pipeline_stage_items:
            if not isinstance(item, dict):
                continue
            stage_number = int_or_none(item.get("stage", item.get("stage_number")))
            if stage_number is None:
                continue
            label = str(item.get("stage_label") or item.get("label") or "")
            role = str(item.get("lane_role") or item.get("role") or "lane").lower()
            stage_info: dict[str, object] = {
                "stage": stage_number,
                "label": label,
                "role": role,
                "source": "explicit",
            }
            block_id = item.get("block_id")
            function_name = item.get("function")
            if block_id not in (None, ""):
                pipeline_stage_by_block[str(block_id)] = stage_info
            if function_name not in (None, ""):
                pipeline_stage_by_function[str(function_name)] = stage_info

    def pipeline_stage_number_from_name(function_name: object) -> int | None:
        match = re.search(r"(?:^|_)stage(\d+)(?:_|$)", str(function_name or "").lower())
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def pipeline_stage_info(step: dict) -> dict[str, object] | None:
        block_id = step.get("block_id")
        if block_id is not None and str(block_id) in pipeline_stage_by_block:
            return pipeline_stage_by_block[str(block_id)]
        function_name = str(step.get("function") or "")
        if function_name in pipeline_stage_by_function:
            return pipeline_stage_by_function[function_name]
        stage_number = pipeline_stage_number_from_name(function_name)
        if stage_number is None:
            return None
        lane_role = "join" if "join" in function_name.lower() or "final" in function_name.lower() else "lane"
        return {
            "stage": stage_number,
            "label": "",
            "role": lane_role,
            "source": "name_heuristic",
        }

    def pipeline_lane_sort_key(step: dict) -> tuple[int, int, str]:
        function_name = str(step.get("function") or "").lower()
        info = pipeline_stage_info(step) or {}
        role = str(info.get("role") or "").lower()
        is_join = 1 if role in {"join", "final", "finalize"} or "join" in function_name or "final" in function_name else 0
        return (is_join, step_sort_key(step)[0], function_name)

    stage_label_by_number: dict[int, str] = {}
    stage_numbers = sorted(
        {
            stage_number
            for step in steps_raw
            for info in [pipeline_stage_info(step)]
            for stage_number in [int_or_none(info.get("stage")) if info else None]
            if stage_number is not None
        }
    )
    for step in steps_raw:
        info = pipeline_stage_info(step)
        if not info:
            continue
        stage_number = int_or_none(info.get("stage"))
        label = str(info.get("label") or "")
        if stage_number is not None and label and stage_number not in stage_label_by_number:
            stage_label_by_number[stage_number] = label
    pipeline_column_by_block: dict[str, int] = {}
    pipeline_lane_by_block: dict[str, int] = {}

    if not stage_numbers:
        pipeline_column_by_block = dict(dependency_column_by_block)
        pipeline_layout = apply_layout(pipeline_column_by_block, dependency_critical_blocks)
        pipeline_layout["lanes"] = max((step["lane"] for step in pipeline_layout["steps"]), default=-1) + 1
        pipeline_layout["order_kind"] = "pipeline_stage_layout"
        pipeline_layout["column_labels"] = [
            f"P{index}" for index in range(pipeline_layout["columns"])
        ]
    else:
        stage_block_ids = {
            str(step.get("block_id"))
            for step in steps_raw
            if step.get("block_id") is not None and pipeline_stage_info(step) is not None
        }
        reaches_stage_cache: dict[str, bool] = {}

        def reaches_stage(block_id: str, active: set[str]) -> bool:
            if block_id in reaches_stage_cache:
                return reaches_stage_cache[block_id]
            if block_id in stage_block_ids:
                reaches_stage_cache[block_id] = True
                return True
            if block_id in active:
                return False
            active.add(block_id)
            result = any(reaches_stage(child, active) for child in call_children.get(block_id, []))
            active.remove(block_id)
            reaches_stage_cache[block_id] = result
            return result

        stage_ancestor_blocks = {
            block_id
            for block_id in step_block_ids
            if block_id not in stage_block_ids and reaches_stage(block_id, set())
        }
        stage_ancestor_incoming = {
            child
            for parent in stage_ancestor_blocks
            for child in order_call_children(parent)
            if child in stage_ancestor_blocks
        }
        stage_ancestor_roots = [
            block_id for block_id in roots if block_id in stage_ancestor_blocks
        ]
        stage_ancestor_roots.extend(
            block_id
            for block_id in order_block_ids(list(stage_ancestor_blocks))
            if block_id not in stage_ancestor_incoming and block_id not in stage_ancestor_roots
        )
        if not stage_ancestor_roots:
            stage_ancestor_roots = order_block_ids(list(stage_ancestor_blocks))

        def assign_stage_ancestor_columns(block_id: str, column: int, active: set[str]) -> None:
            existing_column = pipeline_column_by_block.get(block_id)
            if existing_column is not None and existing_column <= column:
                return
            pipeline_column_by_block[block_id] = column
            if block_id in active:
                return
            active.add(block_id)
            for child in order_call_children(block_id):
                if child in stage_ancestor_blocks:
                    assign_stage_ancestor_columns(child, column + 1, active)
            active.remove(block_id)

        for root in stage_ancestor_roots:
            assign_stage_ancestor_columns(root, 0, set())

        for block_id in stage_ancestor_blocks:
            pipeline_column_by_block.setdefault(block_id, 0)

        stage_start_column = max(
            (pipeline_column_by_block[block_id] for block_id in stage_ancestor_blocks),
            default=0,
        ) + 1
        pipeline_column_by_stage = {
            stage_number: stage_start_column + index
            for index, stage_number in enumerate(stage_numbers)
        }

        for step in steps_raw:
            block_id = str(step.get("block_id"))
            if pipeline_stage_info(step) is None and block_id not in pipeline_column_by_block:
                pipeline_column_by_block[block_id] = 0

        for stage_number in stage_numbers:
            stage_steps = [
                step
                for step in steps_raw
                if (pipeline_stage_info(step) or {}).get("stage") == stage_number
            ]
            for lane, step in enumerate(sorted(stage_steps, key=pipeline_lane_sort_key)):
                block_id = str(step.get("block_id"))
                pipeline_column_by_block[block_id] = pipeline_column_by_stage[stage_number]
                pipeline_lane_by_block[block_id] = lane

        pipeline_layout = apply_layout(pipeline_column_by_block, dependency_critical_blocks)
        for step in pipeline_layout["steps"]:
            lane = pipeline_lane_by_block.get(step["block_id"])
            if lane is not None:
                step["lane"] = lane
            source_step = step_by_block_id.get(step["block_id"])
            if source_step:
                info = pipeline_stage_info(source_step)
                if info:
                    step["pipeline_stage"] = info.get("stage")
                    step["pipeline_stage_source"] = info.get("source")
                    step["pipeline_lane_role"] = info.get("role")
                    if info.get("label"):
                        step["pipeline_stage_label"] = info.get("label")
        pipeline_layout["lanes"] = max((step["lane"] for step in pipeline_layout["steps"]), default=-1) + 1
        pipeline_layout["order_kind"] = "pipeline_stage_layout"
        prefix_labels = ["Entry / utility"] + [
            "Pipeline setup" if index == 1 else f"Pipeline setup {index}"
            for index in range(1, stage_start_column)
        ]
        pipeline_layout["column_labels"] = prefix_labels + [
            f"Stage {stage_number}: {stage_label_by_number[stage_number]}"
            if stage_label_by_number.get(stage_number)
            else f"Stage {stage_number}"
            for stage_number in stage_numbers
        ]

    return {
        "schema": "sequence-execution-diagram-v2",
        "default_order_mode": "call",
        "order_kind": "root_call_layout",
        "order_modes": order_modes,
        "layouts": {
            "call": call_layout,
            "dependency": dependency_layout,
            "pipeline": pipeline_layout,
        },
        "steps": call_layout["steps"],
        "edges": edges,
        "columns": call_layout["columns"],
        "lanes": call_layout["lanes"],
    }


def load_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[json_to_html] Failed to read input JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[json_to_html] Failed to parse JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if not isinstance(data, dict):
        print("[json_to_html] JSON root must be an object.", file=sys.stderr)
        raise SystemExit(1)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        print(f"[json_to_html] Missing required fields: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)

    return data


def build_html(data: dict) -> str:
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    sequence_execution_model = build_sequence_execution_model(data)
    sequence_execution_json = json.dumps(sequence_execution_model, ensure_ascii=False).replace("</", "<\\/")
    default_function_io = {}
    function_io_path = Path(__file__).with_name("function_io.json")
    if function_io_path.exists():
        try:
            default_function_io = json.loads(function_io_path.read_text(encoding="utf-8"))
            if not isinstance(default_function_io, dict):
                default_function_io = {}
        except (OSError, json.JSONDecodeError):
            default_function_io = {}
    function_io_json = json.dumps(default_function_io, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>CVAS Diagram Viewer</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f6f8;
      --panel-bg: #ffffff;
      --border: #2f343b;
      --muted: #6b7280;
      --accent: #2563eb;
      --edge: #4b5563;
      --edge-secondary: #9ca3af;
      --edge-call: #0ea5e9;
      --highlight: #f59e0b;
    }}
    body {{
      margin: 0;
      font-family: "Inter", "Noto Sans", sans-serif;
      background: var(--bg);
      color: #111827;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    header {{
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 12px 16px;
      background: var(--panel-bg);
      border-bottom: 1px solid #e5e7eb;
      flex-wrap: wrap;
    }}
    header input[type=\"text\"] {{
      padding: 6px 10px;
      min-width: 220px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
    }}
    header label {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 14px;
    }}
    header button {{
      padding: 6px 12px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      background: #fff;
      cursor: pointer;
    }}
    header .tab {{
      border: 1px solid #d1d5db;
      border-radius: 999px;
      padding: 6px 14px;
      background: #fff;
      font-size: 13px;
    }}
    header .tab.active {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }}
    #ioStatus {{
      font-size: 12px;
      color: #374151;
      background: #eef2ff;
      border: 1px solid #c7d2fe;
      border-radius: 999px;
      padding: 4px 10px;
      white-space: nowrap;
    }}
    #analysisSummary {{
      font-size: 12px;
      color: #1f2937;
      background: #ecfdf5;
      border: 1px solid #a7f3d0;
      border-radius: 999px;
      padding: 4px 10px;
      white-space: nowrap;
    }}
    main {{
      flex: 1;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(220px, 300px);
      min-height: 0;
    }}
    main.details-expanded {{
      grid-template-columns: minmax(0, 1fr) minmax(220px, 300px);
    }}
    main.details-narrow {{
      grid-template-columns: minmax(0, 1fr) 64px;
    }}
    main.details-narrow #detail-panel {{
      padding: 10px 8px;
      overflow: hidden;
    }}
    main.details-narrow #analysis-card,
    main.details-narrow #detail-json,
    main.details-narrow #anomaly-list {{
      display: none;
    }}
    main.details-narrow .detail-panel-header {{
      flex-direction: column;
      align-items: center;
      gap: 8px;
    }}
    main.details-narrow #detail-panel h2,
    main.details-narrow .detail-panel-toggle {{
      writing-mode: vertical-rl;
      transform: rotate(180deg);
      white-space: nowrap;
      margin: 0 auto;
    }}
    #diagram-panel {{
      position: relative;
      background: #fff;
      border-right: 1px solid #e5e7eb;
      overflow: hidden;
    }}
    #sequence-panel {{
      position: relative;
      background: #fff;
      border-right: 1px solid #e5e7eb;
      overflow: auto;
      padding: 16px 20px;
    }}
    #sequence-panel[hidden] {{
      display: none;
    }}
    .seq-group {{
      flex: 0 0 auto;
      margin-bottom: 10px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 8px;
      background: #f9fafb;
    }}
    .seq-title {{
      font-size: 12px;
      font-weight: 600;
      margin: 0 0 6px 0;
      color: #111827;
    }}
    .seq-board {{
      position: relative;
      display: flex;
      flex-wrap: nowrap;
      gap: 10px;
      align-items: flex-start;
      min-height: 120px;
      width: max-content;
      padding: 8px 8px 16px 8px;
    }}
    .seq-board-overlay {{
      position: absolute;
      inset: 0;
      pointer-events: none;
      overflow: visible;
      z-index: 0;
    }}
    .seq-group {{
      position: relative;
      z-index: 1;
    }}
    .seq-group-edge {{
      stroke: #94a3b8;
      stroke-width: 1.4;
      fill: none;
      marker-end: url(#group-arrowhead);
      opacity: 0.9;
    }}
    .seq-canvas {{
      position: relative;
      min-height: 64px;
      border-radius: 6px;
      background: #ffffff;
      border: 1px dashed #e5e7eb;
    }}
    .seq-node {{
      position: absolute;
      padding: 3px 7px;
      background: #fff;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      font-size: 11px;
      display: inline-flex;
      gap: 4px;
      align-items: center;
      cursor: grab;
      user-select: none;
      line-height: 1.15;
    }}
    .timeline-shell {{
      display: grid;
      gap: 12px;
      max-width: 1120px;
      padding-bottom: 24px;
    }}
    .timeline-banner {{
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1e3a8a;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 13px;
      line-height: 1.45;
    }}
    .timeline-card {{
      border: 1px solid #d1d5db;
      background: #ffffff;
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
    }}
    .timeline-card-header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 8px;
    }}
    .timeline-title {{
      font-weight: 700;
      color: #111827;
      font-size: 14px;
    }}
    .timeline-subtitle {{
      color: #6b7280;
      font-size: 12px;
      margin-top: 2px;
    }}
    .timeline-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      justify-content: flex-end;
    }}
    .timeline-pill {{
      border-radius: 999px;
      background: #f3f4f6;
      color: #374151;
      padding: 3px 8px;
      font-size: 11px;
      border: 1px solid #e5e7eb;
    }}
    .timeline-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }}
    .timeline-section {{
      border: 1px dashed #e5e7eb;
      border-radius: 8px;
      padding: 8px;
      background: #f9fafb;
      min-height: 54px;
    }}
    .timeline-section-title {{
      font-weight: 600;
      color: #374151;
      margin-bottom: 6px;
      font-size: 12px;
    }}
    .timeline-list {{
      display: grid;
      gap: 5px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .timeline-item {{
      border-radius: 6px;
      border: 1px solid #e5e7eb;
      background: #fff;
      padding: 5px 6px;
      font-size: 12px;
      line-height: 1.35;
    }}
    .timeline-empty {{
      color: #9ca3af;
      font-size: 12px;
    }}
    .timeline-mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      color: #334155;
    }}
    .sequence-exec-shell {{
      display: grid;
      gap: 12px;
      min-width: max-content;
      padding-bottom: 28px;
    }}
    .sequence-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      align-items: center;
      border: 1px solid #dbeafe;
      background: #eff6ff;
      color: #1e3a8a;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 12px;
    }}
    .sequence-toolbar label {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-weight: 600;
    }}
    .sequence-toolbar button,
    .sequence-toolbar select {{
      border: 1px solid #93c5fd;
      background: #ffffff;
      color: #1e40af;
      border-radius: 7px;
      padding: 4px 8px;
      cursor: pointer;
      font-weight: 600;
      font-size: 12px;
    }}
    .sequence-toolbar .mode-group {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 6px;
      border: 1px solid #bfdbfe;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.68);
    }}
    .sequence-exec-board {{
      position: relative;
      min-width: 720px;
      min-height: 320px;
      border: 1px solid #cbd5e1;
      border-radius: 14px;
      overflow: visible;
      background-color: #f8fafc;
      background-image:
        linear-gradient(to right, rgba(148, 163, 184, 0.22) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(148, 163, 184, 0.18) 1px, transparent 1px);
      background-size: 260px 100%, 100% 150px;
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.8);
    }}
    .sequence-timestep {{
      position: absolute;
      top: 8px;
      color: #64748b;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      pointer-events: none;
    }}
    .sequence-lane {{
      position: absolute;
      left: 8px;
      color: #94a3b8;
      font-size: 11px;
      font-weight: 700;
      pointer-events: none;
    }}
    .sequence-edge-overlay {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      overflow: visible;
      z-index: 1;
    }}
    .sequence-edge {{
      fill: none;
      stroke-width: 2;
      opacity: 0.88;
    }}
    .sequence-edge-data {{
      stroke: #2563eb;
      marker-end: url(#sequence-arrow-data);
    }}
    .sequence-edge-write {{
      stroke: #ea580c;
      marker-end: url(#sequence-arrow-write);
    }}
    .sequence-edge-exec {{
      stroke: #64748b;
      stroke-dasharray: 5 5;
      marker-end: url(#sequence-arrow-exec);
      opacity: 0.65;
    }}
    .sequence-edge-control {{
      stroke: #0ea5e9;
      stroke-dasharray: 8 4;
      marker-end: url(#sequence-arrow-control);
      opacity: 0.7;
    }}
    .sequence-edge-critical {{
      stroke-width: 3;
      filter: drop-shadow(0 0 3px rgba(245, 158, 11, 0.45));
    }}
    .sequence-edge-feedback {{
      stroke-dasharray: 2 5;
      opacity: 0.58;
    }}
    .sequence-edge-label {{
      fill: #334155;
      font-size: 11px;
      paint-order: stroke;
      stroke: #f8fafc;
      stroke-width: 3px;
      stroke-linejoin: round;
      dominant-baseline: middle;
    }}
    .sequence-edge-label.source {{
      fill: #1d4ed8;
    }}
    .sequence-edge-label.target {{
      fill: #0f766e;
    }}
    .sequence-exec-card {{
      position: absolute;
      z-index: 2;
      width: 214px;
      min-height: 108px;
      border: 1px solid #cbd5e1;
      border-radius: 12px;
      background: #ffffff;
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.12);
      padding: 10px;
      cursor: grab;
      user-select: none;
    }}
    .sequence-exec-card.critical {{
      border-color: #f59e0b;
      box-shadow: 0 8px 22px rgba(245, 158, 11, 0.2);
    }}
    .sequence-card-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
      margin-bottom: 8px;
    }}
    .sequence-card-title {{
      font-size: 14px;
      font-weight: 800;
      color: #111827;
      line-height: 1.2;
    }}
    .sequence-card-meta {{
      color: #64748b;
      font-size: 11px;
      line-height: 1.35;
      margin-top: 2px;
    }}
    .sequence-card-badge {{
      flex: 0 0 auto;
      border-radius: 999px;
      background: #eef2ff;
      border: 1px solid #c7d2fe;
      color: #3730a3;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 700;
    }}
    .sequence-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 5px;
    }}
    .sequence-chip {{
      border-radius: 999px;
      border: 1px solid #e5e7eb;
      background: #f8fafc;
      color: #334155;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 700;
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .sequence-chip.read {{
      background: #eff6ff;
      border-color: #bfdbfe;
      color: #1d4ed8;
    }}
    .sequence-chip.write {{
      background: #fff7ed;
      border-color: #fed7aa;
      color: #c2410c;
    }}
    .sequence-chip.read-write {{
      background: #faf5ff;
      border-color: #e9d5ff;
      color: #7e22ce;
    }}
    .sequence-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      color: #475569;
      font-size: 12px;
    }}
    .sequence-legend span {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .sequence-legend-line {{
      width: 26px;
      height: 0;
      border-top: 2px solid #2563eb;
    }}
    .sequence-legend-line.exec {{
      border-top-color: #64748b;
      border-top-style: dashed;
    }}
    .seq-count {{
      background: #111827;
      color: #fff;
      border-radius: 999px;
      padding: 2px 6px;
      font-size: 11px;
    }}
    .seq-svg {{
      position: absolute;
      inset: 0;
      pointer-events: none;
    }}
    .seq-edge {{
      stroke: #9ca3af;
      stroke-width: 1.2;
      fill: none;
      marker-end: url(#arrowhead);
    }}
    #sequence-content {{
      transform-origin: top left;
    }}
    #detail-panel {{
      background: var(--panel-bg);
      padding: 12px 16px;
      overflow: auto;
    }}
    .detail-panel-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .detail-panel-toggle {{
      border: 1px solid #cbd5e1;
      border-radius: 7px;
      background: #f8fafc;
      color: #334155;
      padding: 5px 8px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 700;
    }}
    #detail-panel h2 {{
      margin: 0;
      font-size: 16px;
    }}
    #analysis-card {{
      margin-bottom: 12px;
      padding: 10px;
      border: 1px solid #dbeafe;
      border-radius: 8px;
      background: #eff6ff;
    }}
    #analysis-card h3 {{
      margin: 0 0 8px 0;
      font-size: 14px;
      color: #1e3a8a;
    }}
    #analysis-summary-table {{
      display: grid;
      grid-template-columns: minmax(100px, max-content) minmax(0, 1fr);
      gap: 4px 10px;
      font-size: 12px;
      line-height: 1.35;
    }}
    #analysis-summary-table dt {{
      margin: 0;
      color: #1f2937;
      font-weight: 600;
    }}
    #analysis-summary-table dd {{
      margin: 0;
      color: #374151;
      word-break: break-word;
    }}
    #analysis-summary-table code {{
      font-family: "SFMono-Regular", "Consolas", monospace;
      font-size: 11px;
    }}
    #detail-json {{
      background: #0f172a;
      color: #e2e8f0;
      padding: 12px;
      border-radius: 8px;
      font-size: 12px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    #anomaly-list {{
      margin-top: 16px;
      padding: 10px;
      border: 1px dashed #f59e0b;
      border-radius: 8px;
      background: #fffbeb;
    }}
    #anomaly-list ul {{
      margin: 8px 0 0 16px;
      padding: 0;
      font-size: 13px;
      color: #92400e;
    }}
    svg {{
      width: 100%;
      height: 100%;
    }}
    .node-box {{
      fill: #ffffff;
      stroke: var(--border);
      stroke-width: 2;
      rx: 10;
      ry: 10;
    }}
    .node-box.loop {{
      stroke-dasharray: 6 4;
    }}
    .node-title {{
      font-weight: 600;
      font-size: 13px;
      fill: #111827;
    }}
    .node-line {{
      font-size: 12px;
      fill: var(--muted);
    }}
    .edge-line {{
      fill: none;
      stroke: var(--edge);
      stroke-width: 1.6;
    }}
    .edge-line.exec {{
      stroke: var(--edge-secondary);
      stroke-dasharray: 4 4;
    }}
    .edge-line.call {{
      stroke: var(--edge-call);
      stroke-dasharray: 6 3;
    }}
    .edge-label {{
      font-size: 11px;
      fill: #374151;
      pointer-events: none;
    }}
    .highlight rect {{
      stroke: var(--highlight);
      stroke-width: 3;
    }}
  </style>
</head>
<body>
  <header>
    <strong>CVAS Diagram Viewer</strong>
    <input id=\"searchInput\" type=\"text\" placeholder=\"Search block_id or block_name\" />
    <label><input id=\"toggleData\" type=\"checkbox\" checked /> Show data-flow edges</label>
    <label><input id=\"toggleExec\" type=\"checkbox\" /> Show execution-order edges</label>
    <label><input id=\"toggleCall\" type=\"checkbox\" /> Show call-graph edges</label>
    <button id=\"resetBtn\">Reset View</button>
    <button id=\"tabDiagram\" class=\"tab active\">Diagram</button>
    <button id=\"tabSequence\" class=\"tab\">Sequence</button>
    <button id=\"seqZoomOut\" class=\"tab\">Seq -</button>
    <button id=\"seqZoomReset\" class=\"tab\">Seq 100%</button>
    <button id=\"seqZoomIn\" class=\"tab\">Seq +</button>
    <button id=\"seqLoad\" class=\"tab\">Load Map</button>
    <button id=\"seqExport\" class=\"tab\">Export Map</button>
    <button id=\"ioLoad\" class=\"tab\">Load IO</button>
    <span id=\"ioStatus\">IO: none</span>
    <span id=\"analysisSummary\">Analysis: loading</span>
    <input id=\"seqFile\" type=\"file\" accept=\"application/json\" hidden />
    <input id=\"ioFile\" type=\"file\" accept=\"application/json\" hidden />
  </header>
  <main>
    <section id=\"diagram-panel\">
      <svg id=\"diagramSvg\" xmlns=\"http://www.w3.org/2000/svg\"></svg>
    </section>
    <section id=\"sequence-panel\" hidden>
      <div id=\"sequence-content\"></div>
    </section>
    <aside id=\"detail-panel\">
      <div class=\"detail-panel-header\">
        <h2>Details</h2>
        <button id=\"detailToggle\" class=\"detail-panel-toggle\">Narrow Details</button>
      </div>
      <div id=\"analysis-card\">
        <h3>Analysis Summary</h3>
        <dl id=\"analysis-summary-table\"></dl>
      </div>
      <div id=\"detail-json\">Click a node or edge to inspect data.</div>
      <div id=\"anomaly-list\" hidden>
        <strong>Anomaly Report</strong>
        <ul id=\"anomaly-items\"></ul>
      </div>
    </aside>
  </main>

  <!-- Ensure ./assets/elk.bundled.js is available next to this HTML file for offline use. -->
  <script src=\"./assets/elk.bundled.js\"></script>
  <script>
  const DATA = {data_json};
  const SEQUENCE_EXECUTION_MODEL = {sequence_execution_json};
  const DEFAULT_FUNCTION_IO = {function_io_json};
  const DIAGRAM_PAN_SPEED = 1.6;

  const state = {{
    toggles: {{ showDataFlow: true, showExecution: false, showCallGraph: false }},
    selectedNodeId: null,
    selectedEdgeKey: null,
    viewTransform: {{ x: 0, y: 0, k: 1 }},
    initialTransform: {{ x: 0, y: 0, k: 1 }},
    anomalies: [],
    activeTab: "diagram",
    sequenceZoom: 1,
    sequenceOrderMode: "call",
    sequenceLabelMode: "compact",
    sequenceEdgeDensityMode: "all",
    sequenceStageFilter: "all",
    sequenceToggles: {{ showDataFlow: true, showExecution: true, showControl: false, showCritical: true }},
    sequenceBlockMapsByMode: {{ call: {{}}, dependency: {{}} }},
    sequenceBlockMap: {{}},
    sequenceMap: {{}},
    sequenceGroupMap: {{}},
    detailsMode: "expanded",
    functionIO: {{}},
    functionIOSource: "none"
  }};

  function addAnomaly(kind, message, details) {{
    state.anomalies.push({{ kind, message, details }});
  }}

  function updateIOSourceStatus(state) {{
    const el = document.getElementById("ioStatus");
    if (!el) return;
    el.textContent = `IO: ${{state.functionIOSource || "none"}}`;
  }}

  function setDetailsMode(state, mode) {{
    const allowed = ["expanded", "narrow"];
    state.detailsMode = allowed.includes(mode) ? mode : "expanded";
    applyDetailsPanelState(state);
  }}

  function applyDetailsPanelState(state) {{
    const main = document.querySelector("main");
    const detailToggle = document.getElementById("detailToggle");
    const mode = state.detailsMode === "narrow" ? "narrow" : "expanded";
    state.detailsMode = mode;
    if (main) {{
      main.classList.remove("details-expanded", "details-narrow");
      main.classList.add(`details-${{mode}}`);
    }}
    if (detailToggle) {{
      detailToggle.textContent = mode === "expanded" ? "Narrow Details" : "Expand Details";
      detailToggle.title = "Toggle Details panel: expanded <-> narrow";
    }}
    requestAnimationFrame(() => redrawActiveSequenceEdges(state));
  }}

  function toggleDetailsPanel(state) {{
    const mode = state.detailsMode === "narrow" ? "narrow" : "expanded";
    const next = mode === "expanded" ? "narrow" : "expanded";
    setDetailsMode(state, next);
  }}

  function safeValue(value, fallback = "unknown") {{
    if (value === null || value === undefined || value === "") return fallback;
    return String(value);
  }}

  function arrayCount(value) {{
    return Array.isArray(value) ? String(value.length) : "0";
  }}

  function summarizeGccDump(gccDump) {{
    if (!gccDump || typeof gccDump !== "object") return "not emitted (fast mode or legacy artifact)";
    const status = safeValue(gccDump.status);
    const backend = safeValue(gccDump.backend);
    const language = safeValue(gccDump.language);
    const standard = safeValue(gccDump.standard);
    return `${{status}} (${{backend}}, ${{language}}/${{standard}})`;
  }}

  function buildAnalysisRows(data) {{
    const flow = data.flow || {{}};
    const callGraph = flow.call_graph || {{}};
    const callGraphNodes = callGraph.nodes && typeof callGraph.nodes === "object"
      ? Object.keys(callGraph.nodes).length
      : 0;
    const rows = [
      ["mode", safeValue(data.analysis_mode)],
      ["backend", safeValue(data.analysis_backend)],
      ["version", safeValue(data.analysis_version)],
      ["project mode", typeof data.project_mode === "boolean" ? String(data.project_mode) : safeValue(data.project_mode)],
      ["blocks", arrayCount(data.blocks)],
      ["operations", arrayCount(data.operations)],
      ["signals", arrayCount(data.signals)],
      ["execution order", arrayCount(flow.execution_order)],
      ["call sequence", arrayCount(flow.call_sequence)],
      ["call graph nodes", String(callGraphNodes)],
      ["gcc dump", summarizeGccDump(data.gcc_dump)]
    ];

    if (data.gcc_dump && typeof data.gcc_dump === "object") {{
      const gccDump = data.gcc_dump;
      rows.push(["gcc returncode", safeValue(gccDump.returncode, "n/a")]);
      rows.push(["gcc diagnostics", arrayCount(gccDump.diagnostics)]);
      rows.push(["gcc dump files", arrayCount(gccDump.dump_files)]);
    }}
    return rows;
  }}

  function renderAnalysisSummary(state) {{
    const data = state.data || {{}};
    const badge = document.getElementById("analysisSummary");
    const table = document.getElementById("analysis-summary-table");
    const mode = safeValue(data.analysis_mode);
    const backend = safeValue(data.analysis_backend);
    const gccStatus = data.gcc_dump && typeof data.gcc_dump === "object"
      ? ` | gcc_dump: ${{safeValue(data.gcc_dump.status)}}`
      : "";
    if (badge) badge.textContent = `mode: ${{mode}} | backend: ${{backend}}${{gccStatus}}`;
    if (!table) return;
    table.innerHTML = "";
    buildAnalysisRows(data).forEach(([label, value]) => {{
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      if (label.endsWith("command")) {{
        const code = document.createElement("code");
        code.textContent = String(value);
        dd.appendChild(code);
      }} else {{
        dd.textContent = String(value);
      }}
      table.appendChild(dt);
      table.appendChild(dd);
    }});
  }}

  function applySequenceZoom(state) {{
    const content = document.getElementById("sequence-content");
    if (!content) return;
    const z = Math.max(0.5, Math.min(2.5, state.sequenceZoom || 1));
    state.sequenceZoom = z;
    content.style.transform = `scale(${{z}})`;
  }}

  function parseData(raw) {{
    const data = Object.assign({{}}, raw);
    data.blocks = Array.isArray(data.blocks) ? data.blocks : [];
    data.signals = Array.isArray(data.signals) ? data.signals : [];
    data.flow = data.flow || {{}};
    data.flow.execution_order = Array.isArray(data.flow.execution_order) ? data.flow.execution_order : [];
    data.flow.call_graph = data.flow.call_graph || {{}};
    return data;
  }}

  function computeNodeSize(node) {{
    const baseWidth = 180;
    const charWidth = 7;
    const lineHeight = 16;
    const lines = [node.title].concat(node.bodyLines);
    const maxLen = Math.max.apply(null, lines.map(line => line.length));
    const width = Math.max(baseWidth, Math.min(360, maxLen * charWidth + 40));
    const height = 20 + lines.length * lineHeight + 12;
    return {{ width, height }};
  }}

  function buildNodes(data) {{
    return data.blocks.map(block => {{
      const inputs = Array.isArray(block.inputs) ? block.inputs.join(", ") : "";
      const outputs = Array.isArray(block.outputs) ? block.outputs.join(", ") : "";
      const cycles = block.estimated_cycles != null ? String(block.estimated_cycles) : "n/a";
      const badges = [];
      if (block.cfg && block.cfg.has_branches) badges.push("⚡ branch");
      if (block.cfg && Array.isArray(block.cfg.loops) && block.cfg.loops.length) badges.push("🔁 loop");
      const blockId = block.block_id || "unknown";
      const blockName = block.block_name || "unnamed";
      const node = {{
        id: blockId,
        title: `${{blockName}} (${{blockId}})`,
        bodyLines: [
          `inputs: ${{inputs || "-"}}`,
          `outputs: ${{outputs || "-"}}`,
          `cycles: ${{cycles}}`
        ].concat(badges.length ? [`badges: ${{badges.join(", ")}}`] : []),
        data: block
      }};
      const size = computeNodeSize(node);
      return {{
        id: node.id,
        width: size.width,
        height: size.height,
        data: node
      }};
    }});
  }}

  function mergeParallelEdges(edges) {{
    const map = new Map();
    edges.forEach(edge => {{
      const key = `${{edge.source_id}}::${{edge.destination_id}}`;
      if (!map.has(key)) {{
        map.set(key, {{
          source_id: edge.source_id,
          destination_id: edge.destination_id,
          labels_merged: [],
          original_signals: []
        }});
      }}
      const item = map.get(key);
      item.labels_merged.push(edge.label);
      item.original_signals.push(edge.original);
    }});
    return Array.from(map.values()).map(item => {{
      const labelCount = item.labels_merged.length;
      const label_display = labelCount > 4 ? `${{labelCount}} labels` : item.labels_merged.join(", ");
      return Object.assign(item, {{ label_display }});
    }});
  }}

  function buildEdges(data, toggles) {{
    const blockIds = new Set(data.blocks.map(block => block.block_id));
    const dataEdgesRaw = [];

    if (toggles.showDataFlow) {{
      data.signals.forEach(signal => {{
        if (signal.source_type !== "block" || signal.destination_type !== "block") return;
        if (!blockIds.has(signal.source_id) || !blockIds.has(signal.destination_id)) {{
          addAnomaly("warn", "Signal references missing block", signal);
          return;
        }}
        dataEdgesRaw.push({{
          source_id: signal.source_id,
          destination_id: signal.destination_id,
          label: signal.signal_name || "signal",
          original: signal
        }});
      }});
    }}

    const mergedDataEdges = mergeParallelEdges(dataEdgesRaw).map((edge, index) => ({{
      id: `data_${{index}}_${{edge.source_id}}_${{edge.destination_id}}`,
      sources: [edge.source_id],
      targets: [edge.destination_id],
      labels: [{{ text: edge.label_display }}],
      data: Object.assign({{ type: "data" }}, edge)
    }}));

    const execEdges = [];
    if (toggles.showExecution && Array.isArray(data.flow.execution_order)) {{
      const order = data.flow.execution_order;
      for (let i = 0; i < order.length - 1; i += 1) {{
        const from = order[i];
        const to = order[i + 1];
        if (!blockIds.has(from) || !blockIds.has(to)) {{
          addAnomaly("warn", "execution_order references missing block", {{ from, to }});
          continue;
        }}
        execEdges.push({{
          id: `exec_${{i}}_${{from}}_${{to}}`,
          sources: [from],
          targets: [to],
          labels: [{{ text: "exec" }}],
          data: {{ type: "exec", from, to }}
        }});
      }}
    }}

    const callEdges = [];
    if (toggles.showCallGraph && data.flow.call_graph && data.flow.call_graph.nodes) {{
      const nodes = data.flow.call_graph.nodes;
      const functionToBlock = {{}};
      Object.keys(nodes).forEach(name => {{
        if (nodes[name] && nodes[name].block_id) functionToBlock[name] = nodes[name].block_id;
      }});

      Object.keys(nodes).forEach(name => {{
        const callerBlock = functionToBlock[name];
        if (!callerBlock) return;
        const callees = Array.isArray(nodes[name].callees) ? nodes[name].callees : [];
        callees.forEach(calleeName => {{
          const calleeBlock = functionToBlock[calleeName];
          if (!calleeBlock) {{
            addAnomaly("warn", "call_graph mapping failed", {{ caller: name, callee: calleeName }});
            return;
          }}
          if (!blockIds.has(callerBlock) || !blockIds.has(calleeBlock)) {{
            addAnomaly("warn", "call_graph references missing block", {{ callerBlock, calleeBlock }});
            return;
          }}
          callEdges.push({{
            id: `call_${{name}}_${{calleeName}}`,
            sources: [callerBlock],
            targets: [calleeBlock],
            labels: [{{ text: "call" }}],
            data: {{ type: "call", caller: name, callee: calleeName }}
          }});
        }});
      }});
    }}

    return mergedDataEdges.concat(execEdges, callEdges);
  }}

  function layoutWithELK(nodes, edges, elkOptions) {{
    const elk = new ELK();
    const graph = {{
      id: "root",
      layoutOptions: elkOptions,
      children: nodes,
      edges: edges
    }};
    return elk.layout(graph);
  }}

  function renderSVG(layout, svgEl) {{
    const nodes = layout.children || [];
    const edges = layout.edges || [];

    let minX = 0;
    let minY = 0;
    let maxX = 0;
    let maxY = 0;

    function trackPoint(x, y) {{
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }}

    nodes.forEach(node => {{
      trackPoint(node.x, node.y);
      trackPoint(node.x + node.width, node.y + node.height);
    }});

    edges.forEach(edge => {{
      (edge.sections || []).forEach(section => {{
        trackPoint(section.startPoint.x, section.startPoint.y);
        (section.bendPoints || []).forEach(bp => trackPoint(bp.x, bp.y));
        trackPoint(section.endPoint.x, section.endPoint.y);
      }});
    }});

    const padding = 40;
    const viewBox = [
      minX - padding,
      minY - padding,
      (maxX - minX) + padding * 2,
      (maxY - minY) + padding * 2
    ].join(" ");

    const edgeParts = edges.map(edge => {{
      const sections = edge.sections || [];
      if (!sections.length) return "";
      const section = sections[0];
      const points = [section.startPoint]
        .concat(section.bendPoints || [])
        .concat([section.endPoint]);
      const polyline = points.map(p => `${{p.x}},${{p.y}}`).join(" ");
      const edgeType = edge.data && edge.data.type ? edge.data.type : "data";
      const className = edgeType === "exec" ? "edge-line exec" : edgeType === "call" ? "edge-line call" : "edge-line";
      const label = edge.labels && edge.labels[0] ? edge.labels[0].text : "";
      const midPoint = points[Math.floor(points.length / 2)];
      return `
        <g class=\"edge-group\" data-edge-id=\"${{edge.id}}\">
          <polyline class=\"${{className}}\" points=\"${{polyline}}\" />
          <text class=\"edge-label\" x=\"${{midPoint.x + 6}}\" y=\"${{midPoint.y - 6}}\">${{label}}</text>
        </g>`;
      // TODO: add edge label toggle if needed.
    }}).join("");

    const nodeParts = nodes.map(node => {{
      const data = node.data || {{}};
      const lines = [data.title].concat(data.bodyLines || []);
      const textLines = lines.map((line, idx) => {{
        const y = node.y + 24 + idx * 16;
        const className = idx === 0 ? "node-title" : "node-line";
        return `<text class=\"${{className}}\" x=\"${{node.x + 12}}\" y=\"${{y}}\">${{line}}</text>`;
      }}).join("");
      const loopClass = data.data && data.data.cfg && Array.isArray(data.data.cfg.loops) && data.data.cfg.loops.length ? "loop" : "";
      return `
        <g class=\"node-group\" data-node-id=\"${{node.id}}\">
          <rect class=\"node-box ${{loopClass}}\" x=\"${{node.x}}\" y=\"${{node.y}}\" width=\"${{node.width}}\" height=\"${{node.height}}\"></rect>
          ${{textLines}}
        </g>`;
    }}).join("");

    svgEl.setAttribute("viewBox", viewBox);
    svgEl.innerHTML = `
      <g id=\"viewport\" transform=\"translate(${{state.viewTransform.x}}, ${{state.viewTransform.y}}) scale(${{state.viewTransform.k}})\">
        ${{edgeParts}}
        ${{nodeParts}}
      </g>`;
  }}

  function applySearchHighlight(state, query) {{
    const svg = document.getElementById("diagramSvg");
    const nodeGroups = svg.querySelectorAll(".node-group");
    const q = query.trim().toLowerCase();
    nodeGroups.forEach(group => {{
      const id = group.getAttribute("data-node-id");
      const block = state.blockMap[id];
      const name = block && block.block_name ? block.block_name.toLowerCase() : "";
      if (!q) {{
        group.classList.remove("highlight");
      }} else if ((id && id.toLowerCase().includes(q)) || name.includes(q)) {{
        group.classList.add("highlight");
      }} else {{
        group.classList.remove("highlight");
      }}
    }});
  }}

  function getSequenceOrderModeIds(model = SEQUENCE_EXECUTION_MODEL) {{
    const modes = Array.isArray(model.order_modes) ? model.order_modes.map(mode => mode.id).filter(Boolean) : [];
    return modes.length ? modes : ["call", "dependency", "pipeline"];
  }}

  function getSequenceOrderMode(state, model = SEQUENCE_EXECUTION_MODEL) {{
    const modes = getSequenceOrderModeIds(model);
    const fallback = model.default_order_mode || "call";
    return modes.includes(state.sequenceOrderMode) ? state.sequenceOrderMode : fallback;
  }}

  function getSequenceModeMeta(model, modeId) {{
    const modes = Array.isArray(model.order_modes) ? model.order_modes : [];
    return modes.find(mode => mode.id === modeId) || {{ id: modeId, label: modeId, description: "" }};
  }}

  function getActiveSequenceModel(state, model = SEQUENCE_EXECUTION_MODEL) {{
    const mode = getSequenceOrderMode(state, model);
    const layout = model.layouts && model.layouts[mode] ? model.layouts[mode] : model;
    return Object.assign({{}}, model, layout, {{
      active_order_mode: mode,
      order_mode: mode,
      edges: model.edges || []
    }});
  }}

  function getSequenceBlockMapForMode(state, mode = getSequenceOrderMode(state)) {{
    if (!state.sequenceBlockMapsByMode || typeof state.sequenceBlockMapsByMode !== "object") {{
      state.sequenceBlockMapsByMode = {{}};
    }}
    if (!state.sequenceBlockMapsByMode[mode] || typeof state.sequenceBlockMapsByMode[mode] !== "object") {{
      state.sequenceBlockMapsByMode[mode] = {{}};
    }}
    state.sequenceBlockMap = state.sequenceBlockMapsByMode[mode];
    return state.sequenceBlockMapsByMode[mode];
  }}

  function setSequenceOrderMode(state, mode) {{
    state.sequenceOrderMode = mode;
    getSequenceBlockMapForMode(state, getSequenceOrderMode(state));
    renderSequence(state);
  }}

  function applyInitialViewerRouteParams(state) {{
    const params = new URLSearchParams(window.location.search || "");
    const tabParam = (params.get("tab") || params.get("view") || "").toLowerCase();
    if (tabParam === "sequence") {{
      state.activeTab = "sequence";
    }} else if (tabParam === "diagram") {{
      state.activeTab = "diagram";
    }}

    const requestedOrder = params.get("sequence_order_mode")
      || params.get("sequenceOrderMode")
      || params.get("order");
    if (requestedOrder && getSequenceOrderModeIds().includes(requestedOrder)) {{
      state.sequenceOrderMode = requestedOrder;
      getSequenceBlockMapForMode(state, requestedOrder);
    }}
  }}

  function resetSequenceCardsOnly(state) {{
    const mode = getSequenceOrderMode(state);
    if (!state.sequenceBlockMapsByMode || typeof state.sequenceBlockMapsByMode !== "object") state.sequenceBlockMapsByMode = {{}};
    state.sequenceBlockMapsByMode[mode] = {{}};
    state.sequenceBlockMap = state.sequenceBlockMapsByMode[mode];
    state.sequenceMap = {{}};
    state.sequenceGroupMap = {{}};
    renderSequence(state);
  }}

  function resetSequenceCurrentMode(state) {{
    state.sequenceZoom = 1;
    resetSequenceCardsOnly(state);
  }}

  function resetCurrentView(state, svg) {{
    if (state.activeTab === 'sequence') {{
      resetSequenceCurrentMode(state);
      return;
    }}
    state.viewTransform = Object.assign({{}}, state.initialTransform);
    if (state.layout) renderSVG(state.layout, svg);
  }}

  function getDiagramPanDelta(svgEl, state, previousPoint, nextPoint) {{
    const rect = svgEl.getBoundingClientRect();
    const viewBox = svgEl.viewBox && svgEl.viewBox.baseVal ? svgEl.viewBox.baseVal : null;
    const unitsPerPixelX = viewBox && rect.width ? viewBox.width / rect.width : 1;
    const unitsPerPixelY = viewBox && rect.height ? viewBox.height / rect.height : 1;
    return {{
      x: (nextPoint.x - previousPoint.x) * unitsPerPixelX * DIAGRAM_PAN_SPEED,
      y: (nextPoint.y - previousPoint.y) * unitsPerPixelY * DIAGRAM_PAN_SPEED
    }};
  }}

  function diagramClientPointToViewport(svgEl, clientPoint) {{
    const rect = svgEl.getBoundingClientRect();
    const viewBox = svgEl.viewBox && svgEl.viewBox.baseVal ? svgEl.viewBox.baseVal : null;
    const unitsPerPixelX = viewBox && rect.width ? viewBox.width / rect.width : 1;
    const unitsPerPixelY = viewBox && rect.height ? viewBox.height / rect.height : 1;
    const originX = viewBox ? viewBox.x : 0;
    const originY = viewBox ? viewBox.y : 0;
    return {{
      x: originX + (clientPoint.x - rect.left) * unitsPerPixelX,
      y: originY + (clientPoint.y - rect.top) * unitsPerPixelY
    }};
  }}

  function zoomDiagramAtPointer(svgEl, state, event) {{
    const point = diagramClientPointToViewport(svgEl, {{ x: event.clientX, y: event.clientY }});
    const oldZoom = Math.max(0.1, state.viewTransform.k || 1);
    const zoomFactor = event.deltaY < 0 ? 1.1 : 0.9;
    const nextZoom = Math.min(3.5, Math.max(0.25, oldZoom * zoomFactor));
    const diagramPointX = (point.x - state.viewTransform.x) / oldZoom;
    const diagramPointY = (point.y - state.viewTransform.y) / oldZoom;
    state.viewTransform.k = nextZoom;
    state.viewTransform.x = point.x - diagramPointX * nextZoom;
    state.viewTransform.y = point.y - diagramPointY * nextZoom;
  }}

  function clonePlainObject(value) {{
    return value && typeof value === "object" && !Array.isArray(value) ? Object.assign({{}}, value) : {{}};
  }}

  function serializeSequenceMap(state) {{
    const mode = getSequenceOrderMode(state);
    const mapsByMode = state.sequenceBlockMapsByMode && typeof state.sequenceBlockMapsByMode === "object"
      ? state.sequenceBlockMapsByMode
      : {{}};
    const sequenceBlockMapsByMode = {{}};
    getSequenceOrderModeIds().forEach(mode => {{
      sequenceBlockMapsByMode[mode] = clonePlainObject(mapsByMode[mode]);
    }});
    return {{
      version: 5,
      renderer: "sequence-execution-board",
      sequence_order_mode: mode,
      sequence_edge_density_mode: state.sequenceEdgeDensityMode || "all",
      sequence_stage_filter: state.sequenceStageFilter || "all",
      nodes: state.sequenceMap || {{}},
      groups: state.sequenceGroupMap || {{}},
      sequence_block_positions_by_mode: sequenceBlockMapsByMode,
      sequence_block_positions: sequenceBlockMapsByMode.dependency || {{}}
    }};
  }}

  function loadSequenceMapPayload(state, data) {{
    const payload = data && typeof data === "object" ? data : {{}};
    state.sequenceMap = payload.nodes && typeof payload.nodes === "object" ? payload.nodes : {{}};
    state.sequenceGroupMap = payload.groups && typeof payload.groups === "object" ? payload.groups : {{}};
    if (payload.sequence_edge_density_mode) state.sequenceEdgeDensityMode = String(payload.sequence_edge_density_mode);
    if (payload.sequence_stage_filter !== undefined && payload.sequence_stage_filter !== null) {{
      state.sequenceStageFilter = String(payload.sequence_stage_filter);
    }}
    const mapsByMode = payload.sequence_block_positions_by_mode;
    if (mapsByMode && typeof mapsByMode === "object") {{
      state.sequenceBlockMapsByMode = {{}};
      getSequenceOrderModeIds().forEach(mode => {{
        state.sequenceBlockMapsByMode[mode] = clonePlainObject(mapsByMode[mode]);
      }});
      Object.keys(mapsByMode).forEach(mode => {{
        if (!state.sequenceBlockMapsByMode[mode]) state.sequenceBlockMapsByMode[mode] = clonePlainObject(mapsByMode[mode]);
      }});
      if (payload.sequence_order_mode) state.sequenceOrderMode = String(payload.sequence_order_mode);
    }} else {{
      const legacyPositions = payload.sequence_block_positions || payload.block_positions || payload.sequenceBlockMap;
      if (!state.sequenceBlockMapsByMode || typeof state.sequenceBlockMapsByMode !== "object") state.sequenceBlockMapsByMode = {{}};
      if (!state.sequenceBlockMapsByMode.call || typeof state.sequenceBlockMapsByMode.call !== "object") state.sequenceBlockMapsByMode.call = {{}};
      state.sequenceBlockMapsByMode.dependency = legacyPositions && typeof legacyPositions === "object" ? legacyPositions : {{}};
      state.sequenceOrderMode = "dependency";
    }}
    getSequenceBlockMapForMode(state, getSequenceOrderMode(state));
    renderSequence(state);
  }}

  function exportSequenceMap(state) {{
    const payload = JSON.stringify(serializeSequenceMap(state), null, 2);
    const blob = new Blob([payload], {{ type: "application/json" }});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "sequence_map.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }}

  function exportSequenceMapPayload(state) {{
    return serializeSequenceMap(state);
  }}

  function importSequenceMapPayload(state, payload) {{
    loadSequenceMapPayload(state, payload);
    return serializeSequenceMap(state);
  }}

  function installViewerTestHooks(state) {{
    const params = new URLSearchParams(window.location.search || "");
    if (!params.has("test-hooks")) return;
    if (document.getElementById("cvas-viewer-test-hooks")) return;
    const panel = document.createElement("section");
    panel.id = "cvas-viewer-test-hooks";
    panel.setAttribute("data-testid", "cvas-viewer-test-hooks");
    panel.style.position = "fixed";
    panel.style.left = "12px";
    panel.style.bottom = "12px";
    panel.style.zIndex = "1000";
    panel.style.display = "grid";
    panel.style.gap = "6px";
    panel.style.width = "340px";
    panel.style.padding = "8px";
    panel.style.border = "1px solid #94a3b8";
    panel.style.background = "#ffffff";
    panel.style.boxShadow = "0 8px 24px rgba(15, 23, 42, 0.16)";

    const input = document.createElement("textarea");
    input.id = "cvas-test-map-input";
    input.setAttribute("data-testid", "cvas-test-map-input");
    input.rows = 4;
    const output = document.createElement("textarea");
    output.id = "cvas-test-map-output";
    output.setAttribute("data-testid", "cvas-test-map-output");
    output.rows = 4;
    output.readOnly = true;

    const exportBtn = document.createElement("button");
    exportBtn.id = "cvas-test-export-map";
    exportBtn.setAttribute("data-testid", "cvas-test-export-map");
    exportBtn.type = "button";
    exportBtn.textContent = "Export payload";
    exportBtn.addEventListener("click", () => {{
      output.value = JSON.stringify(exportSequenceMapPayload(state), null, 2);
    }});

    const importBtn = document.createElement("button");
    importBtn.id = "cvas-test-import-map";
    importBtn.setAttribute("data-testid", "cvas-test-import-map");
    importBtn.type = "button";
    importBtn.textContent = "Import payload";
    importBtn.addEventListener("click", () => {{
      try {{
        const rawPayload = input.value || output.value || "{{}}";
        output.value = JSON.stringify(importSequenceMapPayload(state, JSON.parse(rawPayload)), null, 2);
      }} catch (err) {{
        output.value = JSON.stringify({{ error: String(err) }}, null, 2);
      }}
    }});

    panel.appendChild(input);
    panel.appendChild(output);
    panel.appendChild(exportBtn);
    panel.appendChild(importBtn);
    document.body.appendChild(panel);
  }}

  function bindUI(state) {{
    const svg = document.getElementById("diagramSvg");
    const detailPanel = document.getElementById("detail-json");
    const searchInput = document.getElementById("searchInput");
    const toggleData = document.getElementById("toggleData");
    const toggleExec = document.getElementById("toggleExec");
    const toggleCall = document.getElementById("toggleCall");
    const resetBtn = document.getElementById("resetBtn");
    const tabDiagram = document.getElementById("tabDiagram");
    const tabSequence = document.getElementById("tabSequence");
    const seqZoomOut = document.getElementById("seqZoomOut");
    const seqZoomReset = document.getElementById("seqZoomReset");
    const seqZoomIn = document.getElementById("seqZoomIn");
    const diagramPanel = document.getElementById("diagram-panel");
    const sequencePanel = document.getElementById("sequence-panel");
    const seqLoad = document.getElementById("seqLoad");
    const seqExport = document.getElementById("seqExport");
    const seqFile = document.getElementById("seqFile");
    const ioLoad = document.getElementById("ioLoad");
    const ioFile = document.getElementById("ioFile");
    const detailToggle = document.getElementById("detailToggle");

    searchInput.addEventListener("input", event => {{
      applySearchHighlight(state, event.target.value);
    }});

    function rerender() {{
      state.anomalies = [];
      const edges = buildEdges(state.data, state.toggles);
      layoutWithELK(state.nodes, edges, state.elkOptions).then(layout => {{
        state.layout = layout;
        renderSVG(layout, svg);
        applySearchHighlight(state, searchInput.value);
        updateAnomalies(state);
      }});
    }}

    toggleData.addEventListener("change", () => {{
      state.toggles.showDataFlow = toggleData.checked;
      rerender();
    }});
    toggleExec.addEventListener("change", () => {{
      state.toggles.showExecution = toggleExec.checked;
      rerender();
    }});
    toggleCall.addEventListener("change", () => {{
      state.toggles.showCallGraph = toggleCall.checked;
      rerender();
    }});

    resetBtn.addEventListener("click", () => {{
      resetCurrentView(state, svg);
    }});

    function setTab(next) {{
      state.activeTab = next;
      if (next === "diagram") {{
        diagramPanel.hidden = false;
        sequencePanel.hidden = true;
        tabDiagram.classList.add("active");
        tabSequence.classList.remove("active");
      }} else {{
        diagramPanel.hidden = true;
        sequencePanel.hidden = false;
        tabSequence.classList.add("active");
        tabDiagram.classList.remove("active");
        redrawActiveSequenceEdges(state);
        requestAnimationFrame(() => {{
          redrawActiveSequenceEdges(state);
        }});
      }}
    }}

    tabDiagram.addEventListener("click", () => setTab("diagram"));
    tabSequence.addEventListener("click", () => setTab("sequence"));
    detailToggle.addEventListener("click", () => toggleDetailsPanel(state));

    function updateSequenceZoom(nextZoom) {{
      state.sequenceZoom = Math.min(3.5, Math.max(0.25, nextZoom));
      applySequenceZoom(state);
      redrawActiveSequenceEdges(state);
    }}

    seqZoomOut.addEventListener("click", () => {{
      updateSequenceZoom((state.sequenceZoom || 1) / 1.15);
    }});
    seqZoomReset.addEventListener("click", () => {{
      updateSequenceZoom(1);
    }});
    seqZoomIn.addEventListener("click", () => {{
      updateSequenceZoom((state.sequenceZoom || 1) * 1.15);
    }});

    sequencePanel.addEventListener("wheel", event => {{
      if (!event.ctrlKey) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.1 : 0.9;
      updateSequenceZoom((state.sequenceZoom || 1) * factor);
    }}, {{ passive: false }});

    window.addEventListener("resize", () => {{
      redrawActiveSequenceEdges(state);
    }});

    seqLoad.addEventListener("click", () => seqFile.click());
    seqFile.addEventListener("change", event => {{
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {{
        try {{
          const data = JSON.parse(reader.result);
          loadSequenceMapPayload(state, data);
        }} catch (err) {{
          alert("Failed to load map: " + err);
        }}
      }};
      reader.readAsText(file);
      seqFile.value = "";
    }});

    seqExport.addEventListener("click", () => exportSequenceMap(state));

    ioLoad.addEventListener("click", () => ioFile.click());
    ioFile.addEventListener("change", event => {{
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {{
        try {{
          const data = JSON.parse(reader.result);
          state.functionIO = data || {{}};
          state.functionIOSource = "loaded from file";
          updateIOSourceStatus(state);
          renderSequence(state);
        }} catch (err) {{
          alert("Failed to load IO map: " + err);
        }}
      }};
      reader.readAsText(file);
      ioFile.value = "";
    }});

    let isPanning = false;
    let lastPanPoint = {{ x: 0, y: 0 }};

    svg.addEventListener("mousedown", event => {{
      if (event.button !== 0) return;
      isPanning = true;
      lastPanPoint = {{ x: event.clientX, y: event.clientY }};
    }});
    window.addEventListener("mousemove", event => {{
      if (!isPanning) return;
      const nextPanPoint = {{ x: event.clientX, y: event.clientY }};
      const delta = getDiagramPanDelta(svg, state, lastPanPoint, nextPanPoint);
      state.viewTransform.x += delta.x;
      state.viewTransform.y += delta.y;
      lastPanPoint = nextPanPoint;
      if (state.layout) renderSVG(state.layout, svg);
    }});
    window.addEventListener("mouseup", () => {{
      isPanning = false;
    }});

    svg.addEventListener("wheel", event => {{
      event.preventDefault();
      zoomDiagramAtPointer(svg, state, event);
      if (state.layout) renderSVG(state.layout, svg);
    }}, {{ passive: false }});

    svg.addEventListener("click", event => {{
      const nodeGroup = event.target.closest(".node-group");
      const edgeGroup = event.target.closest(".edge-group");
      if (nodeGroup) {{
        const nodeId = nodeGroup.getAttribute("data-node-id");
        state.selectedNodeId = nodeId;
        state.selectedEdgeKey = null;
        const block = state.blockMap[nodeId];
        detailPanel.textContent = JSON.stringify(block || {{ error: "Node not found" }}, null, 2);
        return;
      }}
      if (edgeGroup) {{
        const edgeId = edgeGroup.getAttribute("data-edge-id");
        state.selectedEdgeKey = edgeId;
        state.selectedNodeId = null;
        const edge = (state.layout.edges || []).find(item => item.id === edgeId);
        detailPanel.textContent = JSON.stringify(edge && edge.data ? edge.data : {{ error: "Edge not found" }}, null, 2);
      }}
    }});

    setTab(state.activeTab === "sequence" ? "sequence" : "diagram");
    rerender();
  }}

  function updateAnomalies(state) {{
    const panel = document.getElementById("anomaly-list");
    const list = document.getElementById("anomaly-items");
    list.innerHTML = "";
    if (!state.anomalies.length) {{
      panel.hidden = true;
      return;
    }}
    state.anomalies.forEach(anomaly => {{
      const li = document.createElement("li");
      li.textContent = `${{anomaly.kind.toUpperCase()}}: ${{anomaly.message}}`;
      list.appendChild(li);
    }});
    panel.hidden = false;
  }}

  function init() {{
    const data = parseData(DATA);
    const nodes = buildNodes(data);
    const blockMap = {{}};
    data.blocks.forEach(block => {{
      blockMap[block.block_id] = block;
    }});

    const elkOptions = {{
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "50",
      "elk.layered.spacing.nodeNodeBetweenLayers": "80",
      "elk.layered.spacing.edgeNodeBetweenLayers": "40"
    }};

    state.data = data;
    state.nodes = nodes;
    state.blockMap = blockMap;
    state.elkOptions = elkOptions;
    state.functionIO = (DEFAULT_FUNCTION_IO && typeof DEFAULT_FUNCTION_IO === "object") ? DEFAULT_FUNCTION_IO : {{}};
    state.functionIOSource = Object.keys(state.functionIO).length ? "embedded" : "none";
    applyInitialViewerRouteParams(state);
    updateIOSourceStatus(state);
    applyDetailsPanelState(state);
    renderAnalysisSummary(state);

    renderSequence(state);
    bindUI(state);
    installViewerTestHooks(state);
    // Optional fetch-based override for workflows that keep function_io.json outside the embedded build.
    autoLoadFunctionIO(state);
  }}

  async function autoLoadFunctionIO(state) {{
    const candidates = ["./function_io.json", "../function_io.json"];
    for (const path of candidates) {{
      try {{
        const resp = await fetch(path, {{ cache: "no-store" }});
        if (!resp.ok) continue;
        const data = await resp.json();
        if (data && typeof data === "object") {{
          state.functionIO = data;
          state.functionIOSource = `auto-loaded (${{path}})`;
          updateIOSourceStatus(state);
          renderSequence(state);
          return;
        }}
      }} catch (err) {{
        // Ignore and try next candidate.
      }}
    }}
  }}

  function detectSchemaVersion(data) {{
    if (data && typeof data.schema_version === "string") return data.schema_version;
    if (data && data.schema && typeof data.schema.version === "string") return data.schema.version;
    return "2.x";
  }}

  function normalizeFunctionIOMap(value) {{
    if (!value || typeof value !== "object") return {{}};
    if (value.functions && typeof value.functions === "object") return value.functions;
    return value;
  }}

  function getFunctionIOMap(state) {{
    const flowIO = state.data && state.data.flow ? state.data.flow.function_io : null;
    const flowMap = normalizeFunctionIOMap(flowIO);
    if (Object.keys(flowMap).length) return flowMap;
    return normalizeFunctionIOMap(state.functionIO);
  }}

  function renderSequence(state) {{
    const flow = state.data && state.data.flow ? state.data.flow : {{}};
    const timeline = Array.isArray(flow.sequence_timeline) ? flow.sequence_timeline : [];
    const schemaVersion = detectSchemaVersion(state.data);
    if (timeline.length) {{
      renderSequenceExecutionDiagramV3(state, SEQUENCE_EXECUTION_MODEL, schemaVersion);
      return;
    }}
    renderLegacySequence(state, schemaVersion);
  }}

  function appendTimelineText(parent, className, text) {{
    const el = document.createElement("div");
    el.className = className;
    el.textContent = text;
    parent.appendChild(el);
    return el;
  }}

  function appendTimelinePill(parent, text) {{
    const pill = document.createElement("span");
    pill.className = "timeline-pill";
    pill.textContent = text;
    parent.appendChild(pill);
    return pill;
  }}

  function makeIdMap(items, key) {{
    const map = new Map();
    if (!Array.isArray(items)) return map;
    items.forEach(item => {{
      if (item && item[key] != null) map.set(item[key], item);
    }});
    return map;
  }}

  function renderTimelineList(parent, title, items, renderItem) {{
    const section = document.createElement("div");
    section.className = "timeline-section";
    appendTimelineText(section, "timeline-section-title", title);
    if (!items.length) {{
      appendTimelineText(section, "timeline-empty", "none");
    }} else {{
      const list = document.createElement("ul");
      list.className = "timeline-list";
      items.forEach(item => {{
        const li = document.createElement("li");
        li.className = "timeline-item";
        renderItem(li, item);
        list.appendChild(li);
      }});
      section.appendChild(list);
    }}
    parent.appendChild(section);
  }}

  function describeCall(call) {{
    if (!call) return "missing call";
    const callId = call.call_id || "unknown";
    const caller = call.caller_function || call.caller_block_id || "?";
    const callee = call.callee_function || call.callee_block_id || "?";
    const args = Array.isArray(call.args) ? call.args.map(arg => arg.expr || "?").join(", ") : "";
    const assigned = call.assigned && call.assigned.target ? ` -> ${{call.assigned.target}}` : "";
    return `${{callId}}: ${{caller}} -> ${{callee}}(${{args}})${{assigned}}`;
  }}

  function describeSignal(signal) {{
    if (!signal) return "missing signal";
    const signalId = signal.signal_id || "unknown";
    const callId = signal.call_id ? ` call_id=${{signal.call_id}}` : "";
    const kind = signal.kind || signal.comment || "signal";
    const expr = signal.expr || signal.signal_name || "";
    const param = signal.param ? ` param=${{signal.param}}` : "";
    return `${{signalId}}: ${{kind}} ${{expr}}${{param}}${{callId}}`;
  }}

  function asStringList(value) {{
    if (!Array.isArray(value)) return [];
    return value.filter(item => item != null).map(item => String(item));
  }}

  function callArgExprByParam(call) {{
    const map = new Map();
    const args = call && Array.isArray(call.args) ? call.args : [];
    args.forEach((arg, index) => {{
      if (arg && typeof arg === "object") {{
        if (arg.param != null && arg.expr != null) map.set(String(arg.param), String(arg.expr));
      }} else if (arg != null && Array.isArray(call.callee_params) && index < call.callee_params.length) {{
        map.set(String(call.callee_params[index]), String(arg));
      }}
    }});
    return map;
  }}

  function mapCallIONames(names, call) {{
    if (!call) return names;
    const exprByParam = callArgExprByParam(call);
    return names.map(name => exprByParam.has(name) ? exprByParam.get(name) : name);
  }}

  function timelineIOItem(functionIOMap, functionName, role, call) {{
    if (!functionName || !functionIOMap || !functionIOMap[functionName]) return null;
    const io = functionIOMap[functionName];
    if (!io || typeof io !== "object") return null;
    const contractReads = asStringList(io.reads);
    const contractWrites = asStringList(io.writes);
    const item = {{
      role,
      function: functionName,
      reads: mapCallIONames(contractReads, call),
      writes: mapCallIONames(contractWrites, call),
      contract_reads: contractReads,
      contract_writes: contractWrites
    }};
    if (call) {{
      item.call_id = call.call_id || "";
      item.caller_function = call.caller_function || "";
      item.callee_function = call.callee_function || "";
      if (call.assigned && call.assigned.target != null) item.assigned = String(call.assigned.target);
    }}
    if (io.provenance && typeof io.provenance === "object") item.provenance = io.provenance;
    return item;
  }}

  function summarizeTimelineFunctionIO(state, step, functionIOMap, callById) {{
    const summary = [];
    if (!step || !functionIOMap || !Object.keys(functionIOMap).length) return summary;
    const stepItem = timelineIOItem(functionIOMap, step.function, "step_function", null);
    if (stepItem) summary.push(stepItem);
    (step.call_ids_as_caller || []).forEach(callId => {{
      const call = callById.get(callId);
      if (!call) return;
      const item = timelineIOItem(functionIOMap, call.callee_function, "called_function", call);
      if (item) summary.push(item);
    }});
    (step.call_ids_as_callee || []).forEach(callId => {{
      const call = callById.get(callId);
      if (!call) return;
      const item = timelineIOItem(functionIOMap, call.caller_function, "caller_function", call);
      if (item) summary.push(item);
    }});
    return summary;
  }}

  function describeTimelineIO(item) {{
    const callId = item.call_id ? ` ${{item.call_id}}` : "";
    const reads = item.reads && item.reads.length ? item.reads.join(", ") : "-";
    const writes = item.writes && item.writes.length ? item.writes.join(", ") : "-";
    const assigned = item.assigned ? ` return->${{item.assigned}}` : "";
    const provenance = item.provenance && item.provenance.source ? ` source=${{item.provenance.source}}` : "";
    return `${{item.role}}${{callId}} ${{item.function}} reads: ${{reads}}; writes: ${{writes}}${{assigned}}${{provenance}}`;
  }}

  function sequenceSummaryItems(step, bucketNames) {{
    const summary = step && step.read_write_summary && typeof step.read_write_summary === "object"
      ? step.read_write_summary
      : {{}};
    const items = [];
    bucketNames.forEach(name => {{
      const bucket = Array.isArray(summary[name]) ? summary[name] : [];
      bucket.forEach(item => items.push(item));
    }});
    return items;
  }}

  function appendSequenceChip(parent, className, prefix, text) {{
    const chip = document.createElement("span");
    chip.className = `sequence-chip ${{className}}`;
    chip.textContent = `${{prefix}} ${{text || "signal"}}`;
    parent.appendChild(chip);
    return chip;
  }}

  function chipTextFromSummaryItem(item) {{
    if (!item || typeof item !== "object") return "signal";
    return item.signal_name || item.target || item.expr || item.param || item.signal_id || "signal";
  }}

  function renderSequenceChipRows(card, step) {{
    const reads = sequenceSummaryItems(step, ["reads_from_other", "read_by_other"]);
    const writes = sequenceSummaryItems(step, ["writes_to_other", "written_by_other"]);
    const row = document.createElement("div");
    row.className = "sequence-chip-row";
    const limit = 5;
    let rendered = 0;
    reads.slice(0, limit).forEach(item => {{
      appendSequenceChip(row, "read", "R:", chipTextFromSummaryItem(item));
      rendered += 1;
    }});
    writes.slice(0, Math.max(0, limit - rendered)).forEach(item => {{
      appendSequenceChip(row, "write", "W:", chipTextFromSummaryItem(item));
      rendered += 1;
    }});
    const remaining = reads.length + writes.length - rendered;
    if (remaining > 0) appendSequenceChip(row, "", "+", `${{remaining}} more`);
    if (!rendered && remaining <= 0) appendSequenceChip(row, "", "R/W:", "no external signals");
    card.appendChild(row);
  }}

  function renderSequenceOrderModeControls(state, model, toolbar) {{
    const wrapper = document.createElement("label");
    wrapper.className = "mode-group";
    wrapper.title = "Switch Sequence board interpretation: root/caller order, static dependency order, or pipeline stage order";
    wrapper.appendChild(document.createTextNode("Order"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Sequence order mode");
    const modes = Array.isArray(model.order_modes) ? model.order_modes : [];
    modes.forEach(mode => {{
      const option = document.createElement("option");
      option.value = mode.id;
      option.textContent = mode.label || mode.id;
      option.title = mode.description || "";
      select.appendChild(option);
    }});
    select.value = getSequenceOrderMode(state, model);
    select.addEventListener("change", () => setSequenceOrderMode(state, select.value));
    wrapper.appendChild(select);
    toolbar.appendChild(wrapper);
  }}

  function renderSequenceLabelModeControls(state, toolbar) {{
    const wrapper = document.createElement("label");
    wrapper.className = "mode-group";
    wrapper.title = "Edge labels: Compact labels, All labels, or No labels";
    wrapper.appendChild(document.createTextNode("Edge labels"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Sequence edge label density");
    [
      ["compact", "Compact labels"],
      ["all", "All labels"],
      ["off", "No labels"]
    ].forEach(([value, label]) => {{
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }});
    select.value = state.sequenceLabelMode || "compact";
    select.addEventListener("change", () => {{
      state.sequenceLabelMode = select.value;
      redrawActiveSequenceEdges(state);
    }});
    wrapper.appendChild(select);
    toolbar.appendChild(wrapper);
  }}

  function renderSequenceEdgeDensityControls(state, toolbar) {{
    const wrapper = document.createElement("label");
    wrapper.className = "mode-group";
    wrapper.title = "Sequence edge density: all edges, stage-local edges, or selected stage only";
    wrapper.appendChild(document.createTextNode("Edges"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Sequence edge density");
    [
      ["all", "All edges"],
      ["stage_local", "Stage-local edges"],
      ["selected_stage", "Selected stage only"]
    ].forEach(([value, label]) => {{
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }});
    select.value = state.sequenceEdgeDensityMode || "all";
    select.addEventListener("change", () => {{
      state.sequenceEdgeDensityMode = select.value;
      redrawActiveSequenceEdges(state);
    }});
    wrapper.appendChild(select);
    toolbar.appendChild(wrapper);
  }}

  function getSequenceStageFilterOptions(model) {{
    const stages = new Set();
    (model.steps || []).forEach(step => {{
      if (step.pipeline_stage !== undefined && step.pipeline_stage !== null) {{
        stages.add(String(step.pipeline_stage));
      }}
    }});
    return Array.from(stages).sort((a, b) => Number(a) - Number(b));
  }}

  function renderSequenceStageFilterControls(state, model, toolbar) {{
    const wrapper = document.createElement("label");
    wrapper.className = "mode-group";
    wrapper.title = "Limit pipeline edges to a selected stage";
    wrapper.appendChild(document.createTextNode("Stage"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Sequence stage filter");
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All stages";
    select.appendChild(all);
    getSequenceStageFilterOptions(model).forEach(stage => {{
      const option = document.createElement("option");
      option.value = stage;
      option.textContent = `Stage ${{stage}}`;
      select.appendChild(option);
    }});
    select.value = state.sequenceStageFilter || "all";
    select.disabled = select.options.length <= 1;
    select.addEventListener("change", () => {{
      state.sequenceStageFilter = select.value;
      redrawActiveSequenceEdges(state);
    }});
    wrapper.appendChild(select);
    toolbar.appendChild(wrapper);
  }}

  function renderSequenceToolbar(state, shell, model) {{
    const toolbar = document.createElement("div");
    toolbar.className = "sequence-toolbar";
    const title = document.createElement("strong");
    title.textContent = "Sequence execution board";
    toolbar.appendChild(title);
    renderSequenceOrderModeControls(state, model, toolbar);
    renderSequenceLabelModeControls(state, toolbar);
    renderSequenceEdgeDensityControls(state, toolbar);
    renderSequenceStageFilterControls(state, getActiveSequenceModel(state, model), toolbar);

    function addToggle(label, key) {{
      const wrapper = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(state.sequenceToggles[key]);
      input.addEventListener("change", () => {{
        state.sequenceToggles[key] = input.checked;
        redrawActiveSequenceEdges(state);
      }});
      wrapper.appendChild(input);
      wrapper.appendChild(document.createTextNode(label));
      toolbar.appendChild(wrapper);
    }}

    addToggle("Data flow", "showDataFlow");
    addToggle("Execution order", "showExecution");
    addToggle("Control/call", "showControl");
    addToggle("Critical path", "showCritical");

    const reset = document.createElement("button");
    reset.type = "button";
    reset.textContent = "Reset cards only";
    reset.addEventListener("click", () => resetSequenceCardsOnly(state));
    toolbar.appendChild(reset);
    shell.appendChild(toolbar);
  }}

  function renderSequenceExecutionDiagramV3(state, model, schemaVersion) {{
    const container = document.getElementById("sequence-content");
    container.innerHTML = "";
    const activeModel = getActiveSequenceModel(state, model);
    const orderMode = getSequenceOrderMode(state, model);
    const modeMeta = getSequenceModeMeta(model, orderMode);
    const blockMap = getSequenceBlockMapForMode(state, orderMode);
    const shell = document.createElement("div");
    shell.className = "sequence-exec-shell";
    shell.dataset.schemaVersion = schemaVersion;
    shell.dataset.sequenceSchema = model.schema || "sequence-execution-diagram-v1";
    shell.dataset.sequenceOrderMode = orderMode;

    const banner = document.createElement("div");
    banner.className = "timeline-banner";
    const flow = state.data.flow || {{}};
    const metaKind = flow.execution_order_meta && flow.execution_order_meta.kind ? flow.execution_order_meta.kind : "static_block_order";
    if (orderMode === "dependency") {{
      banner.textContent = `Schema ${{schemaVersion}} Sequence · Dependency order: static dependency/data producers flow left-to-right where facts allow; dashed arrows show ${{metaKind}}, not a runtime trace.`;
    }} else if (orderMode === "pipeline") {{
      banner.textContent = `Schema ${{schemaVersion}} Sequence · Pipeline stage order: dependency-constrained blocks flow left-to-right; stage groups, when known, share columns with parallel lanes.`;
    }} else {{
      banner.textContent = `Schema ${{schemaVersion}} Sequence · Call order: root/caller blocks start on the left; caller-local call sequence expands right; data arrows retain true direction and may point backward.`;
    }}
    banner.title = modeMeta.description || "";
    shell.appendChild(banner);
    renderSequenceToolbar(state, shell, model);

    const legend = document.createElement("div");
    legend.className = "sequence-legend";
    legend.innerHTML = '<span><i class="sequence-legend-line"></i>data/read/write</span><span><i class="sequence-legend-line exec"></i>execution order</span><span>R:/W: chips summarize signal access</span>';
    shell.appendChild(legend);

    const board = document.createElement("div");
    board.className = "sequence-exec-board";
    const columnWidth = 260;
    const laneHeight = 150;
    const width = Math.max(720, (activeModel.columns || 1) * columnWidth + 80);
    const height = Math.max(320, (activeModel.lanes || 1) * laneHeight + 96);
    board.style.width = `${{width}}px`;
    board.style.height = `${{height}}px`;

    for (let column = 0; column < Math.max(1, activeModel.columns || 1); column += 1) {{
      const marker = document.createElement("div");
      marker.className = "sequence-timestep";
      marker.style.left = `${{column * columnWidth + 12}}px`;
      const columnLabels = Array.isArray(activeModel.column_labels) ? activeModel.column_labels : [];
      marker.textContent = columnLabels[column] || (orderMode === "dependency" ? `D+${{column}}` : (orderMode === "pipeline" ? `Stage ${{column}}` : `Call ${{column}}`));
      board.appendChild(marker);
    }}
    for (let lane = 0; lane < Math.max(1, activeModel.lanes || 1); lane += 1) {{
      const marker = document.createElement("div");
      marker.className = "sequence-lane";
      marker.style.top = `${{lane * laneHeight + 66}}px`;
      marker.textContent = `lane ${{lane}}`;
      board.appendChild(marker);
    }}

    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("class", "sequence-edge-overlay");
    board.appendChild(overlay);

    (activeModel.steps || []).forEach(step => {{
      const card = document.createElement("article");
      card.className = `sequence-exec-card${{step.is_critical ? " critical" : ""}}`;
      card.dataset.stepId = step.step_id || "";
      card.dataset.blockId = step.block_id || "";
      card.dataset.column = String(step.column || 0);
      card.dataset.lane = String(step.lane || 0);
      const saved = blockMap[step.step_id] || blockMap[step.block_id];
      const x = saved && typeof saved.x === "number" ? saved.x : ((step.column || 0) * columnWidth + 24);
      const y = saved && typeof saved.y === "number" ? saved.y : ((step.lane || 0) * laneHeight + 42);
      card.style.left = `${{x}}px`;
      card.style.top = `${{y}}px`;

      const header = document.createElement("div");
      header.className = "sequence-card-header";
      const titleWrap = document.createElement("div");
      appendTimelineText(titleWrap, "sequence-card-title", step.function || "unknown");
      appendTimelineText(titleWrap, "sequence-card-meta", `${{step.block_id}} · order ${{step.order_index}} · col ${{step.column}} / lane ${{step.lane}}`);
      header.appendChild(titleWrap);
      const badge = document.createElement("span");
      badge.className = "sequence-card-badge";
      badge.textContent = step.is_critical ? "critical" : `calls ${{(step.call_ids_as_caller || []).length + (step.call_ids_as_callee || []).length}}`;
      header.appendChild(badge);
      card.appendChild(header);
      renderSequenceChipRows(card, step);
      card.addEventListener("click", event => {{
        event.stopPropagation();
        updateSequenceDetails(state, step, activeModel);
      }});
      attachSequenceBlockDrag(card, board, activeModel, state);
      board.appendChild(card);
    }});

    shell.appendChild(board);
    container.appendChild(shell);
    applySequenceZoom(state);
    drawSequenceExecutionEdges(board, activeModel, state);
    requestAnimationFrame(() => drawSequenceExecutionEdges(board, activeModel, state));
  }}

  function updateSequenceDetails(state, step, model) {{
    const detailPanel = document.getElementById("detail-json");
    const signalById = makeIdMap(state.data.signals || [], "signal_id");
    const callById = makeIdMap((state.data.flow || {{}}).call_instances || [], "call_id");
    const signalIds = [].concat(step.incoming_signal_ids || [], step.outgoing_signal_ids || []);
    const callIds = [].concat(step.call_ids_as_caller || [], step.call_ids_as_callee || []);
    const relatedEdges = (model.edges || []).filter(edge => edge.source_block_id === step.block_id || edge.destination_block_id === step.block_id);
    detailPanel.textContent = JSON.stringify({{
      step,
      related_signals: signalIds.map(id => signalById.get(id)).filter(Boolean),
      related_calls: callIds.map(id => callById.get(id)).filter(Boolean),
      related_edges: relatedEdges
    }}, null, 2);
  }}

  function sequenceStageByBlock(model) {{
    const map = new Map();
    (model.steps || []).forEach(step => {{
      if (step && step.block_id && step.pipeline_stage !== undefined && step.pipeline_stage !== null) {{
        map.set(step.block_id, String(step.pipeline_stage));
      }}
    }});
    return map;
  }}

  function visibleSequenceEdgesForMode(model, state) {{
    const edges = Array.isArray(model.edges) ? model.edges : [];
    if (model.order_mode !== "pipeline") return edges;
    const densityMode = state.sequenceEdgeDensityMode || "all";
    if (densityMode === "all") return edges;
    const stageByBlock = sequenceStageByBlock(model);
    const selectedStage = String(state.sequenceStageFilter || "all");
    return edges.filter(edge => {{
      const sourceStage = stageByBlock.get(edge.source_block_id);
      const destinationStage = stageByBlock.get(edge.destination_block_id);
      if (!sourceStage || !destinationStage) return false;
      if (densityMode === "stage_local") return sourceStage === destinationStage;
      if (densityMode === "selected_stage") {{
        if (selectedStage === "all") return sourceStage === destinationStage;
        return sourceStage === selectedStage || destinationStage === selectedStage;
      }}
      return true;
    }});
  }}

  function shouldDrawSequenceEdge(edge, state, model) {{
    if (edge.kind === "exec") return state.sequenceToggles.showExecution;
    if (edge.kind === "control") return state.sequenceToggles.showControl;
    return state.sequenceToggles.showDataFlow;
  }}

  function sequenceCardRects(board, zoom) {{
    const boardRect = board.getBoundingClientRect();
    const rects = new Map();
    Array.from(board.querySelectorAll(".sequence-exec-card")).forEach(card => {{
      const rect = card.getBoundingClientRect();
      rects.set(card.dataset.blockId, {{
        x: (rect.left - boardRect.left) / zoom,
        y: (rect.top - boardRect.top) / zoom,
        w: rect.width / zoom,
        h: rect.height / zoom
      }});
    }});
    return rects;
  }}

  function drawSequenceExecutionEdges(board, model, state) {{
    const svg = board.querySelector(".sequence-edge-overlay");
    if (!svg) return;
    const width = Math.ceil(board.scrollWidth || board.clientWidth || 0);
    const height = Math.ceil(board.scrollHeight || board.clientHeight || 0);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.innerHTML = `
      <defs>
        <marker id="sequence-arrow-data" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" fill="#2563eb"></path></marker>
        <marker id="sequence-arrow-write" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" fill="#ea580c"></path></marker>
        <marker id="sequence-arrow-exec" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" fill="#64748b"></path></marker>
        <marker id="sequence-arrow-control" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" fill="#0ea5e9"></path></marker>
      </defs>`;
    const zoom = state.sequenceZoom || 1;
    const rects = sequenceCardRects(board, zoom);
    visibleSequenceEdgesForMode(model, state).forEach(edge => {{
      if (!shouldDrawSequenceEdge(edge, state, model)) return;
      const from = rects.get(edge.source_block_id);
      const to = rects.get(edge.destination_block_id);
      if (!from || !to) return;
      const x1 = from.x + from.w;
      const y1 = from.y + from.h / 2;
      const x2 = to.x;
      const y2 = to.y + to.h / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const classes = ["sequence-edge"];
      if (edge.kind === "exec") classes.push("sequence-edge-exec");
      else if (edge.kind === "control") classes.push("sequence-edge-control");
      else if (edge.role === "write") classes.push("sequence-edge-write");
      else classes.push("sequence-edge-data");
      if (edge.is_feedback) classes.push("sequence-edge-feedback");
      if (edge.is_critical && state.sequenceToggles.showCritical) classes.push("sequence-edge-critical");
      path.setAttribute("class", classes.join(" "));
      if (edge.is_feedback || x2 <= x1) {{
        const lift = Math.max(38, Math.abs(y2 - y1) / 2 + 32);
        path.setAttribute("d", `M${{x1}},${{y1}} C${{x1 + 52}},${{y1 - lift}} ${{x2 - 52}},${{y2 - lift}} ${{x2}},${{y2}}`);
      }} else {{
        const midX = (x1 + x2) / 2;
        path.setAttribute("d", `M${{x1}},${{y1}} C${{midX}},${{y1}} ${{midX}},${{y2}} ${{x2}},${{y2}}`);
      }}
      if (edge.label) {{
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = edge.label;
        path.appendChild(title);
      }}
      svg.appendChild(path);
      const visibleLabel = sequenceEdgeLabelForMode(edge, state);
      if (visibleLabel && edge.kind !== "exec") {{
        appendSequenceEndpointLabel(svg, visibleLabel, x1, y1, "source");
        appendSequenceEndpointLabel(svg, visibleLabel, x2, y2, "target");
      }}
    }});
  }}

  function compactSequenceEdgeLabel(label) {{
    const text = String(label || "");
    if (!text) return "";
    const parts = text.split(",").map(part => part.trim()).filter(Boolean);
    const compact = parts.length > 1 ? `${{parts[0]}} +${{parts.length - 1}}` : text;
    return compact.length > 22 ? `${{compact.slice(0, 19)}}...` : compact;
  }}

  function sequenceEdgeLabelForMode(edge, state) {{
    if (!edge || edge.kind === "exec") return "";
    const mode = state.sequenceLabelMode || "compact";
    if (mode === "off") return "";
    if (mode === "all") return edge.label || "";
    return compactSequenceEdgeLabel(edge.label || "");
  }}

  function appendSequenceEndpointLabel(svg, label, x, y, side) {{
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    const isSource = side === "source";
    text.setAttribute("class", isSource ? "sequence-edge-label source" : "sequence-edge-label target");
    text.setAttribute("x", String(isSource ? x + 8 : x - 8));
    text.setAttribute("y", String(y - 10));
    text.setAttribute("text-anchor", isSource ? "start" : "end");
    text.textContent = label;
    svg.appendChild(text);
  }}

  function getSequenceDragDelta(state, startPoint, nextPoint) {{
    const zoom = Math.max(0.1, state.sequenceZoom || 1);
    const deltaX = nextPoint.x - startPoint.x;
    const deltaY = nextPoint.y - startPoint.y;
    return {{
      x: deltaX / zoom,
      y: deltaY / zoom
    }};
  }}

  function attachSequenceBlockDrag(card, board, model, state) {{
    let dragging = false;
    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;
    let moved = false;

    function finishSequenceDrag(event) {{
      if (!dragging) return;
      dragging = false;
      if (pointerId != null && card.hasPointerCapture(pointerId)) card.releasePointerCapture(pointerId);
      pointerId = null;
      card.style.cursor = "grab";
      if (event) event.preventDefault();
    }}

    card.addEventListener("click", event => {{
      if (!moved) return;
      event.stopImmediatePropagation();
      event.preventDefault();
      moved = false;
    }}, true);
    card.addEventListener("pointerdown", event => {{
      if (event.button !== 0) return;
      dragging = true;
      moved = false;
      pointerId = event.pointerId;
      card.setPointerCapture(pointerId);
      startX = event.clientX;
      startY = event.clientY;
      originX = parseFloat(card.style.left || "0");
      originY = parseFloat(card.style.top || "0");
      card.style.cursor = "grabbing";
      event.preventDefault();
    }});
    card.addEventListener("pointermove", event => {{
      if (!dragging) return;
      const deltaX = event.clientX - startX;
      const deltaY = event.clientY - startY;
      if (Math.abs(deltaX) + Math.abs(deltaY) > 3) moved = true;
      const delta = getSequenceDragDelta(state, {{ x: startX, y: startY }}, {{ x: event.clientX, y: event.clientY }});
      const nextX = originX + delta.x;
      const nextY = originY + delta.y;
      card.style.left = `${{nextX}}px`;
      card.style.top = `${{nextY}}px`;
      const key = card.dataset.stepId || card.dataset.blockId;
      const mode = getSequenceOrderMode(state);
      const blockMap = getSequenceBlockMapForMode(state, mode);
      if (key) blockMap[key] = {{ x: nextX, y: nextY }};
      drawSequenceExecutionEdges(board, model, state);
    }});
    card.addEventListener("pointerup", finishSequenceDrag);
    card.addEventListener("pointercancel", finishSequenceDrag);
    card.addEventListener("lostpointercapture", () => {{
      dragging = false;
      pointerId = null;
      card.style.cursor = "grab";
    }});
  }}

  function redrawActiveSequenceEdges(state) {{
    const execBoard = document.querySelector("#sequence-content .sequence-exec-board");
    if (execBoard) {{
      const activeModel = getActiveSequenceModel(state, SEQUENCE_EXECUTION_MODEL);
      drawSequenceExecutionEdges(execBoard, activeModel, state);
      requestAnimationFrame(() => drawSequenceExecutionEdges(execBoard, activeModel, state));
      return;
    }}
    const legacyBoard = document.querySelector("#sequence-content .seq-board");
    if (legacyBoard) requestAnimationFrame(() => drawSequenceGroupEdges(legacyBoard, state.data));
  }}

  function renderSequenceTimelineV3(state, timeline, schemaVersion) {{
    const container = document.getElementById("sequence-content");
    const flow = state.data.flow || {{}};
    const callById = makeIdMap(flow.call_instances || [], "call_id");
    const signalById = makeIdMap(state.data.signals || [], "signal_id");
    const functionIOMap = getFunctionIOMap(state);

    container.innerHTML = "";
    const shell = document.createElement("div");
    shell.className = "timeline-shell";
    shell.dataset.schemaVersion = schemaVersion;

    const banner = document.createElement("div");
    banner.className = "timeline-banner";
    const metaKind = flow.execution_order_meta && flow.execution_order_meta.kind ? flow.execution_order_meta.kind : "static_block_order";
    banner.textContent = `Schema ${{schemaVersion}} sequence_timeline: one card per execution_order block. Order kind: ${{metaKind}}. v2 call_sequence remains available as a fallback.`;
    shell.appendChild(banner);

    timeline.forEach(step => {{
      const card = document.createElement("article");
      card.className = "timeline-card";
      card.dataset.stepId = step.step_id || "";
      card.dataset.blockId = step.block_id || "";
      card.dataset.function = step.function || "";

      const header = document.createElement("div");
      header.className = "timeline-card-header";
      const titleWrap = document.createElement("div");
      appendTimelineText(titleWrap, "timeline-title", `${{step.order_index}}. ${{step.function || "unknown"}}`);
      appendTimelineText(titleWrap, "timeline-subtitle", `${{step.block_id || "unknown block"}} · ${{step.step_id || "no step_id"}}`);
      header.appendChild(titleWrap);

      const badges = document.createElement("div");
      badges.className = "timeline-badges";
      appendTimelinePill(badges, `caller calls: ${{(step.call_ids_as_caller || []).length}}`);
      appendTimelinePill(badges, `callee calls: ${{(step.call_ids_as_callee || []).length}}`);
      appendTimelinePill(badges, `incoming signal_id: ${{(step.incoming_signal_ids || []).length}}`);
      appendTimelinePill(badges, `outgoing signal_id: ${{(step.outgoing_signal_ids || []).length}}`);
      header.appendChild(badges);
      card.appendChild(header);

      const grid = document.createElement("div");
      grid.className = "timeline-grid";

      renderTimelineList(grid, "Call IDs as caller", step.call_ids_as_caller || [], (li, callId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeCall(callById.get(callId));
      }});
      renderTimelineList(grid, "Call IDs as callee", step.call_ids_as_callee || [], (li, callId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeCall(callById.get(callId));
      }});
      renderTimelineList(grid, "Incoming signal_id", step.incoming_signal_ids || [], (li, signalId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeSignal(signalById.get(signalId));
      }});
      renderTimelineList(grid, "Outgoing signal_id", step.outgoing_signal_ids || [], (li, signalId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeSignal(signalById.get(signalId));
      }});
      renderTimelineList(
        grid,
        "Function IO reads/writes",
        summarizeTimelineFunctionIO(state, step, functionIOMap, callById),
        (li, item) => {{
          li.classList.add("timeline-mono");
          li.textContent = describeTimelineIO(item);
        }}
      );

      card.appendChild(grid);
      shell.appendChild(card);
    }});

    container.appendChild(shell);
    applySequenceZoom(state);
  }}

  function renderLegacySequence(state, schemaVersion) {{
    const container = document.getElementById("sequence-content");
    const seq = (state.data.flow && state.data.flow.call_sequence) ? state.data.flow.call_sequence : [];
    if (!seq.length) {{
      container.textContent = "No call sequence data available.";
      return;
    }}

    container.innerHTML = "";
    const board = document.createElement("div");
    board.className = "seq-board";

    const groupByName = new Map();
    seq.forEach(group => groupByName.set(group.function, group));

    const order = buildFunctionOrder(state.data, seq);
    order.forEach(name => {{
      const group = groupByName.get(name);
      if (!group) return;
      const calls = group.calls || [];
      const groupEl = document.createElement("div");
      groupEl.className = "seq-group";
      groupEl.dataset.function = group.function;
      applyGroupTransform(groupEl, state, group.function);
      const title = document.createElement("div");
      title.className = "seq-title";
      title.textContent = group.function;
      groupEl.appendChild(title);

      const canvas = document.createElement("div");
      canvas.className = "seq-canvas";
      canvas.dataset.function = group.function;

      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "seq-svg");
      svg.innerHTML = `
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#9ca3af"></path>
          </marker>
        </defs>
      `;
      canvas.appendChild(svg);

      const layout = buildSequenceLayout(group.function, calls, getFunctionIOMap(state));
      const nodes = layout.nodes;
      const edges = layout.edges;
      canvas._seqEdges = edges;

      // Compute canvas size
      const width = Math.max(220, (layout.maxLayer + 1) * 150 + 40);
      const height = Math.max(64, (layout.maxRow + 1) * 58 + 28);
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));

      nodes.forEach(node => {{
        const el = document.createElement("div");
        el.className = "seq-node";
        el.dataset.nodeId = node.id;
        el.dataset.function = group.function;
        el.textContent = node.label;
        el.style.left = node.x + "px";
        el.style.top = node.y + "px";
        attachDrag(el, svg, edges, state);
        canvas.appendChild(el);
      }});

      drawSequenceEdges(svg, edges);
      groupEl.appendChild(canvas);
      board.appendChild(groupEl);
      attachGroupDrag(groupEl, board, state);
    }});

    // Append any remaining functions not covered by the call graph order
    seq.forEach(group => {{
      if (order.indexOf(group.function) !== -1) return;
      const calls = group.calls || [];
      const groupEl = document.createElement("div");
      groupEl.className = "seq-group";
      groupEl.dataset.function = group.function;
      applyGroupTransform(groupEl, state, group.function);
      const title = document.createElement("div");
      title.className = "seq-title";
      title.textContent = group.function;
      groupEl.appendChild(title);

      const canvas = document.createElement("div");
      canvas.className = "seq-canvas";
      canvas.dataset.function = group.function;

      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "seq-svg");
      svg.innerHTML = `
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#9ca3af"></path>
          </marker>
        </defs>
      `;
      canvas.appendChild(svg);

      const layout = buildSequenceLayout(group.function, calls, getFunctionIOMap(state));
      const nodes = layout.nodes;
      const edges = layout.edges;
      canvas._seqEdges = edges;

      const width = Math.max(220, (layout.maxLayer + 1) * 150 + 40);
      const height = Math.max(64, (layout.maxRow + 1) * 58 + 28);
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));

      nodes.forEach(node => {{
        const el = document.createElement("div");
        el.className = "seq-node";
        el.dataset.nodeId = node.id;
        el.dataset.function = group.function;
        el.textContent = node.label;
        el.style.left = node.x + "px";
        el.style.top = node.y + "px";
        attachDrag(el, svg, edges, state);
        canvas.appendChild(el);
      }});

      drawSequenceEdges(svg, edges);
      groupEl.appendChild(canvas);
      board.appendChild(groupEl);
      attachGroupDrag(groupEl, board, state);
    }});

    container.appendChild(board);
    applySequenceZoom(state);
    requestAnimationFrame(() => {{
      board.querySelectorAll(".seq-svg").forEach(svg => {{
        const canvas = svg.parentElement;
        const edges = canvas && canvas._seqEdges ? canvas._seqEdges : [];
        drawSequenceEdges(svg, edges);
      }});
      drawSequenceGroupEdges(board, state.data);
    }});
  }}

  function buildFunctionOrder(data, seq) {{
    const order = [];
    const visited = new Set();
    const callGraph = data.flow && data.flow.call_graph ? data.flow.call_graph : null;
    const entry = callGraph && callGraph.entry_functions ? callGraph.entry_functions : [];
    const seqMap = new Map();
    seq.forEach(group => seqMap.set(group.function, group));

    function dfs(funcName) {{
      if (visited.has(funcName)) return;
      visited.add(funcName);
      order.push(funcName);
      const group = seqMap.get(funcName);
      if (!group || !group.calls) return;
      group.calls.forEach(call => dfs(call.callee));
    }}

    entry.forEach(name => dfs(name));
    seq.forEach(group => {{
      if (!visited.has(group.function)) {{
        order.push(group.function);
        visited.add(group.function);
      }}
    }});
    return order;
  }}

  function drawSequenceGroupEdges(board, data) {{
    const old = board.querySelector(".seq-board-overlay");
    if (old) old.remove();

    const groupEls = Array.from(board.querySelectorAll(".seq-group"));
    if (!groupEls.length) return;

    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("class", "seq-board-overlay");
    overlay.setAttribute("width", String(Math.ceil(board.scrollWidth || board.clientWidth || 0)));
    overlay.setAttribute("height", String(Math.ceil(board.scrollHeight || board.clientHeight || 0)));
    overlay.innerHTML = `
      <defs>
        <marker id="group-arrowhead" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
          <path d="M0,0 L8,3 L0,6 Z" fill="#94a3b8"></path>
        </marker>
      </defs>
    `;
    board.insertBefore(overlay, board.firstChild);

    const boardRect = board.getBoundingClientRect();
    const groupMap = new Map();
    groupEls.forEach(el => {{
      const fn = el.dataset.function;
      if (!fn) return;
      groupMap.set(fn, el);
    }});

    const callGraph = data && data.flow ? data.flow.call_graph : null;
    const drawn = new Set();
    if (callGraph && callGraph.nodes) {{
      Object.keys(callGraph.nodes).forEach(caller => {{
        const node = callGraph.nodes[caller];
        const callerEl = groupMap.get(caller);
        if (!callerEl || !node || !Array.isArray(node.callees)) return;
        node.callees.forEach(callee => {{
          const calleeEl = groupMap.get(callee);
          if (!calleeEl) return;
          const key = `${{caller}}->${{callee}}`;
          if (drawn.has(key)) return;
          drawn.add(key);
          appendGroupEdgePath(overlay, boardRect, callerEl, calleeEl);
        }});
      }});
    }}

    // Fallback: connect neighbors if call graph data is absent.
    if (!drawn.size && groupEls.length > 1) {{
      for (let i = 0; i < groupEls.length - 1; i += 1) {{
        appendGroupEdgePath(overlay, boardRect, groupEls[i], groupEls[i + 1]);
      }}
    }}
  }}

  function appendGroupEdgePath(svg, boardRect, fromEl, toEl) {{
    const zoom = state.sequenceZoom || 1;
    const fromRect = fromEl.getBoundingClientRect();
    const toRect = toEl.getBoundingClientRect();
    const x1 = ((fromRect.right - boardRect.left) / zoom);
    const y1 = (((fromRect.top + fromRect.height / 2) - boardRect.top) / zoom);
    const x2 = ((toRect.left - boardRect.left) / zoom);
    const y2 = (((toRect.top + toRect.height / 2) - boardRect.top) / zoom);
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", "seq-group-edge");
    path.setAttribute("d", `M${{x1}},${{y1}} C${{x1 + dx}},${{y1}} ${{x2 - dx}},${{y2}} ${{x2}},${{y2}}`);
    svg.appendChild(path);
  }}

  function applyGroupTransform(groupEl, state, functionName) {{
    const saved = state.sequenceGroupMap && state.sequenceGroupMap[functionName];
    const tx = saved && typeof saved.x === "number" ? saved.x : 0;
    const ty = saved && typeof saved.y === "number" ? saved.y : 0;
    groupEl.dataset.tx = String(tx);
    groupEl.dataset.ty = String(ty);
    groupEl.style.transform = `translate(${{tx}}px, ${{ty}}px)`;
    groupEl.style.cursor = "grab";
  }}

  function buildSequenceLayout(functionName, calls, functionIO) {{
    const nodes = [];
    const edges = [];

    const assignInfo = calls.map((call, index) => {{
      return {{
        index,
        callee: call.callee,
        args: call.args || [],
        assigned: call.assigned || null,
        calleeParams: call.callee_params || []
      }};
    }});

    const deps = new Map();
    assignInfo.forEach(call => deps.set(call.index, []));

    const keywords = new Set(["if", "for", "while", "return", "sizeof", "int", "float", "double", "char", "void"]);
    const extractIdentifiers = (text) => {{
      if (!text) return [];
      const matches = text.match(/[A-Za-z_]\\w*/g) || [];
      return matches.filter(token => !keywords.has(token));
    }};
    const toSet = (items) => new Set(items);
    const mergeSets = (a, b) => new Set([...a, ...b]);

    const ioFor = (name) => (functionIO && functionIO[name]) ? functionIO[name] : null;
    const readWriteForCall = (call) => {{
      const io = ioFor(call.callee);
      let reads = [];
      let writes = [];
      if (io && Array.isArray(io.reads) && Array.isArray(io.writes)) {{
        io.reads.forEach(param => {{
          const idx = call.calleeParams.indexOf(param);
          if (idx >= 0 && call.args[idx] != null) {{
            reads = reads.concat(extractIdentifiers(call.args[idx]));
          }}
        }});
        io.writes.forEach(param => {{
          const idx = call.calleeParams.indexOf(param);
          if (idx >= 0 && call.args[idx] != null) {{
            writes = writes.concat(extractIdentifiers(call.args[idx]));
          }}
        }});
      }} else {{
        call.args.forEach(arg => {{
          reads = reads.concat(extractIdentifiers(arg));
        }});
      }}
      if (call.assigned) {{
        writes = writes.concat(extractIdentifiers(call.assigned));
      }}
      return {{ readSet: toSet(reads), writeSet: toSet(writes) }};
    }};

    for (let i = 0; i < assignInfo.length; i += 1) {{
      const src = assignInfo[i];
      for (let j = i + 1; j < assignInfo.length; j += 1) {{
        const dst = assignInfo[j];
        const srcRW = readWriteForCall(src);
        const dstRW = readWriteForCall(dst);
        const raw = intersects(srcRW.writeSet, mergeSets(dstRW.readSet, dstRW.writeSet));
        const war = intersects(srcRW.readSet, dstRW.writeSet);
        if (raw || war) {{
          deps.get(dst.index).push(src.index);
        }}
      }}
    }}

    const layerByIndex = new Map();
    let maxLayer = 0;
    assignInfo.forEach(call => {{
      const incoming = deps.get(call.index) || [];
      let layer = 0;
      if (incoming.length) {{
        layer = Math.max(...incoming.map(idx => layerByIndex.get(idx) || 0)) + 1;
      }}
      layerByIndex.set(call.index, layer);
      maxLayer = Math.max(maxLayer, layer);
    }});

    const rowByLayer = new Map();
    let maxRow = 0;
    assignInfo.forEach(call => {{
      const layer = layerByIndex.get(call.index) || 0;
      const row = rowByLayer.get(layer) || 0;
      rowByLayer.set(layer, row + 1);
      maxRow = Math.max(maxRow, row);

      const id = `${{functionName}}::${{call.index}}::${{call.callee}}`;
      const mapKey = `${{functionName}}::${{id}}`;
      const saved = state.sequenceMap[mapKey];
      const x = saved && typeof saved.x === "number" ? saved.x : (layer * 150 + 26);
      const y = saved && typeof saved.y === "number" ? saved.y : (row * 58 + 18);

      nodes.push({{
        id,
        label: call.callee,
        index: call.index,
        layer,
        row,
        x,
        y
      }});
    }});

    assignInfo.forEach(call => {{
      const incoming = deps.get(call.index) || [];
      incoming.forEach(srcIdx => {{
        edges.push({{
          from: `${{functionName}}::${{srcIdx}}::${{assignInfo[srcIdx].callee}}`,
          to: `${{functionName}}::${{call.index}}::${{call.callee}}`
        }});
      }});
    }});

    return {{
      nodes,
      edges,
      maxLayer,
      maxRow
    }};
  }}

  function intersects(a, b) {{
    for (const item of a) {{
      if (b.has(item)) return true;
    }}
    return false;
  }}

  function drawSequenceEdges(svg, edges) {{
    const canvas = svg.parentElement;
    if (!canvas) return;
    const zoom = state.sequenceZoom || 1;
    const svgRect = svg.getBoundingClientRect();
    const nodeEls = Array.from(canvas.querySelectorAll(".seq-node"));
    const nodeMap = new Map();
    nodeEls.forEach(el => {{
      const r = el.getBoundingClientRect();
      nodeMap.set(el.dataset.nodeId, {{
        x: (r.left - svgRect.left) / zoom,
        y: (r.top - svgRect.top) / zoom,
        w: r.width / zoom,
        h: r.height / zoom
      }});
    }});
    const existing = svg.querySelectorAll("path.seq-edge");
    existing.forEach(el => el.remove());

    edges.forEach(edge => {{
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (!from || !to) return;
      const x1 = from.x + from.w;
      const y1 = from.y + from.h / 2;
      const x2 = to.x;
      const y2 = to.y + to.h / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const midX = (x1 + x2) / 2;
      path.setAttribute("d", `M${{x1}},${{y1}} C${{midX}},${{y1}} ${{midX}},${{y2}} ${{x2}},${{y2}}`);
      path.setAttribute("class", "seq-edge");
      svg.appendChild(path);
    }});
  }}

  function attachDrag(nodeEl, svg, edges, state) {{
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;

    nodeEl.addEventListener("pointerdown", event => {{
      dragging = true;
      nodeEl.setPointerCapture(event.pointerId);
      startX = event.clientX;
      startY = event.clientY;
      originX = parseFloat(nodeEl.style.left || "0");
      originY = parseFloat(nodeEl.style.top || "0");
      nodeEl.style.cursor = "grabbing";
    }});

    nodeEl.addEventListener("pointermove", event => {{
      if (!dragging) return;
      const delta = getSequenceDragDelta(state, {{ x: startX, y: startY }}, {{ x: event.clientX, y: event.clientY }});
      const nextX = originX + delta.x;
      const nextY = originY + delta.y;
      nodeEl.style.left = `${{nextX}}px`;
      nodeEl.style.top = `${{nextY}}px`;

      const key = `${{nodeEl.dataset.function}}::${{nodeEl.dataset.nodeId}}`;
      state.sequenceMap[key] = {{ x: nextX, y: nextY }};

      drawSequenceEdges(svg, edges);
    }});

    nodeEl.addEventListener("pointerup", event => {{
      dragging = false;
      nodeEl.releasePointerCapture(event.pointerId);
      nodeEl.style.cursor = "grab";
    }});
  }}

  function attachGroupDrag(groupEl, board, state) {{
    let dragging = false;
    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;

    groupEl.addEventListener("pointerdown", event => {{
      if (event.button !== 0) return;
      if (event.target.closest(".seq-node")) return;
      dragging = true;
      pointerId = event.pointerId;
      groupEl.setPointerCapture(pointerId);
      startX = event.clientX;
      startY = event.clientY;
      originX = parseFloat(groupEl.dataset.tx || "0");
      originY = parseFloat(groupEl.dataset.ty || "0");
      groupEl.style.cursor = "grabbing";
    }});

    groupEl.addEventListener("pointermove", event => {{
      if (!dragging) return;
      const delta = getSequenceDragDelta(state, {{ x: startX, y: startY }}, {{ x: event.clientX, y: event.clientY }});
      const tx = originX + delta.x;
      const ty = originY + delta.y;
      groupEl.dataset.tx = String(tx);
      groupEl.dataset.ty = String(ty);
      groupEl.style.transform = `translate(${{tx}}px, ${{ty}}px)`;
      const fn = groupEl.dataset.function;
      if (fn) state.sequenceGroupMap[fn] = {{ x: tx, y: ty }};
      drawSequenceGroupEdges(board, state.data);
    }});

    groupEl.addEventListener("pointerup", event => {{
      if (!dragging) return;
      dragging = false;
      if (pointerId != null) groupEl.releasePointerCapture(pointerId);
      pointerId = null;
      groupEl.style.cursor = "grab";
    }});
  }}

  if (typeof ELK === "undefined") {{
    document.getElementById("detail-json").textContent = "ELK.js bundle not loaded. Place ./assets/elk.bundled.js next to this HTML file.";
  }} else {{
    init();
  }}
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert CVAS JSON to a standalone HTML viewer.")
    parser.add_argument("input_json", help="Path to input JSON file")
    parser.add_argument("output_html", help="Path to output HTML file")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_html)

    data = load_json(input_path)
    html = build_html(data)

    try:
        output_path.write_text(html, encoding="utf-8")
    except OSError as exc:
        print(f"[json_to_html] Failed to write output HTML: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
