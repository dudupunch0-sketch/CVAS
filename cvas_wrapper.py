#!/usr/bin/env python3
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def run_cvas(input_c: Path, output_json: Path, extra_args: list[str]) -> None:
    cmd = [sys.executable, str(Path(__file__).parent / "src" / "cvas_mvp.py"), str(input_c), "-o", str(output_json)]
    cmd.extend(extra_args)
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)


def run_html(input_json: Path, output_html: Path) -> None:
    cmd = [sys.executable, str(Path(__file__).parent / "json_to_html.py"), str(input_json), str(output_html)]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CVAS analysis and emit a standalone HTML viewer.")
    parser.add_argument("input_c", help="Input C source file")
    parser.add_argument("output_html", help="Output HTML file")
    parser.add_argument("--output-json", help="Optional path to also write the intermediate JSON")
    parser.add_argument("--cvas-args", nargs=argparse.REMAINDER, default=[], help="Extra arguments passed to cvas_mvp.py")
    args = parser.parse_args()

    input_c = Path(args.input_c)
    output_html = Path(args.output_html)
    if args.output_json:
        output_json = Path(args.output_json)
        run_cvas(input_c, output_json, args.cvas_args)
        run_html(output_json, output_html)
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "analysis.json"
        run_cvas(input_c, json_path, args.cvas_args)
        run_html(json_path, output_html)


if __name__ == "__main__":
    main()
