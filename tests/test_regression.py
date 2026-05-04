"""Regression tests for CVAS parser using snapshot comparison."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import pytest

CVAS_PARSER = Path(__file__).resolve().parents[1] / "src" / "cvas_mvp.py"
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def run_cvas(
    input_c: Path, output_json: Path, extra_args: Optional[List[str]] = None
) -> Dict:
    """Run CVAS parser and return the resulting model.

    Args:
        input_c: Path to input C file
        output_json: Path where JSON output will be written

    Returns:
        Parsed JSON model as dict

    Raises:
        RuntimeError: If CVAS parser fails
    """
    result = run_cvas_process(input_c, output_json, extra_args=extra_args)

    if result.returncode != 0:
        raise RuntimeError(
            "CVAS parser failed:\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )

    return json.loads(output_json.read_text(encoding="utf-8"))


def run_cvas_process(
    input_c: Path, output_json: Path, extra_args: Optional[List[str]] = None
) -> subprocess.CompletedProcess[str]:
    """Run the CVAS parser and return the raw process result."""
    cmd = [
        sys.executable,
        str(CVAS_PARSER),
        str(input_c),
        "-o",
        str(output_json),
    ]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )

    return result


def build_snapshot(model: Dict) -> Dict:
    """Build comparable snapshot from full model.

    Extracts only the fields we want to compare in regression tests:
    - operations
    - signals
    - cfg structures
    - call_graph
    - operation summaries and cycle estimates

    Args:
        model: Full CVAS model dict

    Returns:
        Snapshot dict with comparable fields
    """
    cfg_snapshot = []
    blocks_summary = []

    for block in model.get("blocks", []):
        cfg = block.get("cfg")
        if cfg:
            cfg_snapshot.append(
                {
                    "block_name": block.get("block_name"),
                    "cfg": cfg,
                }
            )

        blocks_summary.append(
            {
                "block_name": block.get("block_name"),
                "block_id": block.get("block_id"),
                "ops_summary": block.get("internal_ops_summary"),
                "estimated_cycles": block.get("estimated_cycles"),
            }
        )

    return {
        "operations": model.get("operations", []),
        "signals": model.get("signals", []),
        "blocks_summary": blocks_summary,
        "cfg": cfg_snapshot,
        "call_graph": model.get("flow", {}).get("call_graph"),
    }


def normalize_json(data: Dict) -> str:
    """Normalize JSON for comparison.

    Uses consistent formatting (sorted keys, indentation) so that
    minor formatting differences don't cause test failures.
    """
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def get_all_fixtures() -> List[str]:
    """Discover all test fixtures recursively.

    Returns list of fixture base names (without .c extension)
    that have both .c and .expected.json files.
    """
    fixtures = []

    for c_file in FIXTURES_DIR.rglob("*.c"):
        rel_path = c_file.relative_to(FIXTURES_DIR)
        base_name = rel_path.with_suffix("")
        expected_file = c_file.with_suffix(".expected.json")

        if expected_file.exists():
            fixtures.append(str(base_name))

    return sorted(fixtures)


@pytest.mark.parametrize("fixture_name", get_all_fixtures())
def test_fixture_regression(fixture_name: str, update_snapshots: bool):
    """Test that fixture output matches expected snapshot.

    Args:
        fixture_name: Name of fixture (without extension)
        update_snapshots: If True, update expected file instead of comparing
    """
    fixture_c = FIXTURES_DIR / f"{fixture_name}.c"
    expected_json = FIXTURES_DIR / f"{fixture_name}.expected.json"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "output.json"
        model = run_cvas(fixture_c, output_path)

    snapshot = build_snapshot(model)
    snapshot_normalized = normalize_json(snapshot)

    if update_snapshots:
        expected_json.write_text(snapshot_normalized + "\n", encoding="utf-8")
        pytest.skip(f"Updated snapshot for {fixture_name}")
    else:
        if not expected_json.exists():
            pytest.fail(
                f"Expected snapshot not found: {expected_json}\n"
                "Run with --update-snapshots to create it"
            )

        expected = json.loads(expected_json.read_text(encoding="utf-8"))
        expected_normalized = normalize_json(expected)

        if snapshot_normalized != expected_normalized:
            import difflib

            diff = difflib.unified_diff(
                expected_normalized.splitlines(keepends=True),
                snapshot_normalized.splitlines(keepends=True),
                fromfile=f"{fixture_name}.expected.json",
                tofile=f"{fixture_name}.actual",
                lineterm="",
            )

            pytest.fail(f"Snapshot mismatch for {fixture_name}:\n\n" + "".join(diff))


def test_no_fixtures_found():
    """Ensure we actually found some fixtures to test."""
    fixtures = get_all_fixtures()
    assert len(fixtures) > 0, (
        f"No test fixtures found in {FIXTURES_DIR}. "
        "Expected files matching *.c with corresponding *.expected.json"
    )


def test_analysis_modes_report_deployable_backends():
    from cvas_analysis import AnalysisOptions

    assert AnalysisOptions(mode="fast").backend == "pycparser"
    assert AnalysisOptions(mode="full").backend == "tree-sitter+pycparser+gcc-dump"

    with pytest.raises(ValueError, match="Unsupported analysis mode"):
        AnalysisOptions(mode="tolerant")


def test_full_analysis_mode_uses_fast_plus_gcc_dump_backend_without_clang():
    fixture_c = FIXTURES_DIR / "minimal" / "single_add.c"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "output.json"
        model = run_cvas(
            fixture_c,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert model["analysis_mode"] == "full"
    assert model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert model["blocks"]
    assert model["gcc_dump"]["backend"] in {"gcc", "g++"}
    assert model["gcc_dump"]["status"] in {"ok", "failed", "unavailable"}


def test_full_mode_uses_tree_sitter_function_definitions_when_available(monkeypatch):
    import cvas_source
    from cvas_analysis import AnalysisOptions

    observed = {}

    def fake_tree_sitter(source, *, language=None, source_path=None, region_bounds=None):
        observed["source"] = source
        observed["language"] = language
        observed["source_path"] = source_path
        observed["region_bounds"] = region_bounds
        return [("int", "from_tree_sitter", "int x", "return x;")]

    monkeypatch.setattr(
        cvas_source,
        "find_function_definitions_with_tree_sitter",
        fake_tree_sitter,
    )

    functions = cvas_source.find_function_definitions(
        "int ignored(void) { return 0; }",
        analysis_options=AnalysisOptions(mode="full", language_override="c++"),
    )

    assert functions == [("int", "from_tree_sitter", "int x", "return x;")]
    assert observed["language"] == "c++"


def test_gcc_dump_command_uses_gcc_10_2_compatible_flags(monkeypatch, tmp_path):
    import cvas_gcc_dump
    from cvas_analysis import AnalysisOptions

    source_file = tmp_path / "model.c"
    source_file.write_text("CVAS_START\nint top(void) { return 1; }\nCVAS_END\n", encoding="utf-8")
    captured = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        return Result()

    monkeypatch.setattr(cvas_gcc_dump.shutil, "which", lambda compiler: f"/usr/bin/{compiler}")
    monkeypatch.setattr(cvas_gcc_dump.subprocess, "run", fake_run)

    metadata = cvas_gcc_dump.run_gcc_dump(
        source_file.read_text(encoding="utf-8"),
        source_path=source_file,
        analysis_options=AnalysisOptions(mode="full", clang_args=("-I", "include", "-DDEBUG=1")),
    )

    cmd = captured["cmd"]
    assert metadata["status"] == "ok"
    assert "-fdump-tree-cfg" in cmd
    assert "-fdiagnostics-format=json" not in cmd
    assert "-fanalyzer" not in cmd
    assert "-DCVAS_START=" in cmd
    assert "-DCVAS_END=" in cmd
    assert "-I" in cmd
    assert "include" in cmd
    assert "-DDEBUG=1" in cmd
    assert cmd[-1] == str(source_file.resolve())


def test_full_mode_malformed_compile_db_reports_gcc_dump_failure(tmp_path):
    source_file = tmp_path / "model.c"
    source_file.write_text(
        "CVAS_START\nint top(void) { return 1; }\nCVAS_END\n",
        encoding="utf-8",
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text("{bad json", encoding="utf-8")
    output_path = tmp_path / "output.json"

    result = run_cvas_process(
        source_file,
        output_path,
        extra_args=[
            "--analysis-mode",
            "full",
            "--clang-compile-db",
            str(compile_db),
        ],
    )

    assert result.returncode == 0, result.stderr
    model = json.loads(output_path.read_text(encoding="utf-8"))
    assert model["gcc_dump"]["status"] == "failed"
    assert any("compile" in line.lower() for line in model["gcc_dump"]["diagnostics"])


def test_full_mode_entry_tree_sitter_receives_entry_path_for_cpp(monkeypatch, tmp_path):
    import cvas_pipeline
    import cvas_source
    from cvas_analysis import AnalysisOptions
    from cvas_model import CycleRules

    source_file = tmp_path / "model.cpp"
    source = "CVAS_START\nint top(void) { return 1; }\nCVAS_END\n"
    source_file.write_text(source, encoding="utf-8")
    observed = {}

    def fake_tree_sitter(source, *, language=None, source_path=None, region_bounds=None):
        observed["language"] = language
        observed["source_path"] = source_path
        observed["region_bounds"] = region_bounds
        return [("int", "top", "void", "return 1;")]

    monkeypatch.setattr(
        cvas_source,
        "find_function_definitions_with_tree_sitter",
        fake_tree_sitter,
    )
    monkeypatch.setattr(
        cvas_pipeline,
        "run_gcc_dump",
        lambda *args, **kwargs: {
            "backend": "g++",
            "status": "ok",
            "language": "c++",
            "standard": "c++11",
            "dump_files": [],
            "diagnostics": [],
        },
    )

    model = cvas_pipeline.build_model(
        source,
        CycleRules(),
        entry_file=source_file,
        analysis_options=AnalysisOptions(mode="full"),
    )

    assert model["blocks"]
    assert observed["source_path"] == source_file


def test_full_mode_merges_tree_sitter_with_fallback_functions(monkeypatch):
    import cvas_source
    from cvas_analysis import AnalysisOptions

    def fake_tree_sitter(source, *, language=None, source_path=None, region_bounds=None):
        return [("int", "tree_func", "void", "return 1;")]

    monkeypatch.setattr(
        cvas_source,
        "find_function_definitions_with_tree_sitter",
        fake_tree_sitter,
    )

    functions = cvas_source.find_function_definitions(
        "int tree_func(void) { return 1; }\nint fallback_func(void) { return 2; }",
        analysis_options=AnalysisOptions(mode="full"),
        merge_fallback=True,
    )

    assert [name for _, name, _, _ in functions] == ["tree_func", "fallback_func"]


def test_full_mode_no_region_output_includes_analysis_metadata(monkeypatch):
    import cvas_pipeline
    from cvas_analysis import AnalysisOptions
    from cvas_model import CycleRules

    monkeypatch.setattr(
        cvas_pipeline,
        "run_gcc_dump",
        lambda *args, **kwargs: {
            "backend": "gcc",
            "status": "ok",
            "language": "c",
            "standard": "c11",
            "dump_files": [],
            "diagnostics": [],
        },
    )

    model = cvas_pipeline.build_model(
        "int top(void) { return 1; }",
        CycleRules(),
        entry_file=Path("model.c"),
        analysis_options=AnalysisOptions(mode="full"),
    )

    assert model["analysis_mode"] == "full"
    assert model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert model["gcc_dump"]["status"] == "ok"


def test_full_mode_empty_region_output_includes_analysis_metadata(monkeypatch):
    import cvas_pipeline
    from cvas_analysis import AnalysisOptions
    from cvas_model import CycleRules

    monkeypatch.setattr(
        cvas_pipeline,
        "run_gcc_dump",
        lambda *args, **kwargs: {
            "backend": "gcc",
            "status": "ok",
            "language": "c",
            "standard": "c11",
            "dump_files": [],
            "diagnostics": [],
        },
    )

    model = cvas_pipeline.build_model(
        "CVAS_START\nint not_a_definition;\nCVAS_END\n",
        CycleRules(),
        entry_file=Path("model.c"),
        analysis_options=AnalysisOptions(mode="full"),
    )

    assert model["analysis_mode"] == "full"
    assert model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert model["gcc_dump"]["status"] == "ok"


def test_full_mode_accepts_neutral_compile_arg_and_compile_db_aliases(tmp_path):
    project_root = tmp_path
    include_dir = project_root / "include"
    source_dir = project_root / "src"
    build_dir = project_root / "build"
    include_dir.mkdir()
    source_dir.mkdir()
    build_dir.mkdir()

    (include_dir / "defs.hpp").write_text(
        "struct Pixel { int value; };\n",
        encoding="utf-8",
    )
    source_file = source_dir / "model.cpp"
    source_file.write_text(
        '#include "defs.hpp"\n'
        "CVAS_START\n"
        "int top(int x) {\n"
        "    Pixel p = {x};\n"
        "    return p.value;\n"
        "}\n"
        "CVAS_END\n",
        encoding="utf-8",
    )
    compile_db = build_dir / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(project_root),
                    "arguments": [
                        "g++",
                        "-std=c++11",
                        "-Iinclude",
                        "-c",
                        "src/model.cpp",
                        "-o",
                        "build/model.o",
                    ],
                    "file": "src/model.cpp",
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = project_root / "output.json"

    model = run_cvas(
        source_file,
        output_path,
        extra_args=[
            "--analysis-mode",
            "full",
            "--compile-db",
            str(compile_db),
            "--compile-arg=-DLOCAL_ALIAS=1",
        ],
    )

    assert {block["block_name"] for block in model["blocks"]} == {"top"}
    assert model["gcc_dump"]["status"] == "ok"
    assert "-DLOCAL_ALIAS=1" in model["gcc_dump"]["command"]


def test_pycparser_normalization_handles_common_compiler_extensions():
    from c_ast_utils import parse_translation_unit

    source = """
