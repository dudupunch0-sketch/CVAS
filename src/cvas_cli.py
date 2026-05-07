#!/usr/bin/env python3
"""CLI front-end for CVAS analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from cvas_analysis import AnalysisOptions
from cvas_clang import ClangParseError
from cvas_model import CycleRules
from cvas_pipeline import build_model
from cvas_index import collect_project_sources


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="CVAS - C-model block diagram parser with advanced analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s model.c -o output.json
  %(prog)s model.c --cycle-config cycle.json
  %(prog)s model.c --add-per-cycle 8 --mul-per-cycle 2
  %(prog)s model.c --analysis-mode full --compile-arg=-Iinclude
        """,
    )

    parser.add_argument("input", type=Path, help="Path to entry C/C++ source file")
    parser.add_argument(
        "-o", "--output", type=Path, help="Output JSON path (default: stdout)"
    )
    parser.add_argument("--cycle-config", type=Path, help="JSON file with cycle rules")
    parser.add_argument(
        "--add-per-cycle", type=int, help="Override add operations per cycle"
    )
    parser.add_argument(
        "--compare-per-cycle", type=int, help="Override compare operations per cycle"
    )
    parser.add_argument(
        "--logic-per-cycle", type=int, help="Override logic operations per cycle"
    )
    parser.add_argument(
        "--mul-per-cycle", type=int, help="Override multiply operations per cycle"
    )
    parser.add_argument(
        "--copy-per-cycle", type=int, help="Override copy operations per cycle"
    )
    parser.add_argument(
        "--shift-per-cycle", type=int, help="Override shift operations per cycle"
    )
    parser.add_argument(
        "--bitwise-per-cycle", type=int, help="Override bitwise operations per cycle"
    )
    parser.add_argument(
        "--const-per-cycle", type=int, help="Override const operations per cycle"
    )
    parser.add_argument(
        "--load-per-cycle", type=int, help="Override load operations per cycle"
    )
    parser.add_argument(
        "--store-per-cycle", type=int, help="Override store operations per cycle"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Project root for multi-file indexing/resolution",
    )
    parser.add_argument(
        "--entry-file",
        type=Path,
        help="Entry file inside project root (default: positional input)",
    )
    parser.add_argument(
        "--source-extensions",
        default="c,cc,cpp,h,hpp",
        help="Comma-separated extensions to index in project mode",
    )
    parser.add_argument(
        "--analysis-mode",
        choices=["fast", "full"],
        default="fast",
        help=(
            "Select pycparser fast analysis or full analysis with optional "
            "tree-sitter structure parsing plus GCC dump metadata"
        ),
    )
    parser.add_argument(
        "--clang-arg",
        "--compile-arg",
        dest="compile_arg",
        action="append",
        default=[],
        help=(
            "Compile argument for full analysis; "
            "include/define/std flags are reused by GCC dump metadata"
        ),
    )
    parser.add_argument(
        "--language",
        choices=["c", "c++"],
        help="Override source language used by full analysis and GCC dump metadata",
    )
    parser.add_argument(
        "--clang-compile-db",
        "--compile-db",
        dest="compile_db",
        type=Path,
        help=(
            "Path to compile_commands.json used to reconstruct "
            "include/define/std flags for full analysis"
        ),
    )
    parser.add_argument(
        "--function-io",
        type=Path,
        help="Optional function_io.json to embed under flow.function_io for schema v3 Sequence rendering",
    )

    return parser.parse_args()


def _load_cycle_rules(args: argparse.Namespace) -> CycleRules:
    rules = CycleRules()

    if args.cycle_config:
        if not args.cycle_config.exists():
            print(
                f"ERROR: Cycle config file '{args.cycle_config}' not found",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            rules = CycleRules.from_json(args.cycle_config)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"ERROR: Invalid cycle config: {e}", file=sys.stderr)
            sys.exit(1)

    overrides = [
        ("add_per_cycle", "add_per_cycle"),
        ("compare_per_cycle", "compare_per_cycle"),
        ("logic_per_cycle", "logic_per_cycle"),
        ("mul_per_cycle", "mul_per_cycle"),
        ("copy_per_cycle", "copy_per_cycle"),
        ("shift_per_cycle", "shift_per_cycle"),
        ("bitwise_per_cycle", "bitwise_per_cycle"),
        ("const_per_cycle", "const_per_cycle"),
        ("load_per_cycle", "load_per_cycle"),
        ("store_per_cycle", "store_per_cycle"),
    ]
    for attr, rule_attr in overrides:
        value = getattr(args, attr)
        if value is not None:
            setattr(rules, rule_attr, value)

    try:
        rules.validate()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    return rules


