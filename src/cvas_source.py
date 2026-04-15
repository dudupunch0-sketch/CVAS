from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

from c_ast_utils import parse_translation_unit
from cvas_analysis import AnalysisOptions
from cvas_clang import find_function_definitions_with_clang

MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"

KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "do",
    "else",
    "break",
    "continue",
}


def extract_cvas_region(source: str) -> Tuple[str, bool]:
    """Extract code between CVAS_START and CVAS_END markers."""
    start_index = source.find(MARKER_START)
    end_index = source.find(MARKER_END)

    if start_index == -1 or end_index == -1:
        return "", False

    if end_index <= start_index:
        print(f"WARNING: {MARKER_END} appears before {MARKER_START}", file=sys.stderr)
        return "", False

    return source[start_index + len(MARKER_START) : end_index], True


def strip_comments_and_strings(source: str) -> str:
    """Remove comments and string literals while preserving positions."""

    def replacer(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    pattern = re.compile(
        r"//.*?$" r"|/\*.*?\*/" r"|\"(\\.|[^\\\"])*\"" r"|'(\\.|[^\\'])*'",
        re.DOTALL | re.MULTILINE,
    )
    return re.sub(pattern, replacer, source)


def extract_brace_block(source: str, start_index: int) -> Tuple[Optional[str], int]:
    """Extract content within matching braces."""
    if start_index >= len(source) or source[start_index] != "{":
        return None, start_index

    depth = 0
    for idx in range(start_index, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[start_index + 1 : idx], idx + 1

    return None, start_index


def split_top_level_commas(text: str) -> List[str]:
    """Split a string by commas at the top level (ignoring nested parens)."""
    parts = []
    depth = 0
    start = 0
    for idx, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(text[start:idx])
            start = idx + 1
    parts.append(text[start:])
    return parts


def _compute_line_starts(source: str) -> List[int]:
    starts = [0]
    for idx, char in enumerate(source):
        if char == "\n":
            starts.append(idx + 1)
    return starts


def _find_function_body_from_coord(
    cleaned_source: str, coord_line: int, coord_column: int
) -> Optional[str]:
    if coord_line <= 0 or coord_column <= 0:
        return None
    line_starts = _compute_line_starts(cleaned_source)
    if coord_line - 1 >= len(line_starts):
        return None
    start_index = line_starts[coord_line - 1] + coord_column - 1
    brace_index = cleaned_source.find("{", start_index)
    if brace_index == -1:
        return None
    body, _ = extract_brace_block(cleaned_source, brace_index)
    return body


def _find_matching_paren(source: str, close_index: int) -> Optional[int]:
    depth = 0
    for idx in range(close_index, -1, -1):
        if source[idx] == ")":
            depth += 1
        elif source[idx] == "(":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _strip_attributes_between(source: str, start: int, end: int) -> str:
    segment = source[start:end]
    segment = re.sub(
        r"__attribute__\s*\(\((?:.|\n)*?\)\)", " ", segment, flags=re.DOTALL
    )
    segment = re.sub(r"__declspec\s*\([^)]*\)", " ", segment, flags=re.DOTALL)
    return segment


def _extract_name_before_paren(
    source: str, open_paren: int
) -> Optional[Tuple[str, int]]:
    idx = open_paren - 1
    while idx >= 0 and source[idx].isspace():
        idx -= 1
    if idx < 0:
        return None
    if source[idx] == ")":
        open_idx = _find_matching_paren(source, idx)
        if open_idx is None:
            return None
        name_match = re.search(r"[A-Za-z_]\w*", source[open_idx:idx])
        if not name_match:
            return None
        name = name_match.group(0)
        name_start = open_idx + name_match.start()
        return name, name_start
    name_match = re.search(r"[A-Za-z_]\w*$", source[: idx + 1])
    if not name_match:
        return None
    name = name_match.group(0)
    name_start = name_match.start()
    return name, name_start


def _find_function_definitions_regex(source: str) -> List[Tuple[str, str, str, str]]:
    functions = []
    cleaned = strip_comments_and_strings(source)

    for brace_index, char in enumerate(cleaned):
        if char != "{":
            continue
        close_paren = cleaned.rfind(")", 0, brace_index)
        if close_paren == -1:
            continue
        between = _strip_attributes_between(cleaned, close_paren + 1, brace_index)
        if between.strip():
            continue
        open_paren = _find_matching_paren(cleaned, close_paren)
        if open_paren is None:
            continue
        name_info = _extract_name_before_paren(cleaned, open_paren)
        if not name_info:
            continue
        name, name_start = name_info
        if name in KEYWORDS:
            continue
        header_start = max(
            cleaned.rfind(";", 0, name_start),
            cleaned.rfind("}", 0, name_start),
            cleaned.rfind("{", 0, name_start),
        )
        ret = " ".join(cleaned[header_start + 1 : name_start].split())
        params = cleaned[open_paren + 1 : close_paren].strip()
        body, _ = extract_brace_block(cleaned, brace_index)
        if body is None:
            continue
        functions.append((ret, name, params, body))

    return functions


def find_function_definitions(
    source: str, analysis_options: AnalysisOptions = AnalysisOptions()
) -> List[Tuple[str, str, str, str]]:
    """Find all function definitions in source code."""
    if analysis_options.mode == "full":
        functions = find_function_definitions_with_clang(
            source, clang_args=analysis_options.clang_args
        )
        if functions:
            return functions

    parsed = parse_translation_unit(source)
    if parsed is None:
        return _find_function_definitions_regex(source)

    pycparser_module, ast, generator, normalized = parsed
    cleaned = strip_comments_and_strings(normalized)
    functions = []

    for ext in ast.ext:
        if not isinstance(ext, pycparser_module.c_ast.FuncDef):
            continue
        name = ext.decl.name
        if name in KEYWORDS:
            continue
        func_type = ext.decl.type
        ret = " ".join(generator.visit(func_type.type).split())
        params = generator.visit(func_type.args) if func_type.args else ""
        coord = ext.decl.coord
        body = None
        if coord is not None:
            body = _find_function_body_from_coord(cleaned, coord.line, coord.column)
        if body is None:
            return _find_function_definitions_regex(source)
        functions.append((ret, name, params, body))

    return functions
