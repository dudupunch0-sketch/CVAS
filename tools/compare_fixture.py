#!/usr/bin/env python3
"""Compare CVAS fixture output against stored JSON snapshots."""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

REPO_ROOT = Path(__file__).resolve().parents[1]
CVAS_SCRIPT = REPO_ROOT / "src" / "cvas_mvp.py"


def extract_snapshot(model: dict) -> dict:
    cfg = {}
    for block in model.get("blocks", []):
        if block.get("cfg"):
            cfg[block["block_name"]] = block["cfg"]

    return {
        "operations": model.get("operations", []),
        "signals": model.get("signals", []),
        "cfg": cfg,
        "call_graph": model.get("flow", {}).get("call_graph"),
    }


def build_model(fixture: Path) -> dict:
    if not CVAS_SCRIPT.exists():
        raise FileNotFoundError(f"Missing CVAS script: {CVAS_SCRIPT}")

    with NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        output_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [sys.executable, str(CVAS_SCRIPT), str(fixture), "-o", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "CVAS run failed:\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return json.loads(output_path.read_text(encoding="utf-8"))
    finally:
        output_path.unlink(missing_ok=True)


def compare_snapshot(expected_path: Path, actual_snapshot: dict) -> bool:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    if expected == actual_snapshot:
        return True

    expected_dump = json.dumps(expected, indent=2, ensure_ascii=False, sort_keys=True).splitlines()
    actual_dump = json.dumps(actual_snapshot, indent=2, ensure_ascii=False, sort_keys=True).splitlines()

    diff = "\n".join(
        difflib.unified_diff(
            expected_dump,
            actual_dump,
            fromfile=str(expected_path),
            tofile="actual",
            lineterm="",
        )
    )
    print(diff)
    return False


def compare_fixture(fixture: Path, expected_path: Path) -> bool:
    model = build_model(fixture)
    snapshot = extract_snapshot(model)
    return compare_snapshot(expected_path, snapshot)


def iter_fixtures(fixtures_path: Path):
    if fixtures_path.is_file():
        expected_path = fixtures_path.with_suffix(".expected.json")
        return [(fixtures_path, expected_path)]

    if not fixtures_path.is_dir():
        raise FileNotFoundError(f"Fixture path not found: {fixtures_path}")

    return [
        (fixture, fixture.with_suffix(".expected.json"))
        for fixture in sorted(fixtures_path.glob("*.c"))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare CVAS fixtures against snapshots")
    parser.add_argument(
        "fixtures",
        type=Path,
        help="Fixture .c file or directory containing .c fixtures",
    )
    args = parser.parse_args()

    fixtures = iter_fixtures(args.fixtures)
    if not fixtures:
        print(f"No fixtures found in {args.fixtures}")
        return 1

    failures = 0
    for fixture, expected_path in fixtures:
        if not expected_path.exists():
            print(f"Missing expected snapshot: {expected_path}")
            failures += 1
            continue

        print(f"Comparing {fixture} -> {expected_path}")
        if not compare_fixture(fixture, expected_path):
            print(f"Mismatch for {fixture}")
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
