from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from cvas_analysis import infer_language_from_path


def _extract_brace_block(source: str, start_index: int) -> Tuple[Optional[str], int]:
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


def _load_parser(language: str):
    """Return a tree-sitter parser for C or C++ if optional deps are installed."""
    try:
        from tree_sitter import Language, Parser
    except Exception:
        return None

    module_name = "tree_sitter_cpp" if language == "c++" else "tree_sitter_c"
    try:
        grammar_module = __import__(module_name)
    except Exception:
        return None

    try:
        language_capsule = grammar_module.language()
        ts_language = Language(language_capsule)
    except Exception:
        try:
            ts_language = grammar_module.language()
        except Exception:
            return None

    parser = Parser()
    try:
        parser.language = ts_language
    except Exception:
        try:
            parser.set_language(ts_language)
        except Exception:
            return None
    return parser


def _node_text(source_bytes: bytes, node) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node):
    yield node
    for child in getattr(node, "children", []) or []:
        yield from _walk(child)


def _find_descendant(node, node_type: str):
    for candidate in _walk(node):
        if candidate.type == node_type:
            return candidate
    return None


def _extract_function_name(declarator, source_bytes: bytes) -> Optional[str]:
    direct = declarator.child_by_field_name("declarator")
    if direct is not None:
        name = _extract_function_name(direct, source_bytes)
        if name:
            return name
    name_node = declarator.child_by_field_name("name")
    if name_node is not None:
        return _node_text(source_bytes, name_node).strip()
    for candidate in _walk(declarator):
        if candidate.type in {"identifier", "field_identifier"}:
            return _node_text(source_bytes, candidate).strip()
    return None


def find_function_definitions_with_tree_sitter(
    source: str,
    *,
    language: Optional[str] = None,
    source_path: Optional[Path] = None,
    region_bounds: Optional[Tuple[int, int]] = None,
) -> List[Tuple[str, str, str, str]]:
    """Find function definitions with optional tree-sitter C/C++ grammars.

    Returns [] when tree-sitter is unavailable or cannot parse useful functions.
    """
    resolved_language = language or infer_language_from_path(source_path) or "c"
    parser = _load_parser(resolved_language)
    if parser is None:
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    functions: List[Tuple[str, str, str, str]] = []
    lower_bound = region_bounds[0] if region_bounds else None
    upper_bound = region_bounds[1] if region_bounds else None

    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        if lower_bound is not None and node.end_byte < lower_bound:
            continue
        if upper_bound is not None and node.start_byte > upper_bound:
            continue

        declarator = node.child_by_field_name("declarator")
        body_node = node.child_by_field_name("body")
        if declarator is None or body_node is None:
            continue
        name = _extract_function_name(declarator, source_bytes)
        if not name:
            continue

        params_node = _find_descendant(declarator, "parameter_list")
        params = ""
        if params_node is not None:
            params_text = _node_text(source_bytes, params_node).strip()
            params = params_text[1:-1].strip() if params_text.startswith("(") else params_text

        header = _node_text(source_bytes, node).split("{", 1)[0]
        name_index = header.rfind(name)
        ret = " ".join(header[:name_index].split()) if name_index >= 0 else ""
        body_text = _node_text(source_bytes, body_node)
        body, _ = _extract_brace_block(body_text, 0)
        if body is None:
            body = body_text.strip()[1:-1]
        functions.append((ret, name, params, body))

    return functions
