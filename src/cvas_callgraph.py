from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from c_ast_utils import parse_translation_unit
from cvas_analysis import AnalysisOptions
from cvas_clang import find_function_calls_with_clang
from cvas_model import Block, CallGraph, CallGraphNode
from cvas_source import split_top_level_commas, strip_comments_and_strings
from cvas_text import KEYWORDS, parse_params, split_statements

_CALL_NAME_PATTERN = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def _find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for idx in range(open_index, len(text)):
        if text[idx] == "(":
            depth += 1
        elif text[idx] == ")":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _extract_assigned_target(prefix: str, suffix: str) -> Optional[str]:
    if suffix.strip():
        return None
    match = re.search(r"(?<![=!<>+\-*/%&|^])=(?!=)\s*$", prefix)
    if not match:
        return None

    lhs = prefix[: match.start()].strip()
    if not lhs:
        return None

    direct_lhs = re.fullmatch(
        r"(?:\*+\s*)?[A-Za-z_]\w*(?:\s*(?:\[[^\]]*\]|\.[A-Za-z_]\w*|->\s*[A-Za-z_]\w*))*",
        lhs,
    )
    if direct_lhs:
        return direct_lhs.group(0).strip()

    decl_name = re.search(r"([A-Za-z_]\w*)\s*$", lhs)
    if decl_name:
        return decl_name.group(1)

    return None


def _scan_calls_in_segment(
    segment: str,
    known_functions: set[str],
    *,
    allow_assignment: bool,
) -> List[Tuple[str, List[str], Optional[str]]]:
    cleaned = strip_comments_and_strings(segment)
    calls: List[Tuple[str, List[str], Optional[str]]] = []
    offset = 0

    while True:
        match = _CALL_NAME_PATTERN.search(cleaned, offset)
        if match is None:
            return calls

        open_index = match.end() - 1
        close_index = _find_matching_paren(cleaned, open_index)
        if close_index == -1:
            offset = match.end()
            continue

        args_text = segment[open_index + 1 : close_index]
        nested_args = _scan_calls_in_segment(
            args_text,
            known_functions,
            allow_assignment=False,
        )
        name = match.group(1)

        if name in KEYWORDS or name not in known_functions:
            calls.extend(nested_args)
            offset = close_index + 1
            continue

        args = [arg.strip() for arg in split_top_level_commas(args_text) if arg.strip()]
        assigned = None
        if allow_assignment:
            assigned = _extract_assigned_target(
                cleaned[: match.start()],
                cleaned[close_index + 1 :],
            )

        calls.append((name, args, assigned))
        calls.extend(nested_args)
        offset = close_index + 1


def _find_calls_text(
    body: str,
    known_functions: set[str],
) -> List[Tuple[str, List[str], Optional[str]]]:
    calls: List[Tuple[str, List[str], Optional[str]]] = []
    for statement in split_statements(body):
        calls.extend(
            _scan_calls_in_segment(
                statement,
                known_functions,
                allow_assignment=True,
            )
        )
    return calls


def find_function_calls(
    body: str,
    known_functions: Iterable[str],
    analysis_options: AnalysisOptions = AnalysisOptions(),
    *,
    source_path: Optional[Path] = None,
) -> Tuple[List[Tuple[str, List[str], Optional[str]]], Dict[str, object]]:
    """Find function calls within known functions."""
    known = set(known_functions)
    limitations: List[str] = []

    if analysis_options.backend == "clang":
        calls, metadata = find_function_calls_with_clang(
            body,
            known_functions,
            analysis_options=analysis_options,
            source_path=source_path,
        )
        if calls or metadata["parser"] == "clang":
            return calls, metadata
        limitations.extend(metadata.get("limitations", []))

    calls: List[Tuple[str, List[str], Optional[str]]] = []

    parsed = parse_translation_unit(f"void __cvas_wrapper(void) {{\n{body}\n}}")
    if parsed is None:
        return _find_calls_text(body, known), {
            "parser": "text",
            "limitations": [
                *limitations,
                "AST parse failed; text fallback used",
            ],
        }

    pycparser_module, ast, generator, _ = parsed

    def record_call(
        node: pycparser_module.c_ast.FuncCall, assigned: Optional[str]
    ) -> None:
        if isinstance(node.name, pycparser_module.c_ast.ID):
            name = node.name.name
        else:
            name = generator.visit(node.name)
        if name in KEYWORDS or name not in known:
            return
        args = []
        if node.args:
            args = [generator.visit(arg).strip() for arg in node.args.exprs]
        calls.append((name, args, assigned))

    def walk(node: pycparser_module.c_ast.Node) -> None:
        if isinstance(node, pycparser_module.c_ast.Assignment):
            if isinstance(node.rvalue, pycparser_module.c_ast.FuncCall):
                record_call(node.rvalue, generator.visit(node.lvalue).strip())
                if node.rvalue.args:
                    for arg in node.rvalue.args.exprs:
                        walk(arg)
            else:
                walk(node.rvalue)
            walk(node.lvalue)
            return
        if isinstance(node, pycparser_module.c_ast.Decl):
            if isinstance(node.init, pycparser_module.c_ast.FuncCall):
                record_call(node.init, node.name)
                if node.init.args:
                    for arg in node.init.args.exprs:
                        walk(arg)
            elif node.init:
                walk(node.init)
            return
        if isinstance(node, pycparser_module.c_ast.FuncCall):
            record_call(node, None)
            if node.args:
                for arg in node.args.exprs:
                    walk(arg)
            return
        for _, child in node.children():
            walk(child)

    walk(ast)
    return calls, {
        "parser": "ast",
        "limitations": limitations,
    }


