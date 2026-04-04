from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from c_ast_utils import parse_translation_unit
from cvas_model import Block, CallGraph, CallGraphNode
from cvas_source import split_top_level_commas, strip_comments_and_strings
from cvas_text import KEYWORDS, parse_params


def find_function_calls(
    body: str, known_functions: Iterable[str]
) -> Tuple[List[Tuple[str, List[str], Optional[str]]], Dict[str, object]]:
    """Find function calls within known functions."""

    def find_calls_regex() -> List[Tuple[str, List[str], Optional[str]]]:
        cleaned = strip_comments_and_strings(body)
        call_pattern = re.compile(
            r"(?P<lhs>[A-Za-z_]\w*\s*=\s*)?(?P<name>\w+)\s*\((?P<args>[^)]*)\)",
            re.DOTALL,
        )
        found_calls = []
        for match in call_pattern.finditer(cleaned):
            name = match.group("name")
            if name in KEYWORDS or name not in known:
                continue
            args = [
                arg.strip()
                for arg in split_top_level_commas(match.group("args"))
                if arg.strip()
            ]
            lhs = match.group("lhs")
            assigned = lhs.split("=")[0].strip() if lhs else None
            found_calls.append((name, args, assigned))
        return found_calls

    known = set(known_functions)
    calls: List[Tuple[str, List[str], Optional[str]]] = []

    parsed = parse_translation_unit(f"void __cvas_wrapper(void) {{\n{body}\n}}")
    if parsed is None:
        return find_calls_regex(), {
            "parser": "regex",
            "limitations": ["AST parse failed; regex fallback used"],
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
        "limitations": [],
    }


def build_call_graph(
    functions: List[Tuple[str, str, str, str, str]],
    block_ids: Dict[str, str],
    blocks: List[Block],
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

    for _, caller_name, _, body, _ in functions:
        calls, metadata = find_function_calls(body, block_ids.keys())
        if metadata["parser"] == "ast":
            ast_analyses += 1
        else:
            analysis_limitations.extend(metadata.get("limitations", []))

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
) -> List[Dict[str, object]]:
    """Build ordered call sequences per function."""
    known = set(known_functions)
    params_by_name = {
        name: parse_params(params) for _, name, params, _, _ in functions
    }
    sequences: List[Dict[str, object]] = []

    for _, caller_name, _, body, _ in functions:
        calls, _ = find_function_calls(body, known)
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
