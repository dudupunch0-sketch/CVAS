"""Viewer compatibility tests for v2 fallback and v3 timeline rendering hooks."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from json_to_html import (  # noqa: E402
    build_sequence_execution_model,
    build_html,
    get_viewer_function_io_map,
    select_sequence_renderer,
    summarize_timeline_function_io,
)


def minimal_v2_model() -> dict:
    return {
        "blocks": [
            {"block_id": "B1", "block_name": "helper", "inputs": ["x"], "outputs": ["return"], "estimated_cycles": 1},
            {"block_id": "B2", "block_name": "top", "inputs": ["a"], "outputs": ["return"], "estimated_cycles": 1},
        ],
        "operations": [],
        "signals": [
            {
                "source_id": "B2",
                "source_type": "block",
                "destination_id": "B1",
                "destination_type": "block",
                "signal_name": "a",
                "direction": "in",
                "comment": "argument flow",
            }
        ],
        "flow": {
            "execution_order": ["B1", "B2"],
            "parallelism": "sequential",
            "call_sequence": [
                {
                    "function": "top",
                    "calls": [
                        {"callee": "helper", "args": ["a"], "assigned": "out", "callee_params": ["x"]}
                    ],
                }
            ],
            "call_graph": {
                "nodes": {
                    "top": {"block_id": "B2", "callees": ["helper"]},
                    "helper": {"block_id": "B1", "callees": []},
                },
                "entry_functions": ["top"],
            },
        },
    }


def minimal_v3_parallel_model() -> dict:
    signals = [
        {
            "signal_id": "S_B1_B3",
            "source_id": "B1",
            "source_type": "block",
            "destination_id": "B3",
            "destination_type": "block",
            "signal_name": "a",
            "direction": "out",
            "kind": "internal_copy",
            "role": "read",
        },
        {
            "signal_id": "S_B2_B4",
            "source_id": "B2",
            "source_type": "block",
            "destination_id": "B4",
            "destination_type": "block",
            "signal_name": "b",
            "direction": "out",
            "kind": "internal_copy",
            "role": "write",
        },
        {
            "signal_id": "S_B3_B4",
            "source_id": "B3",
            "source_type": "block",
            "destination_id": "B4",
            "destination_type": "block",
            "signal_name": "c",
            "direction": "out",
            "kind": "call_return",
            "role": "write",
        },
    ]
    timeline = [
        {
            "step_id": f"T_000{index}_{block_id}",
            "order_index": index,
            "block_id": block_id,
            "function": function,
            "call_ids_as_caller": [],
            "call_ids_as_callee": [],
            "incoming_signal_ids": [],
            "outgoing_signal_ids": [],
            "read_write_summary": {
                "reads_from_other": [],
                "read_by_other": [],
                "writes_to_other": [],
                "written_by_other": [],
            },
        }
        for index, (block_id, function) in enumerate(
            [("B1", "load_a"), ("B2", "load_b"), ("B3", "compute_c"), ("B4", "store")]
        )
    ]
    return {
        "schema_version": "3.0",
        "blocks": [
            {"block_id": "B1", "block_name": "load_a", "inputs": [], "outputs": ["a"], "estimated_cycles": 1},
            {"block_id": "B2", "block_name": "load_b", "inputs": [], "outputs": ["b"], "estimated_cycles": 1},
            {"block_id": "B3", "block_name": "compute_c", "inputs": ["a"], "outputs": ["c"], "estimated_cycles": 1},
            {"block_id": "B4", "block_name": "store", "inputs": ["b", "c"], "outputs": [], "estimated_cycles": 1},
        ],
        "operations": [],
        "signals": signals,
        "flow": {
            "execution_order": ["B1", "B2", "B3", "B4"],
            "execution_order_meta": {"kind": "static_block_order"},
            "parallelism": "sequential",
            "call_instances": [],
            "sequence_timeline": timeline,
            "dependencies": {
                "inter_block": [
                    {
                        "signal_id": item["signal_id"],
                        "source_id": item["source_id"],
                        "destination_id": item["destination_id"],
                        "kind": item["kind"],
                        "role": item["role"],
                    }
                    for item in signals
                ]
            },
        },
    }


def test_viewer_accepts_v2_without_schema_version() -> None:
    html = build_html(minimal_v2_model())

    assert "detectSchemaVersion" in html
    assert "renderLegacySequence" in html
    assert "No call sequence data available" in html
    assert '"schema_version"' not in json.dumps(minimal_v2_model())


def test_sequence_renderer_selection_prefers_v3_and_falls_back_to_legacy() -> None:
    assert select_sequence_renderer(minimal_v2_model()) == "legacy"

    fixture = REPO_ROOT / "tests" / "fixtures" / "schema" / "sequence_timeline_v3.expected.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert select_sequence_renderer(data) == "v3_timeline"

    without_timeline = copy.deepcopy(data)
    without_timeline["flow"]["sequence_timeline"] = []
    assert select_sequence_renderer(without_timeline) == "legacy"


def test_sequence_execution_model_layers_independent_blocks() -> None:
    model = build_sequence_execution_model(minimal_v3_parallel_model())
    steps = {step["block_id"]: step for step in model["steps"]}

    assert steps["B1"]["column"] == steps["B2"]["column"]
    assert steps["B1"]["lane"] != steps["B2"]["lane"]
    assert steps["B3"]["column"] > steps["B1"]["column"]
    assert steps["B4"]["column"] > steps["B3"]["column"]
    assert any(step["is_critical"] for step in model["steps"])


def test_sequence_execution_model_uses_dependencies_without_duplicate_edges() -> None:
    model = build_sequence_execution_model(minimal_v3_parallel_model())
    data_edges = [edge for edge in model["edges"] if edge["kind"] == "data"]
    data_signal_ids = [signal_id for edge in data_edges for signal_id in edge["signal_ids"]]

    assert data_signal_ids.count("S_B1_B3") == 1
    assert data_signal_ids.count("S_B2_B4") == 1
    assert data_signal_ids.count("S_B3_B4") == 1
    assert any(edge["label"] == "a" and edge["role"] == "read" for edge in data_edges)


def test_viewer_function_io_map_prefers_flow_then_state_fallback() -> None:
    state_io = {"helper": {"reads": ["state"], "writes": []}}
    assert get_viewer_function_io_map(minimal_v2_model(), state_io)["helper"]["reads"] == ["state"]

    model = minimal_v2_model()
    model["flow"]["function_io"] = {
        "source": "test",
        "functions": {"helper": {"reads": ["flow"], "writes": []}},
    }
    assert get_viewer_function_io_map(model, state_io)["helper"]["reads"] == ["flow"]


def test_v3_timeline_io_summary_uses_embedded_function_io() -> None:
    fixture = REPO_ROOT / "tests" / "fixtures" / "schema" / "sequence_timeline_v3.expected.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data["flow"]["function_io"] = {
        "source": "test",
        "functions": {
            "inc": {"reads": [], "writes": ["x"]},
            "top": {"reads": ["a"], "writes": ["return"]},
        },
    }

    top_step = next(step for step in data["flow"]["sequence_timeline"] if step["function"] == "top")
    summary = summarize_timeline_function_io(data, top_step)
    called = [item for item in summary if item["role"] == "called_function"]

    assert [item["call_id"] for item in called] == ["C_B2_0001", "C_B2_0002"]
    assert [item["function"] for item in called] == ["inc", "inc"]
    assert [item["writes"] for item in called] == [["a"], ["first"]]
    assert [item["reads"] for item in called] == [[], []]


def test_viewer_prefers_v3_sequence_timeline_when_present() -> None:
    fixture = REPO_ROOT / "tests" / "fixtures" / "schema" / "sequence_timeline_v3.expected.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    html = build_html(data)

    assert "renderSequenceExecutionDiagramV3" in html
    assert "sequence-exec-board" in html
    assert "sequence-exec-card" in html
    assert "sequence-edge-data" in html
    assert "sequence-edge-exec" in html
    assert "sequence_timeline" in html


def test_viewer_sequence_map_controls_support_v3_and_legacy_maps() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "serializeSequenceMap" in html
    assert "loadSequenceMapPayload" in html
    assert "sequence_block_positions" in html
    assert "sequenceBlockMap" in html
    assert "Reset Sequence Layout" in html
    assert "pointercancel" in html
    assert "finishSequenceDrag" in html


def test_viewer_details_panel_can_be_collapsed() -> None:
    html = build_html(minimal_v2_model())

    assert "detailToggle" in html
    assert "details-collapsed" in html
    assert "toggleDetailsPanel" in html


def test_viewer_html_contains_v3_detail_fields() -> None:
    html = build_html(minimal_v2_model())

    assert "call_id" in html
    assert "signal_id" in html
    assert "schemaVersion" in html
