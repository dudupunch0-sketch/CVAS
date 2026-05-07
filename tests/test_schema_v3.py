"""Targeted tests for the additive CVAS JSON Schema v3 timeline contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
CVAS_PARSER = REPO_ROOT / "src" / "cvas_mvp.py"
FIXTURE_C = REPO_ROOT / "tests" / "fixtures" / "schema" / "sequence_timeline_v3.c"
SCHEMA_JSON = REPO_ROOT / "docs" / "schema" / "cvas.schema.v3.json"
EXAMPLE_JSON = REPO_ROOT / "tests" / "fixtures" / "schema" / "sequence_timeline_v3.expected.json"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cvas_analysis import AnalysisOptions
from cvas_model import Block, CycleRules, OpSummary
from cvas_passes import FunctionAnalysisResult
from cvas_pipeline import build_call_instances, build_model


def run_cvas(input_c: Path, extra_args: Optional[List[str]] = None) -> Dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        output_json = Path(tmpdir) / "output.json"
        cmd = [sys.executable, str(CVAS_PARSER), str(input_c), "-o", str(output_json)]
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "CVAS parser failed:\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )
        return json.loads(output_json.read_text(encoding="utf-8"))


def run_cvas_relative_fixture() -> Dict[str, object]:
    """Generate the checked-in schema fixture using repo-relative paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_json = Path(tmpdir) / "output.json"
        result = subprocess.run(
            [
                sys.executable,
                str(CVAS_PARSER.relative_to(REPO_ROOT)),
                "tests/fixtures/schema/sequence_timeline_v3.c",
                "-o",
                str(output_json),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "CVAS parser failed:\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )
        return json.loads(output_json.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def model() -> Dict[str, object]:
    return run_cvas(FIXTURE_C)


def test_schema_v3_docs_and_example_are_valid_json() -> None:
    json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))


def test_schema_v3_example_validates_against_json_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))

    jsonschema.validate(instance=example, schema=schema)


def test_checked_in_schema_fixture_matches_generated_output() -> None:
    expected = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    assert run_cvas_relative_fixture() == expected


def test_full_mode_model_keeps_schema_v3_and_gcc_dump_metadata() -> None:
    full_model = run_cvas(FIXTURE_C, extra_args=["--analysis-mode", "full"])
    flow = full_model["flow"]

    assert full_model["schema_version"] == "3.0"
    assert full_model["analysis_mode"] == "full"
    assert full_model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert "gcc_dump" in full_model
    assert flow["execution_order_meta"]["kind"] == "static_block_order"
    assert flow["call_instances"]
    assert flow["sequence_timeline"]
    assert flow["function_io"]["functions"]
    assert set(flow["dependencies"]) == {"inter_block", "call_instance"}


def test_no_region_model_normalizes_function_io_envelope() -> None:
    model = build_model(
        "int helper(int x) { return x; }",
        CycleRules(),
        analysis_options=AnalysisOptions(mode="fast"),
        function_io={"helper": {"reads": ["x"], "writes": []}},
    )

    function_io = model["flow"]["function_io"]
    assert function_io == {
        "source": "embedded_file",
        "functions": {
            "helper": {
                "reads": ["x"],
                "writes": [],
                "provenance": {"source": "embedded_file", "confidence": "medium"},
            }
        },
    }


def test_call_instance_provenance_uses_function_call_metadata() -> None:
    caller_result = FunctionAnalysisResult(
        name="caller",
        block=Block(
            block_id="B1",
            block_name="caller",
            inputs=[],
            outputs=["return"],
            internal_ops_summary=OpSummary(),
            estimated_cycles=0,
            note="test",
        ),
        operations=[],
        signals=[],
        calls=[("callee", ["x"], "y")],
        call_metadata={"parser": "text", "limitations": ["AST parse failed; text fallback used"]},
        unresolved_calls=[],
        external_symbols=[],
        function_def_meta={"file": "test.c"},
    )
    callee_result = FunctionAnalysisResult(
        name="callee",
        block=Block(
            block_id="B2",
            block_name="callee",
            inputs=["value"],
            outputs=["return"],
            internal_ops_summary=OpSummary(),
            estimated_cycles=0,
            note="test",
        ),
        operations=[],
        signals=[],
        calls=[],
        call_metadata={"parser": "ast", "limitations": []},
        unresolved_calls=[],
        external_symbols=[],
        function_def_meta={"file": "test.c"},
    )
    call_instances = build_call_instances(
        [
            ("int", "caller", "int x", "", "test.c"),
            ("int", "callee", "int value", "", "test.c"),
        ],
        {"caller": "B1", "callee": "B2"},
        {"caller": caller_result, "callee": callee_result},
    )

    assert call_instances[0].provenance == {
        "source": "static",
        "parser": "text",
        "confidence": "low",
        "limitations": ["AST parse failed; text fallback used"],
    }


