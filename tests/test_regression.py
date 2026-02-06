"""Regression tests for CVAS parser using snapshot comparison."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

CVAS_PARSER = Path(__file__).resolve().parents[1] / "src" / "cvas_mvp.py"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def run_cvas(input_c: Path, output_json: Path) -> Dict:
    """Run CVAS parser and return the resulting model.

    Args:
        input_c: Path to input C file
        output_json: Path where JSON output will be written

    Returns:
        Parsed JSON model as dict

    Raises:
        RuntimeError: If CVAS parser fails
    """
    cmd = [
        sys.executable,
        str(CVAS_PARSER),
        str(input_c),
        "-o",
        str(output_json),
    ]

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