def _load_source(args: argparse.Namespace) -> tuple[Path, str]:
    entry_file = args.entry_file if args.entry_file else args.input
    if not entry_file.exists():
        print(f"ERROR: Input file '{entry_file}' not found", file=sys.stderr)
        sys.exit(1)

    try:
        source = entry_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        print(f"ERROR: Failed to read input file: {e}", file=sys.stderr)
        sys.exit(1)

    return entry_file, source


def _load_project_sources(
    args: argparse.Namespace, entry_file: Path
) -> Optional[List[Tuple[Path, str]]]:
    if not args.project_root:
        return None

    if not args.project_root.exists() or not args.project_root.is_dir():
        print(
            f"ERROR: Project root '{args.project_root}' not found or not a directory",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_entry = entry_file.resolve()
    resolved_root = args.project_root.resolve()
    if not str(resolved_entry).startswith(str(resolved_root)):
        print(
            "ERROR: Entry file must be under --project-root in project mode",
            file=sys.stderr,
        )
        sys.exit(1)

    extensions = [ext.strip() for ext in args.source_extensions.split(",")]
    return collect_project_sources(resolved_root, extensions, resolved_entry)


def _load_analysis_options(
    args: argparse.Namespace, entry_file: Path
) -> AnalysisOptions:
    options = AnalysisOptions.from_values(
        mode=args.analysis_mode,
        clang_args=args.compile_arg,
        language_override=args.language,
        compile_db_path=(
            str(args.compile_db.resolve()) if args.compile_db else None
        ),
        project_root=(str(args.project_root.resolve()) if args.project_root else None),
    )
    return options


def _load_function_io(args: argparse.Namespace) -> Optional[dict]:
    if not args.function_io:
        return None
    if not args.function_io.exists():
        print(f"ERROR: Function IO file '{args.function_io}' not found", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(args.function_io.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: Invalid function IO file: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(payload, dict):
        print("ERROR: Function IO file must contain a JSON object", file=sys.stderr)
        sys.exit(1)
    return payload


def main() -> None:
    """Main CLI entry point."""
    args = parse_args()
    rules = _load_cycle_rules(args)
    entry_file, source = _load_source(args)
    analysis_options = _load_analysis_options(args, entry_file)
    project_sources = _load_project_sources(args, entry_file)
    function_io = _load_function_io(args)

    if project_sources:
        print(
            f"Building enhanced model with {analysis_options.backend} backend "
            f"and project indexing ({len(project_sources)} files)...",
            file=sys.stderr,
        )
    else:
        print(
            f"Building enhanced model with P1+P2 analysis "
            f"({analysis_options.backend} backend)...",
            file=sys.stderr,
        )

    try:
        model = build_model(
            source,
            rules,
            project_sources=project_sources,
            entry_file=entry_file,
            analysis_options=analysis_options,
            function_io=function_io,
        )
    except ClangParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    output = json.dumps(model, indent=2, ensure_ascii=False)

    if args.output:
        try:
            args.output.write_text(output, encoding="utf-8")
            print(
                f"✓ Analysis complete. Output written to {args.output}", file=sys.stderr
            )
            num_blocks = len(model.get("blocks", []))
            num_ops = len(model.get("operations", []))
            num_signals = len(model.get("signals", []))

            print(f"✓ Analyzed {num_blocks} functions", file=sys.stderr)
            print(f"✓ Extracted {num_ops} operations", file=sys.stderr)
            print(f"✓ Tracked {num_signals} data flows", file=sys.stderr)

            flow = model.get("flow", {})
            if "call_graph" in flow:
                cg = flow["call_graph"]
                print(
                    f"✓ Call graph: {len(cg.get('nodes', {}))} nodes, "
                    f"depth {cg.get('max_depth', 0)}",
                    file=sys.stderr,
                )
                if cg.get("critical_path"):
                    print(
                        f"✓ Critical path: {' → '.join(cg['critical_path'])}",
                        file=sys.stderr,
                    )
        except IOError as e:
            print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output)


if __name__ == "__main__":
    main()