#pragma once
_Static_assert(sizeof(int) >= 4, "int too small");
__extension__ typedef unsigned long long u64;
static __inline__ int add1(int *__restrict x) {
    asm volatile ("" : : "r"(*x));
    __asm__ volatile ("" : : "r"(*x));
    __asm__ __volatile__ ("" : : "r"(*x));
    *x += 1; __asm__ volatile ("" : : "r"(*x));
    return *x + 1;
}
"""

    parsed = parse_translation_unit(source)

    assert parsed is not None


def test_full_mode_preserves_cast_call_arguments():
    from cvas_analysis import AnalysisOptions
    from cvas_callgraph import find_function_calls

    calls, metadata = find_function_calls(
        "foo((Pixel)bar);",
        ["foo"],
        AnalysisOptions(mode="full"),
    )

    assert calls == [("foo", ["(Pixel)bar"], None)]
    assert metadata["parser"] in {"ast", "text"}


def test_full_mode_preserves_cast_conditions():
    from cvas_analysis import AnalysisOptions
    from cvas_passes import extract_for_condition, extract_keyword_condition

    options = AnalysisOptions(mode="full")

    assert (
        extract_keyword_condition("if ((Pixel)bar) { baz(); }", "if", options)
        == "(Pixel)bar"
    )
    assert (
        extract_keyword_condition("while ((Pixel)bar) { baz(); }", "while", options)
        == "(Pixel)bar"
    )
    assert (
        extract_for_condition("for (i = 0; (Pixel)bar; ++i) { baz(); }", options)
        == "(Pixel)bar"
    )


def test_resolved_clang_config_defaults_cpp11():
    from cvas_analysis import AnalysisOptions, resolve_clang_config

    config = resolve_clang_config(
        AnalysisOptions(mode="full"),
        source_path=Path("/tmp/example.cpp"),
    )

    assert config.language == "c++"
    assert config.standard == "c++11"


def test_resolved_clang_config_user_std_override_wins():
    from cvas_analysis import AnalysisOptions, resolve_clang_config

    config = resolve_clang_config(
        AnalysisOptions(mode="full", clang_args=("-std=c++17",)),
        source_path=Path("/tmp/example.cpp"),
    )

    assert config.standard == "c++17"


def test_full_mode_compile_db_auto_discovery_supplies_include_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        include_dir = project_root / "include"
        source_dir = project_root / "src"
        include_dir.mkdir()
        source_dir.mkdir()

        (include_dir / "defs.hpp").write_text(
            "struct Pixel { int value; };\n",
            encoding="utf-8",
        )
        source_file = source_dir / "model.cpp"
        source_file.write_text(
            '#include "defs.hpp"\n'
            "CVAS_START\n"
            "int top(int x) {\n"
            "    Pixel p = {x};\n"
            "    return p.value;\n"
            "}\n"
            "CVAS_END\n",
            encoding="utf-8",
        )
        (project_root / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(project_root),
                        "command": (
                            "g++ -std=c++11 -Iinclude -c src/model.cpp "
                            "-o CMakeFiles/model.o"
                        ),
                        "file": "src/model.cpp",
                    }
                ]
            ),
            encoding="utf-8",
        )

        output_path = project_root / "output.json"
        model = run_cvas(
            source_file,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert {block["block_name"] for block in model["blocks"]} == {"top"}
    assert model["gcc_dump"]["status"] == "ok"
    assert not any("defs.hpp" in line for line in model["gcc_dump"]["diagnostics"])


def test_full_mode_explicit_compile_db_path_is_honored():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        include_dir = project_root / "include"
        build_dir = project_root / "build"
        source_dir = project_root / "src"
        include_dir.mkdir()
        build_dir.mkdir()
        source_dir.mkdir()

        (include_dir / "defs.hpp").write_text(
            "struct Pixel { int value; };\n",
            encoding="utf-8",
        )
        source_file = source_dir / "model.cpp"
        source_file.write_text(
            '#include "defs.hpp"\n'
            "CVAS_START\n"
            "int top(int x) {\n"
            "    Pixel p = {x};\n"
            "    return p.value + 1;\n"
            "}\n"
            "CVAS_END\n",
            encoding="utf-8",
        )
        compile_db = build_dir / "compile_commands.json"
        compile_db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(project_root),
                        "arguments": [
                            "g++",
                            "-std=c++11",
                            "-Iinclude",
                            "-c",
                            "src/model.cpp",
                            "-o",
                            "build/model.o",
                        ],
                        "file": "src/model.cpp",
                    }
                ]
            ),
            encoding="utf-8",
        )

        output_path = project_root / "output.json"
        model = run_cvas(
            source_file,
            output_path,
            extra_args=[
                "--analysis-mode",
                "full",
                "--clang-compile-db",
                str(compile_db),
            ],
        )

    assert {block["block_name"] for block in model["blocks"]} == {"top"}
    assert model["gcc_dump"]["status"] == "ok"
    assert not any("defs.hpp" in line for line in model["gcc_dump"]["diagnostics"])


def test_full_mode_missing_required_include_records_gcc_dump_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        include_dir = project_root / "include"
        source_dir = project_root / "src"
        include_dir.mkdir()
        source_dir.mkdir()

        (include_dir / "defs.hpp").write_text(
            "struct Pixel { int value; };\n",
            encoding="utf-8",
        )
        source_file = source_dir / "model.cpp"
        source_file.write_text(
            '#include "defs.hpp"\n'
            "CVAS_START\n"
            "int top(int x) {\n"
            "    Pixel p = {x};\n"
            "    return p.value;\n"
            "}\n"
            "CVAS_END\n",
            encoding="utf-8",
        )

        output_path = project_root / "output.json"
        model = run_cvas(
            source_file,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert {block["block_name"] for block in model["blocks"]} == {"top"}
    assert model["gcc_dump"]["status"] == "failed"
    assert any("defs.hpp" in line for line in model["gcc_dump"]["diagnostics"])


def test_full_mode_accepts_header_entry_file_with_fast_plus_gcc_dump():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        source_file = project_root / "model.hpp"
        source_file.write_text(
            "CVAS_START\n"
            "inline int top(int x) { return x + 1; }\n"
            "CVAS_END\n",
            encoding="utf-8",
        )

        output_path = project_root / "output.json"
        model = run_cvas(
            source_file,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert model["analysis_backend"] == "tree-sitter+pycparser+gcc-dump"
    assert {block["block_name"] for block in model["blocks"]} == {"top"}
    assert model["gcc_dump"]["language"] == "c++"
