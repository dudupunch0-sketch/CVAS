from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from cvas_analysis import AnalysisOptions
from cvas_source import (
    find_function_definitions,
    split_top_level_commas,
    strip_comments_and_strings,
)


def collect_project_sources(
    project_root: Path, extensions: Iterable[str], entry_file: Path
) -> List[Tuple[Path, str]]:
    """Collect source files under project root with deterministic ordering."""
    ext_set = {f".{ext.strip().lstrip('.').lower()}" for ext in extensions if ext.strip()}
    files: List[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in ext_set:
            files.append(path)

    files = sorted(set(files))
    if entry_file in files:
        files.remove(entry_file)
    ordered = [entry_file] + files

    sources: List[Tuple[Path, str]] = []
    for path in ordered:
        try:
            sources.append((path, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            print(f"WARNING: Failed to read source file: {path}", file=sys.stderr)
    return sources


def build_project_symbol_index(
    project_sources: List[Tuple[Path, str]],
    analysis_options: AnalysisOptions = AnalysisOptions(),
) -> Tuple[
    Dict[str, Tuple[str, str, str, str, str]],
    List[Dict[str, object]],
    Dict[str, Dict[str, object]],
]:
    """Build function/global/macro index across project files."""
    function_defs: Dict[str, Tuple[str, str, str, str, str]] = {}
    duplicate_functions: List[Dict[str, object]] = []
    symbol_index: Dict[str, Dict[str, object]] = {}

    for path, source in project_sources:
        rel_path = str(path)
        functions = find_function_definitions(
            source,
            analysis_options=analysis_options,
            source_path=path,
        )
        for ret, name, params, body in functions:
            if name in function_defs:
                duplicate_functions.append(
                    {
                        "name": name,
                        "existing_file": function_defs[name][4],
                        "duplicate_file": rel_path,
                    }
                )
                continue
            function_defs[name] = (ret, name, params, body, rel_path)

        for macro_name, macro_value, line in scan_macros(source):
            symbol_index.setdefault(
                macro_name,
                {
                    "name": macro_name,
                    "kind": "macro",
                    "file": rel_path,
                    "line": line,
                    "value": macro_value,
                },
            )

        for global_name, line in scan_global_symbols(source):
            symbol_index.setdefault(
                global_name,
                {
                    "name": global_name,
                    "kind": "global",
                    "file": rel_path,
                    "line": line,
                },
            )

    return function_defs, duplicate_functions, symbol_index


def scan_macros(source: str) -> List[Tuple[str, str, int]]:
    """Scan simple #define macros."""
    results: List[Tuple[str, str, int]] = []
    define_pattern = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\b(.*)$")
    for idx, line in enumerate(source.splitlines(), start=1):
        match = define_pattern.match(line)
        if not match:
            continue
        results.append((match.group(1), match.group(2).strip(), idx))
    return results


def scan_global_symbols(source: str) -> List[Tuple[str, int]]:
    """Scan probable top-level global symbols from declarations."""
    results: List[Tuple[str, int]] = []
    cleaned = strip_comments_and_strings(source)
    lines = cleaned.splitlines()
    depth = 0
    decl_buffer = ""
    decl_start = 1

    for idx, line in enumerate(lines, start=1):
        for char in line:
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(depth - 1, 0)

        if depth != 0:
            continue
        if line.lstrip().startswith("#"):
            continue

        segment = line.strip()
        if not segment:
            continue
        if not decl_buffer:
            decl_start = idx
        decl_buffer = f"{decl_buffer} {segment}".strip()
        if ";" not in segment:
            continue

        stmt = decl_buffer.split(";", 1)[0]
        decl_buffer = ""
        if "(" in stmt:
            continue
        names = extract_decl_names(stmt)
        for name in names:
            results.append((name, decl_start))

    return results


def extract_decl_names(statement: str) -> List[str]:
    """Extract probable declared variable names from a declaration statement."""
    if not statement:
        return []

    type_prefix = re.compile(
        r"^\s*(?:static\s+|extern\s+|const\s+|volatile\s+|register\s+|unsigned\s+|signed\s+|long\s+|short\s+)*"
        r"(?:void|char|int|float|double|bool|size_t|ssize_t|u?int\d+_t|struct\s+\w+|enum\s+\w+)\b"
    )
    match = type_prefix.match(statement)
    if not match:
        return []

    tail = statement[match.end() :].strip()
    if not tail:
        return []

    names: List[str] = []
    for part in split_top_level_commas(tail):
        candidate = part.split("=", 1)[0].strip()
        candidate = re.sub(r"\[[^\]]*\]", "", candidate)
        candidate = candidate.replace("*", " ").strip()
        if not candidate:
            continue
        token = candidate.split()[-1]
        if re.match(r"^[A-Za-z_]\w*$", token):
            names.append(token)
    return names
