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


def minimal_v3_call_order_model() -> dict:
    """Return a fixture where call order and dependency order intentionally differ."""

    signals = [
        {
            "signal_id": "S_HELPER_TOP_RET",
            "source_id": "B_HELPER",
            "source_type": "block",
            "destination_id": "B_TOP",
            "destination_type": "block",
            "signal_name": "helper_result",
            "direction": "out",
            "kind": "call_return",
            "role": "write",
            "call_id": "C_TOP_0001",
        }
    ]
    timeline = [
        {
            "step_id": "T_0000_B_HELPER",
            "order_index": 0,
            "block_id": "B_HELPER",
            "function": "bpc_inner_op",
            "call_ids_as_caller": [],
            "call_ids_as_callee": ["C_TOP_0001"],
            "incoming_signal_ids": [],
            "outgoing_signal_ids": ["S_HELPER_TOP_RET"],
            "read_write_summary": {
                "reads_from_other": [],
                "read_by_other": [],
                "writes_to_other": [{"signal_name": "helper_result"}],
                "written_by_other": [],
            },
        },
        {
            "step_id": "T_0001_B_TOP",
            "order_index": 1,
            "block_id": "B_TOP",
            "function": "simple_bpc_frame",
            "call_ids_as_caller": ["C_TOP_0001"],
            "call_ids_as_callee": [],
            "incoming_signal_ids": ["S_HELPER_TOP_RET"],
            "outgoing_signal_ids": [],
            "read_write_summary": {
                "reads_from_other": [{"signal_name": "helper_result"}],
                "read_by_other": [],
                "writes_to_other": [],
                "written_by_other": [],
            },
        },
    ]
    return {
        "schema_version": "3.0",
        "blocks": [
            {
                "block_id": "B_TOP",
                "block_name": "simple_bpc_frame",
                "inputs": ["frame"],
                "outputs": ["return"],
                "estimated_cycles": 3,
            },
            {
                "block_id": "B_HELPER",
                "block_name": "bpc_inner_op",
                "inputs": ["frame"],
                "outputs": ["helper_result"],
                "estimated_cycles": 1,
            },
        ],
        "operations": [],
        "signals": signals,
        "flow": {
            "execution_order": ["B_HELPER", "B_TOP"],
            "execution_order_meta": {"kind": "static_block_order"},
            "parallelism": "sequential",
            "call_graph": {
                "nodes": {
                    "simple_bpc_frame": {
                        "function_name": "simple_bpc_frame",
                        "block_id": "B_TOP",
                        "callers": [],
                        "callees": ["bpc_inner_op"],
                    },
                    "bpc_inner_op": {
                        "function_name": "bpc_inner_op",
                        "block_id": "B_HELPER",
                        "callers": ["simple_bpc_frame"],
                        "callees": [],
                    },
                },
                "entry_functions": ["simple_bpc_frame"],
                "critical_path": ["simple_bpc_frame", "bpc_inner_op"],
            },
            "call_instances": [
                {
                    "call_id": "C_TOP_0001",
                    "caller_block_id": "B_TOP",
                    "caller_function": "simple_bpc_frame",
                    "callee_block_id": "B_HELPER",
                    "callee_function": "bpc_inner_op",
                    "ordinal_in_caller": 1,
                    "args": [],
                    "assigned": {"target": "helper_result", "signal_id": "S_HELPER_TOP_RET"},
                }
            ],
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


def minimal_v3_explicit_pipeline_model() -> dict:
    data = minimal_v3_parallel_model()
    data["blocks"] = [
        {"block_id": "B_ENTRY", "block_name": "top", "inputs": [], "outputs": ["return"], "estimated_cycles": 1},
        {"block_id": "B_FETCH", "block_name": "fetch_pixel", "inputs": [], "outputs": ["raw"], "estimated_cycles": 1},
        {"block_id": "B_FILTER", "block_name": "filter_pixel", "inputs": ["raw"], "outputs": ["score"], "estimated_cycles": 1},
        {"block_id": "B_JOIN", "block_name": "combine_pixel", "inputs": ["raw", "score"], "outputs": ["pixel"], "estimated_cycles": 1},
        {"block_id": "B_STORE", "block_name": "store_pixel", "inputs": ["pixel"], "outputs": [], "estimated_cycles": 1},
    ]
    data["flow"]["execution_order"] = ["B_ENTRY", "B_FETCH", "B_FILTER", "B_JOIN", "B_STORE"]
    data["flow"]["sequence_timeline"] = [
        {
            "step_id": f"T_{index:04d}_{block['block_id']}",
            "order_index": index,
            "block_id": block["block_id"],
            "function": block["block_name"],
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
        for index, block in enumerate(data["blocks"])
    ]
    data["signals"] = [
        {
            "signal_id": "S_FETCH_FILTER",
            "source_id": "B_FETCH",
            "source_type": "block",
            "destination_id": "B_FILTER",
            "destination_type": "block",
            "signal_name": "raw",
            "direction": "out",
            "kind": "call_return",
            "role": "write",
        },
        {
            "signal_id": "S_FILTER_JOIN",
            "source_id": "B_FILTER",
            "source_type": "block",
            "destination_id": "B_JOIN",
            "destination_type": "block",
            "signal_name": "score",
            "direction": "out",
            "kind": "call_return",
            "role": "write",
        },
        {
            "signal_id": "S_JOIN_STORE",
            "source_id": "B_JOIN",
            "source_type": "block",
            "destination_id": "B_STORE",
            "destination_type": "block",
            "signal_name": "pixel",
            "direction": "out",
            "kind": "call_return",
            "role": "write",
        },
    ]
    data["flow"]["dependencies"] = {
        "inter_block": [
            {
                "signal_id": signal["signal_id"],
                "source_id": signal["source_id"],
                "destination_id": signal["destination_id"],
                "kind": signal["kind"],
                "role": signal["role"],
            }
            for signal in data["signals"]
        ]
    }
    data["flow"]["pipeline_stages"] = {
        "source": "test-annotation",
        "items": [
            {"block_id": "B_FETCH", "stage": 2, "stage_label": "Predict", "lane_role": "lane"},
            {"function": "filter_pixel", "stage": 2, "stage_label": "Predict", "lane_role": "lane"},
            {"block_id": "B_JOIN", "stage": 2, "stage_label": "Predict", "lane_role": "join"},
            {"block_id": "B_STORE", "stage": 4, "stage_label": "Output", "lane_role": "final"},
        ],
    }
    return data


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


def test_sequence_execution_model_defaults_to_call_order_root_left() -> None:
    model = build_sequence_execution_model(minimal_v3_call_order_model())
    call_layout = model["layouts"]["call"]
    dependency_layout = model["layouts"]["dependency"]
    pipeline_layout = model["layouts"]["pipeline"]
    call_steps = {step["block_id"]: step for step in call_layout["steps"]}
    dependency_steps = {step["block_id"]: step for step in dependency_layout["steps"]}

    assert model["schema"] == "sequence-execution-diagram-v2"
    assert model["default_order_mode"] == "call"
    assert model["steps"] == call_layout["steps"]
    assert call_layout["order_kind"] == "root_call_layout"
    assert dependency_layout["order_kind"] == "static_dependency_layout"
    assert pipeline_layout["order_kind"] == "pipeline_stage_layout"
    assert call_steps["B_TOP"]["column"] < call_steps["B_HELPER"]["column"]
    assert dependency_steps["B_HELPER"]["column"] < dependency_steps["B_TOP"]["column"]


def test_sequence_execution_model_layers_independent_blocks() -> None:
    model = build_sequence_execution_model(minimal_v3_parallel_model())
    dependency_layout = model["layouts"]["dependency"]
    pipeline_layout = model["layouts"]["pipeline"]
    steps = {step["block_id"]: step for step in dependency_layout["steps"]}
    pipeline_steps = {step["block_id"]: step for step in pipeline_layout["steps"]}

    assert steps["B1"]["column"] == steps["B2"]["column"]
    assert steps["B1"]["lane"] != steps["B2"]["lane"]
    assert steps["B3"]["column"] > steps["B1"]["column"]
    assert steps["B4"]["column"] > steps["B3"]["column"]
    assert any(step["is_critical"] for step in dependency_layout["steps"])
    assert pipeline_layout["column_labels"] == ["P0", "P1", "P2"]
    assert pipeline_steps["B1"]["column"] == pipeline_steps["B2"]["column"]
    assert pipeline_steps["B3"]["column"] > pipeline_steps["B1"]["column"]
    assert pipeline_steps["B4"]["column"] > pipeline_steps["B3"]["column"]


def test_sequence_execution_model_exposes_layout_mode_metadata() -> None:
    model = build_sequence_execution_model(minimal_v3_call_order_model())
    modes = {mode["id"]: mode for mode in model["order_modes"]}

    assert list(modes) == ["call", "dependency", "pipeline"]
    assert modes["call"]["label"] == "Call order"
    assert modes["call"]["order_kind"] == "root_call_layout"
    assert "root" in modes["call"]["description"].lower()
    assert modes["dependency"]["label"] == "Dependency order"
    assert modes["dependency"]["order_kind"] == "static_dependency_layout"
    assert "dependency" in modes["dependency"]["description"].lower()
    assert modes["pipeline"]["label"] == "Pipeline stage order"
    assert modes["pipeline"]["order_kind"] == "pipeline_stage_layout"
    assert "dependency" in modes["pipeline"]["description"].lower()


def test_sequence_execution_model_prefers_explicit_pipeline_stage_metadata() -> None:
    model = build_sequence_execution_model(minimal_v3_explicit_pipeline_model())
    pipeline_layout = model["layouts"]["pipeline"]
    steps = {step["block_id"]: step for step in pipeline_layout["steps"]}

    assert pipeline_layout["column_labels"] == [
        "Entry / utility",
        "Stage 2: Predict",
        "Stage 4: Output",
    ]
    assert steps["B_ENTRY"]["column"] == 0
    assert steps["B_FETCH"]["column"] == 1
    assert steps["B_FILTER"]["column"] == 1
    assert steps["B_JOIN"]["column"] == 1
    assert steps["B_STORE"]["column"] == 2
    assert steps["B_FETCH"]["pipeline_stage"] == 2
    assert steps["B_FETCH"]["pipeline_stage_source"] == "explicit"
    assert steps["B_FILTER"]["pipeline_stage_source"] == "explicit"
    assert steps["B_JOIN"]["pipeline_lane_role"] == "join"
    assert steps["B_STORE"]["pipeline_lane_role"] == "final"
    assert steps["B_JOIN"]["lane"] > steps["B_FETCH"]["lane"]
    assert steps["B_JOIN"]["lane"] > steps["B_FILTER"]["lane"]


def test_sequence_execution_model_uses_call_graph_without_call_instances() -> None:
    data = minimal_v3_call_order_model()
    data["blocks"].append(
        {
            "block_id": "B_LEAF",
            "block_name": "bpc_leaf_op",
            "inputs": ["helper_result"],
            "outputs": ["leaf_result"],
            "estimated_cycles": 1,
        }
    )
    data["flow"]["sequence_timeline"].insert(
        0,
        {
            "step_id": "T_0000_B_LEAF",
            "order_index": 0,
            "block_id": "B_LEAF",
            "function": "bpc_leaf_op",
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
        },
    )
    data["flow"]["execution_order"] = ["B_LEAF", "B_HELPER", "B_TOP"]
    data["flow"]["call_instances"] = []
    nodes = data["flow"]["call_graph"]["nodes"]
    nodes["bpc_inner_op"]["callees"] = ["bpc_leaf_op"]
    nodes["bpc_leaf_op"] = {
        "function_name": "bpc_leaf_op",
        "block_id": "B_LEAF",
        "callers": ["bpc_inner_op"],
        "callees": [],
    }
    for step in data["flow"]["sequence_timeline"]:
        step["call_ids_as_caller"] = []
        step["call_ids_as_callee"] = []

    model = build_sequence_execution_model(data)
    call_steps = {step["block_id"]: step for step in model["layouts"]["call"]["steps"]}

    assert call_steps["B_TOP"]["column"] < call_steps["B_HELPER"]["column"]
    assert call_steps["B_HELPER"]["column"] < call_steps["B_LEAF"]["column"]


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


def test_viewer_sequence_order_toggle_controls_present() -> None:
    html = build_html(minimal_v3_call_order_model())

    assert "sequenceOrderMode" in html
    assert "renderSequenceOrderModeControls" in html
    assert "Call order" in html
    assert "Dependency order" in html
    assert "Pipeline stage order" in html
    assert "root/caller" in html
    assert "static dependency" in html
    assert "pipeline stage" in html.lower()


def test_viewer_sequence_label_density_controls_present() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "sequenceLabelMode" in html
    assert "renderSequenceLabelModeControls" in html
    assert "Edge labels" in html
    assert "Compact labels" in html
    assert "All labels" in html
    assert "No labels" in html


def test_viewer_sequence_map_controls_support_v3_and_legacy_maps() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "serializeSequenceMap" in html
    assert "loadSequenceMapPayload" in html
    assert "sequence_block_positions" in html
    assert "sequence_block_positions_by_mode" in html
    assert "sequence_order_mode" in html
    assert "sequenceBlockMapsByMode" in html
    assert "const legacyPositions" in html
    assert "state.sequenceBlockMapsByMode.dependency = legacyPositions" in html
    assert 'state.sequenceOrderMode = "dependency"' in html
    assert "sequence_block_positions: sequenceBlockMapsByMode.dependency || {}" in html
    assert "Reset cards only" in html
    assert "Reset Sequence Layout" not in html
    assert "pointercancel" in html
    assert "finishSequenceDrag" in html


def test_viewer_exposes_sequence_import_export_e2e_dom_hooks() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "exportSequenceMapPayload" in html
    assert "importSequenceMapPayload" in html
    assert "installViewerTestHooks" in html
    assert "test-hooks" in html
    assert "cvas-viewer-test-hooks" in html
    assert "cvas-test-map-input" in html
    assert "cvas-test-map-output" in html
    assert "cvas-test-export-map" in html
    assert "cvas-test-import-map" in html
    assert "const rawPayload = input.value || output.value" in html


def test_viewer_route_params_can_open_sequence_pipeline_order_for_e2e() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "applyInitialViewerRouteParams" in html
    assert 'params.get("tab")' in html
    assert 'params.get("view")' in html
    assert 'state.activeTab = "sequence"' in html
    assert 'params.get("sequence_order_mode")' in html
    assert 'params.get("sequenceOrderMode")' in html
    assert 'params.get("order")' in html
    assert "getSequenceOrderModeIds().includes(requestedOrder)" in html
    assert 'setTab(state.activeTab === "sequence" ? "sequence" : "diagram")' in html


def test_viewer_sequence_map_exports_edge_density_and_stage_filter_state() -> None:
    html = build_html(minimal_v3_explicit_pipeline_model())

    assert "sequenceEdgeDensityMode" in html
    assert "sequenceStageFilter" in html
    assert "sequence_edge_density_mode" in html
    assert "sequence_stage_filter" in html
    assert "renderSequenceEdgeDensityControls" in html
    assert "renderSequenceStageFilterControls" in html
    assert "Sequence edge density" in html
    assert "All edges" in html
    assert "Stage-local edges" in html
    assert "Selected stage only" in html
    assert "Sequence stage filter" in html
    assert "visibleSequenceEdgesForMode" in html


def test_viewer_reset_view_is_context_aware_for_sequence_tab() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "resetCurrentView" in html
    assert "resetSequenceCurrentMode" in html
    assert "activeTab === 'sequence'" in html or 'activeTab === "sequence"' in html
    assert "state.sequenceMap = {}" in html
    assert "state.sequenceGroupMap = {}" in html


def test_viewer_diagram_pan_compensates_for_viewbox_scale_without_zoom_slowdown() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "getDiagramPanDelta" in html
    assert "DIAGRAM_PAN_SPEED" in html
    assert "viewBox.width / rect.width" in html
    assert "viewBox.height / rect.height" in html
    assert "* unitsPerPixelX * DIAGRAM_PAN_SPEED" in html
    assert "* unitsPerPixelY * DIAGRAM_PAN_SPEED" in html
    assert "lastPanPoint" in html
    assert "unitsPerPixelX / zoom" not in html
    assert "unitsPerPixelY / zoom" not in html


def test_viewer_diagram_wheel_zoom_tracks_mouse_pointer() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "diagramClientPointToViewport" in html
    assert "zoomDiagramAtPointer" in html
    assert "const diagramPointX = (point.x - state.viewTransform.x) / oldZoom" in html
    assert "const diagramPointY = (point.y - state.viewTransform.y) / oldZoom" in html
    assert "state.viewTransform.x = point.x - diagramPointX * nextZoom" in html
    assert "state.viewTransform.y = point.y - diagramPointY * nextZoom" in html
    assert "zoomDiagramAtPointer(svg, state, event)" in html


def test_viewer_sequence_drag_keeps_original_zoom_normalized_speed() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "getSequenceDragDelta" in html
    assert "SEQUENCE_DRAG_SPEED" not in html
    assert "Math.max(0.1, state.sequenceZoom || 1)" in html
    assert "x: deltaX / zoom," in html
    assert "y: deltaY / zoom" in html


def test_viewer_sequence_edge_labels_render_at_endpoints() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "appendSequenceEndpointLabel" in html
    assert "sequence-edge-label source" in html
    assert "sequence-edge-label target" in html
    assert "text-anchor" in html
    assert "String((x1 + x2) / 2 + 6)" not in html


def test_viewer_sequence_edges_have_immediate_redraw_fallback() -> None:
    html = build_html(minimal_v3_parallel_model())

    assert "drawSequenceExecutionEdges(board, activeModel, state);" in html
    assert "requestAnimationFrame(() => drawSequenceExecutionEdges(board, activeModel, state));" in html
    assert "const activeModel = getActiveSequenceModel(state, SEQUENCE_EXECUTION_MODEL);" in html
    assert "drawSequenceExecutionEdges(execBoard, activeModel, state);" in html
    assert "redrawActiveSequenceEdges(state);\n        requestAnimationFrame" in html


def test_viewer_details_panel_toggle_is_adjacent_and_two_state() -> None:
    html = build_html(minimal_v2_model())

    assert "detail-panel-header" in html
    assert "detail-panel-toggle" in html
    assert '<button id=\"detailToggle\" class=\"detail-panel-toggle\">Narrow Details</button>' in html
    assert "details-narrow" in html
    assert "details-expanded" in html
    assert "details-collapsed" not in html
    assert "Hide Details" not in html
    assert "Show Details" not in html
    assert 'const allowed = ["expanded", "narrow"]' in html
    assert "expanded <-> narrow" in html
    assert "toggleDetailsPanel" in html
    assert "setDetailsMode" in html


def test_viewer_html_contains_v3_detail_fields() -> None:
    html = build_html(minimal_v2_model())

    assert "call_id" in html
    assert "signal_id" in html
    assert "schemaVersion" in html