def build_call_graph(
    functions: List[Tuple[str, str, str, str, str]],
    block_ids: Dict[str, str],
    blocks: List[Block],
    analysis_options: AnalysisOptions = AnalysisOptions(),
) -> CallGraph:
    """Build function call graph with dependency analysis."""
    nodes: Dict[str, CallGraphNode] = {}

    for _, name, _, _, _ in functions:
        block = next((b for b in blocks if b.block_name == name), None)
        nodes[name] = CallGraphNode(
            function_name=name,
            block_id=block_ids[name],
            callers=[],
            callees=[],
            call_depth=0,
            is_recursive=False,
            self_cycles=block.estimated_cycles if block else 0,
            total_cycles=0,
        )

    total_analyses = len(functions)
    ast_analyses = 0
    analysis_limitations: List[str] = []

    for _, caller_name, _, body, source_file in functions:
        calls, metadata = find_function_calls(
            body,
            block_ids.keys(),
            analysis_options=analysis_options,
            source_path=Path(source_file),
        )
        analysis_limitations.extend(metadata.get("limitations", []))
        if metadata["parser"] in {"ast", "clang"}:
            ast_analyses += 1

        for callee_name, _, _ in calls:
            if callee_name not in nodes:
                continue

            nodes[caller_name].callees.append(callee_name)
            nodes[callee_name].callers.append(caller_name)

    entry_functions = [name for name, node in nodes.items() if not node.callers]

    def walk_call_graph(func_name: str, depth: int, stack: List[str]) -> None:
        if func_name in stack:
            cycle_start = stack.index(func_name)
            for cycle_node in stack[cycle_start:]:
                nodes[cycle_node].is_recursive = True
            nodes[func_name].is_recursive = True
            return

        if depth <= nodes[func_name].call_depth:
            return

        nodes[func_name].call_depth = depth
        new_stack = stack + [func_name]
        for callee in nodes[func_name].callees:
            walk_call_graph(callee, depth + 1, new_stack)

    if entry_functions:
        for entry in entry_functions:
            walk_call_graph(entry, 0, [])

    max_depth = max((node.call_depth for node in nodes.values()), default=0)

    for depth in range(max_depth, -1, -1):
        for node in nodes.values():
            if node.call_depth == depth:
                callee_cycles = sum(
                    nodes[callee].total_cycles
                    for callee in node.callees
                    if callee in nodes
                )
                node.total_cycles = node.self_cycles + callee_cycles

    critical_path = []
    max_cycles = 0

    def find_longest_path(func_name: str, path: List[str]) -> Tuple[List[str], int]:
        if func_name in path:
            return path, sum(nodes[f].self_cycles for f in path)

        new_path = path + [func_name]

        if not nodes[func_name].callees:
            cycles = sum(nodes[f].self_cycles for f in new_path)
            return new_path, cycles

        best_path = new_path
        best_cycles = sum(nodes[f].self_cycles for f in new_path)

        for callee in nodes[func_name].callees:
            sub_path, sub_cycles = find_longest_path(callee, new_path)
            if sub_cycles > best_cycles:
                best_path = sub_path
                best_cycles = sub_cycles

        return best_path, best_cycles

    for entry in entry_functions:
        path, cycles = find_longest_path(entry, [])
        if cycles > max_cycles:
            critical_path = path
            max_cycles = cycles

    call_chains = []
    for entry in entry_functions:
        chain, _ = find_longest_path(entry, [])
        call_chains.append(chain)

    has_recursion = any(node.is_recursive for node in nodes.values())

    if total_analyses == 0:
        analysis_coverage = 1.0
    else:
        analysis_coverage = ast_analyses / total_analyses

    if analysis_coverage >= 0.85 and not analysis_limitations:
        analysis_confidence = "high"
    elif analysis_coverage >= 0.6:
        analysis_confidence = "medium"
    else:
        analysis_confidence = "low"

    return CallGraph(
        nodes=nodes,
        entry_functions=entry_functions,
        call_chains=call_chains,
        critical_path=critical_path,
        max_depth=max_depth,
        has_recursion=has_recursion,
        analysis_confidence=analysis_confidence,
        analysis_coverage=round(analysis_coverage, 3),
        analysis_limitations=sorted(set(analysis_limitations)),
    )


def build_call_sequence(
    functions: List[Tuple[str, str, str, str, str]],
    known_functions: Iterable[str],
    analysis_options: AnalysisOptions = AnalysisOptions(),
) -> List[Dict[str, object]]:
    """Build ordered call sequences per function."""
    known = set(known_functions)
    params_by_name = {
        name: parse_params(params) for _, name, params, _, _ in functions
    }
    sequences: List[Dict[str, object]] = []

    for _, caller_name, _, body, source_file in functions:
        calls, _ = find_function_calls(
            body,
            known,
            analysis_options=analysis_options,
            source_path=Path(source_file),
        )
        call_items = []
        for callee_name, args, assigned in calls:
            call_items.append(
                {
                    "callee": callee_name,
                    "args": args,
                    "assigned": assigned,
                    "callee_params": params_by_name.get(callee_name, []),
                }
            )

        sequences.append({"function": caller_name, "calls": call_items})

    return sequences
