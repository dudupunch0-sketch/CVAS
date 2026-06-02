from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from c_ast_utils import parse_translation_unit
from cvas_analysis import AnalysisOptions
from cvas_clang import find_function_definitions_with_clang
from cvas_treesitter import find_function_definitions_with_tree_sitter

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
    bounds = find_cvas_region_bounds(source)
    if bounds is None:
        return "", False
    start_index, end_index = bounds
    return source[start_index:end_index], True


def find_cvas_region_bounds(source: str) -> Optional[Tuple[int, int]]:
    """Return the source offsets for the CVAS region contents."""
    start_index = source.find(MARKER_START)
    end_index = source.find(MARKER_END)

    if start_index == -1 or end_index == -1:
        return None

    if end_index <= start_index:
        print(f"WARNING: {MARKER_END} appears before {MARKER_START}", file=sys.stderr)
        return None

    return start_index + len(MARKER_START), end_index


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
    name_match = re.search(
        r"((?:[A-Za-z_]\w*\s*::\s*)*(?:~\s*)?[A-Za-z_]\w*)\s*$",
        source[: idx + 1],
    )
    if not name_match:
        return None
    name = re.sub(r"\s*::\s*", "::", name_match.group(1).strip())
    name = re.sub(r"~\s+", "~", name)
    name_start = name_match.start(1)
    return name, name_start


def _contains_standalone_colon(segment: str) -> bool:
    for idx, char in enumerate(segment):
        if char != ":":
            continue
        prev_char = segment[idx - 1] if idx > 0 else ""
        next_char = segment[idx + 1] if idx + 1 < len(segment) else ""
        if prev_char != ":" and next_char != ":":
            return True
    return False


def _is_function_definition_suffix(segment: str) -> bool:
    suffix = " ".join(segment.strip().split())
    if not suffix:
        return True
    if suffix.startswith(":"):
        return ";" not in suffix
    suffix = re.sub(r"\b(?:const|volatile|noexcept|override|final)\b", " ", suffix)
    suffix = suffix.replace("&", " ")
    suffix = suffix.strip()
    if not suffix:
        return True
    return suffix.startswith("->")


def _find_class_scopes(source: str) -> List[Tuple[str, int, int]]:
    scopes: List[Tuple[str, int, int]] = []
    class_pattern = re.compile(r"\b(?:class|struct)\s+([A-Za-z_]\w*)[^;{}]*\{")
    for match in class_pattern.finditer(source):
        brace_index = source.rfind("{", match.start(), match.end())
        if brace_index == -1:
            continue
        _, end_index = extract_brace_block(source, brace_index)
        if end_index == brace_index:
            continue
        scopes.append((match.group(1), brace_index, end_index))
    return scopes


def _class_scope_for_index(
    scopes: List[Tuple[str, int, int]],
    index: int,
) -> Optional[str]:
    matching = [
        (start, name)
        for name, start, end in scopes
        if start < index < end
    ]
    if not matching:
        return None
    return max(matching)[1]


def _find_signature_before_brace(
    source: str,
    brace_index: int,
) -> Optional[Tuple[str, int, int, int]]:
    search_end = brace_index
    while search_end > 0:
        close_paren = source.rfind(")", 0, search_end)
        if close_paren == -1:
            return None
        open_paren = _find_matching_paren(source, close_paren)
        if open_paren is None:
            search_end = close_paren
            continue
        name_info = _extract_name_before_paren(source, open_paren)
        if not name_info:
            search_end = open_paren
            continue

        name, name_start = name_info
        header_start = max(
            source.rfind(";", 0, name_start),
            source.rfind("}", 0, name_start),
            source.rfind("{", 0, name_start),
        )
        if _contains_standalone_colon(source[header_start + 1 : name_start]):
            search_end = open_paren
            continue

        suffix = _strip_attributes_between(source, close_paren + 1, brace_index)
        if _is_function_definition_suffix(suffix):
            return name, name_start, open_paren, close_paren
        search_end = open_paren
    return None


def _find_function_definitions_regex(source: str) -> List[Tuple[str, str, str, str]]:
    functions = []
    cleaned = strip_comments_and_strings(source)
    class_scopes = _find_class_scopes(cleaned)

    for brace_index, char in enumerate(cleaned):
        if char != "{":
            continue
        signature = _find_signature_before_brace(cleaned, brace_index)
        if signature is None:
            continue
        name, name_start, open_paren, close_paren = signature
        if name in KEYWORDS:
            continue
        class_scope = _class_scope_for_index(class_scopes, name_start)
        if class_scope and "::" not in name:
            name = f"{class_scope}::{name}"
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
    source: str,
    analysis_options: AnalysisOptions = AnalysisOptions(),
    *,
    source_path: Optional[Path] = None,
    region_bounds: Optional[Tuple[int, int]] = None,
    required: bool = False,
    merge_fallback: bool = False,
) -> List[Tuple[str, str, str, str]]:
    """Find all function definitions in source code."""
    tree_sitter_functions: List[Tuple[str, str, str, str]] = []
    if analysis_options.mode == "full":
        tree_sitter_functions = find_function_definitions_with_tree_sitter(
            source,
            language=analysis_options.language_override,
            source_path=source_path,
            region_bounds=region_bounds,
        )

    if analysis_options.backend == "clang":
        functions = find_function_definitions_with_clang(
            source,
            analysis_options=analysis_options,
            source_path=source_path,
            region_bounds=region_bounds,
            required=required,
        )
        if functions or required or source_path is not None or region_bounds is not None:
            return functions

    parsed = parse_translation_unit(source)
    if parsed is None:
        fallback_functions = _find_function_definitions_regex(source)
        return _merge_function_definitions(
            tree_sitter_functions, fallback_functions, merge_fallback=merge_fallback
        )

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
            fallback_functions = _find_function_definitions_regex(source)
            return _merge_function_definitions(
                tree_sitter_functions, fallback_functions, merge_fallback=merge_fallback
            )
        functions.append((ret, name, params, body))

    return _merge_function_definitions(
        tree_sitter_functions, functions, merge_fallback=merge_fallback
    )


def _merge_function_definitions(
    preferred: List[Tuple[str, str, str, str]],
    fallback: List[Tuple[str, str, str, str]],
    *,
    merge_fallback: bool = True,
) -> List[Tuple[str, str, str, str]]:
    """Merge structural parser results with fallback results by function name."""
    if not preferred:
        return fallback
    if not merge_fallback:
        return preferred
    merged = list(preferred)
    seen = {name for _, name, _, _ in preferred}
    for function in fallback:
        _, name, _, _ = function
        if name in seen:
            continue
        merged.append(function)
        seen.add(name)
    return merged
