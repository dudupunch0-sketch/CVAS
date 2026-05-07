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

    assert "renderSequenceTimelineV3" in html
    assert "timeline-card" in html
    assert "sequence_timeline" in html


def test_viewer_html_contains_v3_detail_fields() -> None:
    html = build_html(minimal_v2_model())

    assert "call_id" in html
    assert "signal_id" in html
    assert "schemaVersion" in html
