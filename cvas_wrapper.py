#!/usr/bin/env python3
import argparse
import shutil
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


def copy_viewer_assets(output_html: Path) -> None:
    """Copy required viewer assets next to the generated HTML."""
    repo_root = Path(__file__).parent
    src_elk = repo_root / "viewer" / "assets" / "elk.bundled.js"
    if not src_elk.exists():
        print(
            f"[cvas_wrapper] Warning: ELK asset not found at {src_elk}",
            file=sys.stderr,
        )
        return

    dst_assets = output_html.parent / "assets"
    dst_assets.mkdir(parents=True, exist_ok=True)
    dst_elk = dst_assets / "elk.bundled.js"
    if src_elk.resolve() == dst_elk.resolve():
        print(f"[cvas_wrapper] Asset already in place: {dst_elk}", file=sys.stderr)
        return
    shutil.copy2(src_elk, dst_elk)
    print(f"[cvas_wrapper] Copied asset: {dst_elk}", file=sys.stderr)


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
        copy_viewer_assets(output_html)
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "analysis.json"
        run_cvas(input_c, json_path, args.cvas_args)
        run_html(json_path, output_html)
        copy_viewer_assets(output_html)


if __name__ == "__main__":
    main()
