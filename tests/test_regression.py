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


def require_clang() -> None:
    from cvas_clang import ClangUnavailableError, ensure_clang_available

    try:
        ensure_clang_available()
    except ClangUnavailableError as exc:
        pytest.skip(str(exc))


def build_snapshot(model: Dict) -> Dict:
    """Build comparable snapshot from full model.

    Extracts only the stable fields we want to compare in broad regression tests.
    Schema v3 adds semantic signal fields that are covered by targeted tests, so
    the legacy signal endpoint shape is retained here to avoid noisy snapshots.
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

    legacy_signal_fields = [
        "source_id",
        "source_type",
        "destination_id",
        "destination_type",
        "signal_name",
        "direction",
        "comment",
    ]
    signals_snapshot = [
        {field: signal.get(field) for field in legacy_signal_fields if field in signal}
        for signal in model.get("signals", [])
    ]

    return {
        "operations": model.get("operations", []),
        "signals": signals_snapshot,
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
        # Schema contract fixtures keep full producer JSON as their expected
        # example for targeted v3 tests. Broad regression snapshots compare a
        # compact legacy subset, so do not reuse those full-contract examples.
        if rel_path.parts and rel_path.parts[0] == "schema":
            continue
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


def test_full_analysis_mode_reports_clang_backend():
    require_clang()
    fixture_c = FIXTURES_DIR / "minimal" / "single_add.c"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "output.json"
        model = run_cvas(
            fixture_c,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert model["analysis_mode"] == "full"
    assert model["analysis_backend"] == "clang"
    assert model["blocks"]


def test_full_analysis_mode_parses_cpp_methods():
    require_clang()
    fixture_cpp = FIXTURES_DIR / "cpp" / "class_methods.cpp"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "output.json"
        model = run_cvas(
            fixture_cpp,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    block_names = {block["block_name"] for block in model["blocks"]}
    assert model["analysis_backend"] == "clang"
    assert {"add1", "top"} <= block_names


def test_full_mode_preserves_cast_call_arguments():
    require_clang()

    from cvas_analysis import AnalysisOptions
    from cvas_callgraph import find_function_calls

    calls, metadata = find_function_calls(
        "foo((Pixel)bar);",
        ["foo"],
        AnalysisOptions(mode="full"),
    )

    assert calls == [("foo", ["(Pixel)bar"], None)]
    assert metadata["parser"] in {"ast", "clang", "text"}


def test_full_mode_preserves_cast_conditions():
    require_clang()

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
    require_clang()

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


def test_full_mode_explicit_compile_db_path_is_honored():
    require_clang()

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


def test_full_mode_missing_required_include_hard_fails():
    require_clang()

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
        result = run_cvas_process(
            source_file,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert result.returncode != 0
    assert "clang failed to parse entry translation unit" in result.stderr
    assert "defs.hpp" in result.stderr


def test_full_mode_rejects_header_entry_file():
    require_clang()

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
        result = run_cvas_process(
            source_file,
            output_path,
            extra_args=["--analysis-mode", "full"],
        )

    assert result.returncode != 0
    assert "does not accept header files" in result.stderr
