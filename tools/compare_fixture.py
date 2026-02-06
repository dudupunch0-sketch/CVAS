#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

def run_cvas(input_c: Path, output_json: Path) -> None:
    cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / "src" / "cvas_mvp.py"), str(input_c), "-o", str(output_json)]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)

def build_snapshot(model: dict) -> dict:
    cfg_snapshot = []
    for block in model.get("blocks", []):
        cfg = block.get("cfg")
        if cfg:
            cfg_snapshot.append({"block_name": block.get("block_name"), "cfg": cfg})
    return {
        "operations": model.get("operations", []),
        "signals": model.get("signals", []),
        "cfg": cfg_snapshot,
        "call_graph": model.get("flow", {}).get("call_graph"),
    }

def normalize_json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)

def main() -> int:
    parser = argparse.ArgumentParser(description="Compare CVAS fixture output with expected snapshot.")
    parser.add_argument("fixture_c", type=Path, help="Path to C fixture")
    parser.add_argument("expected_json", type=Path, help="Path to expected snapshot JSON")
    parser.add_argument("--update", action="store_true", help="Update expected snapshot")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "output.json"
        run_cvas(args.fixture_c, output_path)
        model = json.loads(output_path.read_text())

    snapshot = build_snapshot(model)

    if args.update:
        args.expected_json.write_text(normalize_json(snapshot) + "\n")
        print(f"Updated {args.expected_json}")
        return 0

    if not args.expected_json.exists():
        print(f"Expected snapshot not found: {args.expected_json}")
        return 1

    expected = json.loads(args.expected_json.read_text())

    expected_norm = normalize_json(expected)
    actual_norm = normalize_json(snapshot)

    if expected_norm == actual_norm:
        print("Fixture matches expected snapshot.")
        return 0

    import difflib

    diff = difflib.unified_diff(
        expected_norm.splitlines(),
        actual_norm.splitlines(),
        fromfile=str(args.expected_json),
        tofile="actual",
        lineterm="",
    )
    print("\n".join(diff))
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
