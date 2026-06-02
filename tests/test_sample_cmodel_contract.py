"""Contract tests for the checked-in CVAS sample cmodel and syntax fixtures."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from json_to_html import build_sequence_execution_model  # noqa: E402
from cvas_analysis import AnalysisOptions  # noqa: E402
from cvas_callgraph import find_function_calls  # noqa: E402
from cvas_source import find_function_definitions  # noqa: E402

CVAS_PARSER = ROOT_DIR / "src" / "cvas_mvp.py"
SAMPLE_C = ROOT_DIR / "test_examples.c"
CPP_FIXTURE = ROOT_DIR / "tests" / "fixtures" / "syntax" / "cpp_syntax_coverage.cpp"
CPP_PROJECT_ROOT = ROOT_DIR / "tests" / "fixtures" / "cpp_project"
CPP_PROJECT_ENTRY = CPP_PROJECT_ROOT / "src" / "bpc_project_pipeline.cpp"
CPP_PROJECT_INCLUDE = CPP_PROJECT_ROOT / "include"
FULL_SAMPLE_JSON = ROOT_DIR / "docs" / "test_examples_output_full.json"


def _run_cvas(input_path: Path, *extra_args: str) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "model.json"
        cmd = [sys.executable, str(CVAS_PARSER), str(input_path), "-o", str(output_path)]
        cmd.extend(extra_args)
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        assert result.returncode == 0, (
            "CVAS parser failed\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        return json.loads(output_path.read_text(encoding="utf-8"))


def _function_names(model: dict) -> set[str]:
    return {block.get("block_name", "") for block in model.get("blocks", [])}


def _call_graph_node(model: dict, name: str) -> dict:
    nodes = model.get("flow", {}).get("call_graph", {}).get("nodes", {})
    assert name in nodes, f"missing call graph node: {name}"
    return nodes[name]


def _direct_callees(model: dict, name: str) -> set[str]:
    node = _call_graph_node(model, name)
    return set(node.get("callees", []))


def _stage_functions(source: str, stage_number: int) -> list[str]:
    pattern = re.compile(r"\bstatic\s+[^;{()]+\s+(bpc_stage%d_[A-Za-z0-9_]+)\s*\(" % stage_number)
    return sorted(set(pattern.findall(source)))


def test_sample_cmodel_has_six_stage_pipeline_contract():
    source = SAMPLE_C.read_text(encoding="utf-8")

    assert "void simple_bpc_frame" in source
    assert "static int simple_bpc_pixel" in source

    for stage_number in range(1, 7):
        functions = _stage_functions(source, stage_number)
        assert len(functions) >= 4, f"stage {stage_number} has too few helper functions: {functions}"
        assert any("join" in name or "final" in name for name in functions), (
            f"stage {stage_number} needs a join/finalize helper: {functions}"
        )
        lane_functions = [name for name in functions if "join" not in name and "final" not in name]
        assert len(lane_functions) >= 3, (
            f"stage {stage_number} needs at least 3 parallel lane/helper functions: {functions}"
        )


def test_sample_cmodel_sequence_depth_and_parallel_lanes():
    model = _run_cvas(SAMPLE_C)
    names = _function_names(model)

    assert "simple_bpc_frame" in names
    assert "simple_bpc_pixel" in names
    assert "simple_bpc_pixel" in _direct_callees(model, "simple_bpc_frame")

    pixel_callees = _direct_callees(model, "simple_bpc_pixel")
    for stage_number in range(1, 7):
        stage_callees = {name for name in pixel_callees if name.startswith(f"bpc_stage{stage_number}_")}
        assert len(stage_callees) >= 4, (
            f"simple_bpc_pixel should call at least 4 stage {stage_number} helpers, got {stage_callees}"
        )
        assert any("join" in name or "final" in name for name in stage_callees)

    timeline = model.get("flow", {}).get("sequence_timeline", [])
    timeline_text = json.dumps(timeline)
    for stage_number in range(1, 7):
        assert f"bpc_stage{stage_number}_" in timeline_text
    assert len(timeline) >= 30


def test_sample_cmodel_call_order_layout_separates_pipeline_stages():
    model = _run_cvas(SAMPLE_C)
    sequence_model = build_sequence_execution_model(model)
    call_steps = sequence_model["layouts"]["call"]["steps"]

    stage_column_ranges: list[tuple[int, int]] = []
    for stage_number in range(1, 7):
        columns = [
            step["column"]
            for step in call_steps
            if step["function"].startswith(f"bpc_stage{stage_number}_")
        ]
        assert columns, f"stage {stage_number} has no sequence cards"
        stage_column_ranges.append((min(columns), max(columns)))

    for previous_stage, next_stage in zip(stage_column_ranges, stage_column_ranges[1:]):
        assert previous_stage[1] < next_stage[0], (
            "pipeline stages should progress left-to-right in Call order; "
            f"got adjacent ranges {previous_stage} and {next_stage}"
        )


def test_sample_cmodel_pipeline_stage_layout_groups_stage_lanes_in_parallel():
    model = _run_cvas(SAMPLE_C)
    sequence_model = build_sequence_execution_model(model)
    pipeline_layout = sequence_model["layouts"]["pipeline"]
    pipeline_steps = pipeline_layout["steps"]

    assert pipeline_layout["order_kind"] == "pipeline_stage_layout"
    assert pipeline_layout["column_labels"][:8] == [
        "Entry / utility",
        "Pipeline setup",
        "Stage 1",
        "Stage 2",
        "Stage 3",
        "Stage 4",
        "Stage 5",
        "Stage 6",
    ]
    assert pipeline_layout["lanes"] >= 5

    steps_by_function = {step["function"]: step for step in pipeline_steps}
    assert steps_by_function["simple_bpc_frame"]["column"] < steps_by_function["simple_bpc_pixel"]["column"]
    assert steps_by_function["simple_bpc_pixel"]["column"] < steps_by_function["bpc_stage1_coord_lane"]["column"]

    stage_columns: list[int] = []
    for stage_number in range(1, 7):
        stage_steps = [
            step
            for step in pipeline_steps
            if step["function"].startswith(f"bpc_stage{stage_number}_")
        ]
        assert len(stage_steps) >= 5, f"stage {stage_number} should expose lane/helper cards"

        columns = {step["column"] for step in stage_steps}
        lanes = {step["lane"] for step in stage_steps}
        assert len(columns) == 1, f"stage {stage_number} helpers should share one stage column"
        assert len(lanes) == len(stage_steps), f"stage {stage_number} helpers should occupy parallel lanes"

        join_step = next(step for step in stage_steps if "join" in step["function"] or "final" in step["function"])
        assert join_step["lane"] == max(lanes), "join/final helper should sit below the parallel lanes"
        stage_columns.append(next(iter(columns)))

    assert stage_columns == sorted(stage_columns)


def test_sample_cmodel_c_syntax_markers_are_present():
    source = SAMPLE_C.read_text(encoding="utf-8")

    required_substrings = [
        "typedef unsigned short",
        "typedef struct",
        "struct bpc_debug_record",
        "enum bpc_pixel_state",
        "enum {",
        "(*window)",
        "[BPC_KERNEL_SIZE]",
        "[BPC_KERNEL_SIZE][BPC_KERNEL_SIZE]",
        "&=",
        "|=",
        "^",
        "~",
        "<<",
        ">>",
        "sizeof(",
        "(bpc_sample_t)",
        "NULL",
        "else if",
        "switch",
        "do {",
        "continue;",
        "return;",
        "printf(",
        "fprintf(",
        "sprintf(",
        "fopen(",
        "fclose(",
    ]
    for marker in required_substrings:
        assert marker in source, f"missing C syntax marker: {marker}"

    assert re.search(r"\?.*\?.*:.*:", source, re.DOTALL), "missing nested ternary expression"
    assert re.search(r"\bfor\s*\(", source), "missing for loop"
    assert re.search(r"\bwhile\s*\(", source), "missing while loop"
    assert "++" in source, "missing increment syntax"
    assert "--" in source, "missing decrement syntax"


def test_checked_in_full_sample_gcc_dump_command_is_reproducible():
    model = json.loads(FULL_SAMPLE_JSON.read_text(encoding="utf-8"))
    gcc_dump = model.get("gcc_dump", {})
    command = str(gcc_dump.get("command", ""))

    assert gcc_dump.get("status") == "ok"
    assert gcc_dump.get("command_path_policy") == "normalized"
    assert "<gcc-dump-dir>/cvas-gcc-dump.o" in command
    assert "test_examples.c" in command
    assert "/tmp/cvas-gcc-dump-" not in command
    assert str(ROOT_DIR) not in command


def test_cpp_syntax_fixture_contains_target_cpp_patterns():
    source = CPP_FIXTURE.read_text(encoding="utf-8")

    required_substrings = [
        "template<class T>",
        "class DerivedProcessor : public BaseProcessor",
        "private:",
        "public:",
        "struct ScratchSlot",
        "~BaseProcessor()",
        "virtual ~BaseProcessor()",
        "virtual int process",
        "virtual const char *label() const {",
        "static int scale_value",
        "static int call_count",
        "static const int kMaxSamples",
        "static const bool kEnabled",
        "int &mutable_ref",
        "const int &readonly_ref",
        "const std::string&",
        "const char *",
        "new int[",
        "delete[]",
        "new int**[",
        "int (*row)[4]",
        "int (*grid)[3][4]",
    ]
    for marker in required_substrings:
        assert marker in source, f"missing C++ syntax marker: {marker}"

    forbidden_substrings = [
        "ac_int",
        "ac_uint",
        "ac_fixed",
        "sc_int",
        "sc_uint",
        "sc_bigint",
        "sc_bv",
        "sc_lv",
        "#pragma HLS",
        ".range(",
    ]
    for marker in forbidden_substrings:
        assert marker not in source, f"HLS/SystemC marker should be absent: {marker}"


def test_cpp_syntax_fixture_compiles_syntax_only():
    result = subprocess.run(
        [
            "g++",
            "-std=c++11",
            "-DCVAS_START=",
            "-DCVAS_END=",
            "-fsyntax-only",
            str(CPP_FIXTURE),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cpp_syntax_fixture_cvas_full_mode_non_crash():
    model = _run_cvas(CPP_FIXTURE, "--analysis-mode", "full", "--language", "c++")

    assert model["analysis_mode"] == "full"
    assert model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert model.get("blocks"), "C++ syntax fixture should discover at least one block"


def test_cpp_call_resolution_does_not_guess_member_from_unqualified_suffix():
    calls, _ = find_function_calls(
        "return reset();",
        {"top", "Filter::reset"},
        caller_name="top",
    )

    assert calls == []


def test_cpp_call_resolution_uses_local_pointer_type_for_member_calls():
    known = {"top", "Filter::process", "Other::process"}
    calls, _ = find_function_calls(
        "Filter *ptr = 0; return ptr->process(1);",
        known,
        caller_name="top",
    )

    assert calls == [("Filter::process", ["1"], None)]


def test_cpp_regex_fallback_models_constructors_as_void_blocks():
    functions = find_function_definitions(
        CPP_FIXTURE.read_text(encoding="utf-8"),
        analysis_options=AnalysisOptions(mode="fast", language_override="c++"),
        source_path=CPP_FIXTURE,
        merge_fallback=True,
    )
    returns_by_name = {name: ret for ret, name, _, _ in functions}

    assert returns_by_name["BaseProcessor::BaseProcessor"] == "void"
    assert returns_by_name["BaseProcessor::~BaseProcessor"] == "void"
    assert returns_by_name["DerivedProcessor::DerivedProcessor"] == "void"
    assert returns_by_name["DerivedProcessor::~DerivedProcessor"] == "void"


def test_cpp_syntax_fixture_cvas_full_mode_models_cpp_cmodel():
    model = _run_cvas(CPP_FIXTURE, "--analysis-mode", "full", "--language", "c++")
    names = _function_names(model)

    required_blocks = {
        "clamp_value",
        "BaseProcessor::BaseProcessor",
        "BaseProcessor::~BaseProcessor",
        "BaseProcessor::label",
        "BaseProcessor::scale_value",
        "DerivedProcessor::DerivedProcessor",
        "DerivedProcessor::~DerivedProcessor",
        "DerivedProcessor::process",
        "DerivedProcessor::label",
        "DerivedProcessor::adjust",
        "select_processor_label",
        "sum_row_array",
        "sum_grid_array",
        "allocate_line",
        "release_line",
        "allocate_cube",
        "release_cube",
        "run_cpp_syntax_fixture",
    }
    assert required_blocks.issubset(names)
    assert names.isdisjoint({"name_", "process", "label", "adjust", "BaseProcessor", "DerivedProcessor"})

    blocks_by_name = {block["block_name"]: block for block in model["blocks"]}
    assert blocks_by_name["DerivedProcessor::adjust"]["inputs"] == [
        "mutable_ref",
        "readonly_ref",
        "tag",
    ]
    assert blocks_by_name["sum_row_array"]["inputs"] == ["row", "count"]
    assert blocks_by_name["sum_grid_array"]["inputs"] == ["grid"]

    assert {
        "sum_row_array",
        "sum_grid_array",
        "DerivedProcessor::process",
        "allocate_line",
        "allocate_cube",
        "release_cube",
        "release_line",
        "clamp_value",
    }.issubset(_direct_callees(model, "run_cpp_syntax_fixture"))
    assert "DerivedProcessor::adjust" in _direct_callees(model, "DerivedProcessor::process")
    assert "BaseProcessor::scale_value" in _direct_callees(model, "DerivedProcessor::adjust")
    assert "BaseProcessor::label" in _direct_callees(model, "select_processor_label")

    sequence_model = build_sequence_execution_model(model)
    sequence_text = json.dumps(sequence_model)
    for name in required_blocks:
        assert name in sequence_text


def test_cpp_project_fixture_compiles_syntax_only():
    result = subprocess.run(
        [
            "g++",
            "-std=c++11",
            f"-I{CPP_PROJECT_INCLUDE}",
            "-DCVAS_START=",
            "-DCVAS_END=",
            "-fsyntax-only",
            str(CPP_PROJECT_ROOT / "src" / "bpc_project_math.cpp"),
            str(CPP_PROJECT_ROOT / "src" / "bpc_project_processor.cpp"),
            str(CPP_PROJECT_ENTRY),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cpp_project_fixture_cvas_project_mode_models_included_cmodel():
    model = _run_cvas(
        CPP_PROJECT_ENTRY,
        "--analysis-mode",
        "full",
        "--language",
        "c++",
        "--project-root",
        str(CPP_PROJECT_ROOT),
        "--source-extensions",
        "cpp,hpp",
        f"--compile-arg=-I{CPP_PROJECT_INCLUDE}",
    )
    names = _function_names(model)

    required_blocks = {
        "run_project_bpc_frame",
        "project_sum_row_array",
        "project_sum_grid_array",
        "project_select_processor_label",
        "BpcProjectDerivedProcessor::process",
        "BpcProjectDerivedProcessor::adjust",
        "BpcProjectBaseProcessor::scale_value",
        "BpcProjectBaseProcessor::label",
        "project_allocate_line",
        "project_allocate_cube",
        "project_load_window",
        "project_load_pixel",
        "project_release_cube",
        "project_release_line",
        "project_clamp_value",
    }
    assert required_blocks.issubset(names)
    assert names.isdisjoint({"name_", "process", "label", "adjust"})
    assert model["project_mode"] is True
    assert model["gcc_dump"]["status"] == "ok"

    blocks_by_name = {block["block_name"]: block for block in model["blocks"]}
    assert blocks_by_name["project_sum_row_array"]["inputs"] == ["row", "count"]
    assert blocks_by_name["project_sum_grid_array"]["inputs"] == ["grid"]
    assert blocks_by_name["BpcProjectDerivedProcessor::adjust"]["inputs"] == [
        "mutable_ref",
        "readonly_ref",
        "tag",
    ]
    assert blocks_by_name["project_load_window"]["inputs"] == ["raw", "coord"]

    run_callees = _direct_callees(model, "run_project_bpc_frame")
    assert {
        "project_sum_row_array",
        "project_sum_grid_array",
        "project_select_processor_label",
        "BpcProjectDerivedProcessor::process",
        "project_allocate_line",
        "project_allocate_cube",
        "project_load_window",
        "project_release_cube",
        "project_release_line",
        "project_clamp_value",
    }.issubset(run_callees)
    assert "BpcProjectBaseProcessor::label" in _direct_callees(model, "project_select_processor_label")
    assert "BpcProjectDerivedProcessor::adjust" in _direct_callees(model, "BpcProjectDerivedProcessor::process")
    assert "BpcProjectBaseProcessor::scale_value" in _direct_callees(model, "BpcProjectDerivedProcessor::adjust")
    assert "project_load_pixel" in _direct_callees(model, "project_load_window")
    assert "project_clamp_value" in _direct_callees(model, "project_load_pixel")

    sequence_model = build_sequence_execution_model(model)
    sequence_text = json.dumps(sequence_model)
    for name in required_blocks:
        assert name in sequence_text