def test_model_declares_schema_version_v3(model: Dict[str, object]) -> None:
    assert model["schema_version"] == "3.0"
    assert model["schema"]["name"] == "cvas-analysis"
    assert model["schema"]["compatibility"]["preserves_v2_fields"] is True


def test_call_instances_have_stable_unique_ids(model: Dict[str, object]) -> None:
    flow = model["flow"]
    call_instances = flow["call_instances"]
    repeated = [call for call in call_instances if call["caller_function"] == "top" and call["callee_function"] == "inc"]

    assert len(repeated) == 2
    assert [call["call_id"] for call in repeated] == ["C_B2_0001", "C_B2_0002"]
    assert [call["ordinal_in_caller"] for call in repeated] == [1, 2]
    assert len({call["call_id"] for call in call_instances}) == len(call_instances)
    assert flow["call_sequence"]


def test_call_argument_signals_reference_call_ids(model: Dict[str, object]) -> None:
    call_ids = {call["call_id"] for call in model["flow"]["call_instances"]}
    argument_signals = [signal for signal in model["signals"] if signal.get("kind") == "call_argument"]

    assert argument_signals
    for signal in argument_signals:
        assert signal["signal_id"] == f"S_{signal['call_id']}_ARG_{signal['arg_index']}"
        assert signal["call_id"] in call_ids
        assert isinstance(signal["arg_index"], int)
        assert "param" in signal
        assert signal["expr"]
        assert signal["role"] == "read"
        assert signal["source_function"] == "top"
        assert signal["destination_function"] == "inc"
        assert signal["source_id"] == "B2"
        assert signal["destination_id"] == "B1"


def test_call_return_signals_reference_call_ids(model: Dict[str, object]) -> None:
    call_ids = {call["call_id"] for call in model["flow"]["call_instances"]}
    return_signals = [signal for signal in model["signals"] if signal.get("kind") == "call_return"]

    assert return_signals
    for signal in return_signals:
        assert signal["signal_id"] == f"S_{signal['call_id']}_RET"
        assert signal["call_id"] in call_ids
        assert signal["role"] == "write"
        assert signal["target"] == signal["expr"] == signal["signal_name"]
        assert signal["source_function"] == "inc"
        assert signal["destination_function"] == "top"
        assert signal["source_id"] == "B1"
        assert signal["destination_id"] == "B2"


def test_sequence_timeline_references_existing_blocks_calls_and_signal_ids(model: Dict[str, object]) -> None:
    block_ids = {block["block_id"] for block in model["blocks"]}
    call_ids = {call["call_id"] for call in model["flow"]["call_instances"]}
    signal_ids = {signal["signal_id"] for signal in model["signals"] if signal.get("signal_id")}
    timeline = model["flow"]["sequence_timeline"]

    assert timeline
    assert [step["block_id"] for step in timeline] == model["flow"]["execution_order"]
    assert model["flow"]["execution_order_meta"]["kind"] == "static_block_order"

    for step in timeline:
        assert step["block_id"] in block_ids
        assert step["step_id"] == f"T_{step['order_index']:04d}_{step['block_id']}"
        for call_id in step["call_ids_as_caller"] + step["call_ids_as_callee"]:
            assert call_id in call_ids
        for signal_id in step["incoming_signal_ids"] + step["outgoing_signal_ids"]:
            assert signal_id in signal_ids
        summary = step["read_write_summary"]
        assert {"reads_from_other", "read_by_other", "writes_to_other", "written_by_other"} <= set(summary)


def test_v3_preserves_legacy_diagram_fields(model: Dict[str, object]) -> None:
    assert model["blocks"]
    assert model["operations"]
    assert model["signals"]
    assert model["flow"]["execution_order"]
    assert model["flow"]["call_sequence"]
    for signal in model["signals"]:
        for field in ["source_id", "source_type", "destination_id", "destination_type", "signal_name", "direction"]:
            assert field in signal
