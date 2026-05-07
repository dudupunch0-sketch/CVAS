from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from c_ast_utils import parse_statement
from cvas_analysis import AnalysisOptions
from cvas_clang import (
    extract_condition_with_clang,
    extract_for_condition_with_clang,
)
from cvas_cfg import analyze_control_flow, detect_control_notes
from cvas_callgraph import find_function_calls
from cvas_expr import (
    OPERAND_PATTERN,
    classify_operand,
    is_operand,
    is_store_target,
    parse_expression_ops,
)
from cvas_index import extract_decl_names
from cvas_model import Block, CycleRules, OpSummary, Operation, Signal
from cvas_source import split_top_level_commas, strip_comments_and_strings
from cvas_text import (
    KEYWORDS,
    TYPE_AND_C_KEYWORDS,
    extract_identifier_tokens,
    extract_parenthesized_content,
    parse_params,
    split_statements,
    split_top_level_semicolons,
)

CallReference = Tuple[str, List[str], Optional[str]]


@dataclass
class FunctionAnalysisResult:
    """Per-function analysis outputs used by the model assembler."""

    name: str
    block: Block
    operations: List[Operation]
    signals: List[Signal]
    calls: List[CallReference]
    call_metadata: Dict[str, object]
    unresolved_calls: List[Dict[str, object]]
    external_symbols: List[Dict[str, object]]
    function_def_meta: Dict[str, object]


def infer_local_variables(body: str, params: List[str]) -> Set[str]:
    """Infer probable local variables from declarations in a function body."""
    locals_set: Set[str] = set(params)
    for statement in split_statements(body):
        for name in extract_decl_names(statement):
            locals_set.add(name)
    return locals_set


def find_unknown_calls(body: str, known_functions: Set[str]) -> List[str]:
    """Find probable function calls not present in the known function set."""
    cleaned = strip_comments_and_strings(body)
    pattern = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
    unknown: List[str] = []
    for match in pattern.finditer(cleaned):
        name = match.group(1)
        if name in KEYWORDS or name in TYPE_AND_C_KEYWORDS:
            continue
        if name in known_functions:
            continue
        if name not in unknown:
            unknown.append(name)
    return unknown


def expand_simple_function_macros(source: str) -> str:
    """Expand simple function-like macros inline.

    Only handles simple patterns like:
        #define FUNC(x) real_func((x))
        #define MAX(a,b) ((a) > (b) ? (a) : (b))

    Does not handle multiline macros, stringification, concatenation, or
    complex nested macro bodies.
    """
    lines = source.split("\n")
    macros: Dict[str, Tuple[List[str], str]] = {}
    output_lines = []

    define_pattern = re.compile(r"^\s*#define\s+(\w+)\s*\(([^)]*)\)\s+(.+)$")

    for line in lines:
        match = define_pattern.match(line)
        if match:
            name = match.group(1)
            params = [p.strip() for p in match.group(2).split(",") if p.strip()]
            body = match.group(3).strip()

            if "##" not in body and "#" not in body:
                macros[name] = (params, body)

            output_lines.append(line)
        else:
            output_lines.append(line)

    if not macros:
        return source

    expanded_lines = []
    for line in output_lines:
        if define_pattern.match(line):
            expanded_lines.append(line)
            continue

        expanded_line = line

        for macro_name, (params, body) in macros.items():
            pattern = rf"\b{re.escape(macro_name)}\s*\("

            while re.search(pattern, expanded_line):
                match = re.search(pattern, expanded_line)
                if not match:
                    break

                start_idx = match.end() - 1
                paren_content = extract_parenthesized_content(expanded_line, start_idx)

                if paren_content is None:
                    break

                args = [arg.strip() for arg in split_top_level_commas(paren_content)]

                if len(args) != len(params):
                    break

                expanded_body = body
                for param, arg in zip(params, args):
                    expanded_body = re.sub(
                        rf"\b{re.escape(param)}\b", arg, expanded_body
                    )

                macro_call = expanded_line[
                    match.start() : match.end() + len(paren_content) + 1
                ]
                expanded_line = expanded_line.replace(macro_call, expanded_body, 1)

        expanded_lines.append(expanded_line)

    return "\n".join(expanded_lines)


def normalize_compound_operators(statement: str) -> str:
    """Normalize compound operators in a single statement to simple form."""
    post_inc = re.match(
        rf"^\s*(?P<var>{OPERAND_PATTERN})\s*(?P<op>\+\+|--)\s*$",
        statement,
    )
    if post_inc:
        var = post_inc.group("var")
        op = "+" if post_inc.group("op") == "++" else "-"
        return f"{var} = {var} {op} 1"

    pre_inc = re.match(
        rf"^\s*(?P<op>\+\+|--)\s*(?P<var>{OPERAND_PATTERN})\s*$",
        statement,
    )
    if pre_inc:
        var = pre_inc.group("var")
        op = "+" if pre_inc.group("op") == "++" else "-"
        return f"{var} = {var} {op} 1"

    compound = re.match(
        rf"^\s*(?P<lhs>{OPERAND_PATTERN})\s*(?P<op>[+\-*/%&|^]|<<|>>)"
        r"=\s*(?P<rhs>.+)\s*$",
        statement,
    )
    if compound:
        lhs = compound.group("lhs")
        op = compound.group("op")
        rhs = compound.group("rhs").strip()
        return f"{lhs} = {lhs} {op} {rhs}"

    return statement


def extract_keyword_condition(
    statement: str,
    keyword: str,
    analysis_options: AnalysisOptions = AnalysisOptions(),
    *,
    source_path: Optional[Path] = None,
) -> Optional[str]:
    """Extract a condition expression from a keyword statement."""
    if analysis_options.backend == "clang":
        condition = extract_condition_with_clang(
            statement,
            keyword,
            analysis_options=analysis_options,
            source_path=source_path,
        )
        if condition is not None:
            return condition

    parsed = parse_statement(statement)
    if parsed:
        pycparser_module, node, generator = parsed
        if keyword == "if" and isinstance(node, pycparser_module.c_ast.If):
            return generator.visit(node.cond).strip()
        if keyword == "while":
            if isinstance(node, pycparser_module.c_ast.While):
                return generator.visit(node.cond).strip()
            if isinstance(node, pycparser_module.c_ast.DoWhile):
                return generator.visit(node.cond).strip()

    match = re.search(rf"\b{re.escape(keyword)}\b", statement)
    if not match:
        return None
    open_index = statement.find("(", match.end())
    if open_index == -1:
        return None
    return extract_parenthesized_content(statement, open_index)


def extract_for_condition(
    statement: str,
    analysis_options: AnalysisOptions = AnalysisOptions(),
    *,
    source_path: Optional[Path] = None,
) -> Optional[str]:
    """Extract the condition expression from a for loop statement."""
    if analysis_options.backend == "clang":
        condition = extract_for_condition_with_clang(
            statement,
            analysis_options=analysis_options,
            source_path=source_path,
        )
        if condition is not None:
            return condition

    parsed = parse_statement(statement)
    if parsed:
        pycparser_module, node, generator = parsed
        if isinstance(node, pycparser_module.c_ast.For):
            if node.cond is None:
                return None
            condition = generator.visit(node.cond).strip()
            return condition if condition else None

    match = re.search(r"\bfor\b", statement)
    if not match:
        return None
    open_index = statement.find("(", match.end())
    if open_index == -1:
        return None
    contents = extract_parenthesized_content(statement, open_index)
    if contents is None:
        return None
    parts = split_top_level_semicolons(contents)
    if len(parts) < 2:
        return None
    condition = parts[1].strip()
    return condition if condition else None


def handle_simple_assignment(
    lhs: str,
    rhs: str,
    block_id: str,
    op_index: int,
    var_producers: Dict[str, Tuple[str, str]],
) -> Tuple[List[Operation], int, List[Signal]]:
    """Handle simple assignment: var = operand."""
    operations: List[Operation] = []
    edges: List[Signal] = []

    if is_operand(rhs.strip()):
        rhs_kind = classify_operand(rhs)
        if is_store_target(lhs):
            rhs_value = rhs
            if rhs_kind in {"literal", "memory"}:
                op_id = f"{block_id}_op_{op_index}"
                op_index += 1
                op_type = "const" if rhs_kind == "literal" else "load"
                operations.append(
                    Operation(
                        op_id=op_id,
                        op_type=op_type,
                        inputs=[rhs],
                        outputs=[f"tmp_{op_index}"],
                        parent_block_id=block_id,
                    )
                )
                rhs_value = operations[-1].outputs[0]

                producer = var_producers.get(rhs)
                if producer:
                    source_type, source_id = producer
                    edges.append(
                        Signal(
                            source_id=source_id,
                            source_type=source_type,
                            destination_id=op_id,
                            destination_type="operation",
                            signal_name=rhs,
                            direction="internal",
                            comment=f"{op_type} flow",
                        )
                    )
                var_producers[rhs_value] = ("operation", op_id)

            store_id = f"{block_id}_op_{op_index}"
            op_index += 1
            operations.append(
                Operation(
                    op_id=store_id,
                    op_type="store",
                    inputs=[rhs_value],
                    outputs=[lhs],
                    parent_block_id=block_id,
                )
            )

            producer = var_producers.get(rhs_value)
            if producer:
                source_type, source_id = producer
                edges.append(
                    Signal(
                        source_id=source_id,
                        source_type=source_type,
                        destination_id=store_id,
                        destination_type="operation",
                        signal_name=rhs_value,
                        direction="internal",
                        comment="store flow",
                    )
                )

            var_producers[lhs] = ("operation", store_id)
        else:
            op_id = f"{block_id}_op_{op_index}"
            op_index += 1
            op_type = "copy"
            comment = "copy flow"
            if rhs_kind == "literal":
                op_type = "const"
                comment = "const flow"
            elif rhs_kind == "memory":
                op_type = "load"
                comment = "load flow"

            operation = Operation(
                op_id=op_id,
                op_type=op_type,
                inputs=[rhs],
                outputs=[lhs],
                parent_block_id=block_id,
            )
            operations.append(operation)

            producer = var_producers.get(rhs)
            if producer:
                source_type, source_id = producer
                edges.append(
                    Signal(
                        source_id=source_id,
                        source_type=source_type,
                        destination_id=op_id,
                        destination_type="operation",
                        signal_name=rhs,
                        direction="internal",
                        comment=comment,
                    )
                )

            var_producers[lhs] = ("operation", op_id)

    return operations, op_index, edges


def extract_operations(
    body: str,
    block_id: str,
    block_inputs: List[str],
    has_return: bool,
    analysis_options: AnalysisOptions = AnalysisOptions(),
    *,
    source_path: Optional[Path] = None,
) -> Tuple[List[Operation], List[Signal], OpSummary]:
    """Extract operations from a function body."""
    cleaned = strip_comments_and_strings(body)

    operations: List[Operation] = []
    edges: List[Signal] = []

    var_producers: Dict[str, Tuple[str, str]] = {
        name: ("block", block_id) for name in block_inputs
    }

    op_index = 1
    condition_counter = 1

    statements = split_statements(cleaned)

    for statement in statements:
        statement = normalize_compound_operators(statement)
        declaration_match = re.match(
            r"(?P<type>(?:"
            r"(?:typedef|const|volatile|unsigned|signed|short|long|int|float|double|"
            r"char|bool|void|size_t|ssize_t|uint\d+_t|int\d+_t|[\w_]+)\s+|"
            r"struct\s+\w+\s+|enum\s+\w+\s+|\s*\*\s*"
            r")+)"
            r"(?P<lhs>[\w_\s\*\[\]]+)\s*=\s*(?P<rhs>.+)",
            statement,
        )
        if declaration_match:
            lhs = re.sub(r"\s*\*\s*", "", declaration_match.group("lhs"))
            lhs = re.sub(r"\[.*?\]", "", lhs).strip()
            statement = f"{lhs} = {declaration_match.group('rhs').strip()}"

        assignment_match = re.match(
            r"(?P<lhs>[^=]+?)\s*=(?!=)\s*(?P<rhs>.+)", statement
        )
        if assignment_match:
            lhs = assignment_match.group("lhs").strip()
            rhs = assignment_match.group("rhs").strip()

            if is_operand(rhs):
                ops, op_index, new_edges = handle_simple_assignment(
                    lhs, rhs, block_id, op_index, var_producers
                )
                operations.extend(ops)
                edges.extend(new_edges)
            else:
                if is_store_target(lhs):
                    ops, op_index, last_output, new_edges = parse_expression_ops(
                        rhs, block_id, op_index, var_producers
                    )
                    operations.extend(ops)
                    edges.extend(new_edges)

                    if last_output:
                        store_id = f"{block_id}_op_{op_index}"
                        op_index += 1
                        operations.append(
                            Operation(
                                op_id=store_id,
                                op_type="store",
                                inputs=[last_output],
                                outputs=[lhs],
                                parent_block_id=block_id,
                            )
                        )

                        producer = var_producers.get(last_output)
                        if producer:
                            source_type, source_id = producer
                            edges.append(
                                Signal(
                                    source_id=source_id,
                                    source_type=source_type,
                                    destination_id=store_id,
                                    destination_type="operation",
                                    signal_name=last_output,
                                    direction="internal",
                                    comment="store flow",
                                )
                            )
                        var_producers[lhs] = ("operation", store_id)
                else:
                    ops, op_index, _, new_edges = parse_expression_ops(
                        rhs, block_id, op_index, var_producers, output_target=lhs
                    )
                    operations.extend(ops)
                    edges.extend(new_edges)

                    if ops:
                        var_producers[lhs] = ("operation", ops[-1].op_id)
            continue

        return_match = re.match(r"\breturn\b(?P<expr>.+)", statement)
        if return_match and has_return:
            expr = return_match.group("expr").strip()

            if re.match(r"^\w+$", expr):
                producer = var_producers.get(expr)
                if producer:
                    source_type, source_id = producer
                    edges.append(
                        Signal(
                            source_id=source_id,
                            source_type=source_type,
                            destination_id=block_id,
                            destination_type="block",
                            signal_name="return",
                            direction="out",
                            comment="direct return",
                        )
                    )
            else:
                ops, op_index, _, new_edges = parse_expression_ops(
                    expr, block_id, op_index, var_producers, output_target="return"
                )
                operations.extend(ops)
                edges.extend(new_edges)

                if ops:
                    edges.append(
                        Signal(
                            source_id=ops[-1].op_id,
                            source_type="operation",
                            destination_id=block_id,
                            destination_type="block",
                            signal_name="return",
                            direction="out",
                            comment="return flow",
                        )
                    )
            continue

        for_condition = extract_for_condition(
            statement,
            analysis_options=analysis_options,
            source_path=source_path,
        )
        if for_condition is not None:
            output_name = f"cond_{condition_counter}"
            condition_counter += 1
            ops, op_index, _, new_edges = parse_expression_ops(
                for_condition,
                block_id,
                op_index,
                var_producers,
                output_target=output_name,
            )
            operations.extend(ops)
            edges.extend(new_edges)
            continue

        for keyword in ("if", "while"):
            condition_expr = extract_keyword_condition(
                statement,
                keyword,
                analysis_options=analysis_options,
                source_path=source_path,
            )
            if condition_expr is not None:
                output_name = f"cond_{condition_counter}"
                condition_counter += 1
                ops, op_index, _, new_edges = parse_expression_ops(
                    condition_expr,
                    block_id,
                    op_index,
                    var_producers,
                    output_target=output_name,
                )
                operations.extend(ops)
                edges.extend(new_edges)
                break

    summary = OpSummary()
    for op in operations:
        if op.op_type == "add":
            summary.add += 1
        elif op.op_type == "compare":
            summary.compare += 1
        elif op.op_type == "logic":
            summary.logic += 1
        elif op.op_type == "multiply":
            summary.multiply += 1
        elif op.op_type == "copy":
            summary.copy += 1
        elif op.op_type == "shift":
            summary.shift += 1
        elif op.op_type == "bitwise":
            summary.bitwise += 1
        elif op.op_type == "const":
            summary.const += 1
        elif op.op_type == "load":
            summary.load += 1
        elif op.op_type == "store":
            summary.store += 1

    return operations, edges, summary


def estimate_cycles(summary: OpSummary, rules: CycleRules) -> int:
    """Estimate execution cycles based on operation counts."""
    cycles = 0
    cycles += (summary.add + rules.add_per_cycle - 1) // rules.add_per_cycle
    cycles += (summary.compare + rules.compare_per_cycle - 1) // rules.compare_per_cycle
    cycles += (summary.logic + rules.logic_per_cycle - 1) // rules.logic_per_cycle
    cycles += (summary.multiply + rules.mul_per_cycle - 1) // rules.mul_per_cycle
    cycles += (summary.copy + rules.copy_per_cycle - 1) // rules.copy_per_cycle
    cycles += (summary.shift + rules.shift_per_cycle - 1) // rules.shift_per_cycle
    cycles += (summary.bitwise + rules.bitwise_per_cycle - 1) // rules.bitwise_per_cycle
    cycles += (summary.const + rules.const_per_cycle - 1) // rules.const_per_cycle
    cycles += (summary.load + rules.load_per_cycle - 1) // rules.load_per_cycle
    cycles += (summary.store + rules.store_per_cycle - 1) // rules.store_per_cycle
    return cycles


def analyze_function(
    *,
    name: str,
    ret_type: str,
    params: str,
    body: str,
    source_file: str,
    block_id: str,
    known_functions: Set[str],
    symbol_index: Dict[str, Dict[str, object]],
    rules: CycleRules,
    analysis_options: AnalysisOptions = AnalysisOptions(),
) -> FunctionAnalysisResult:
    """Analyze a single function into a block, operations, and metadata."""
    inputs = parse_params(params)
    outputs = [] if ret_type.strip() == "void" else ["return"]

    block_operations, op_edges, summary = extract_operations(
        body,
        block_id,
        inputs,
        bool(outputs),
        analysis_options=analysis_options,
        source_path=Path(source_file),
    )
    cycles = estimate_cycles(summary, rules)

    control_note = detect_control_notes(body)
    note = f"{control_note}; internal op nodes emitted"
    cfg = analyze_control_flow(body, name, block_operations)

    block = Block(
        block_id=block_id,
        block_name=name,
        inputs=inputs,
        outputs=outputs,
        internal_ops_summary=summary,
        estimated_cycles=cycles,
        note=note,
        cfg=cfg,
    )

    local_vars = infer_local_variables(body, inputs)
    identifiers = extract_identifier_tokens(body)
    seen_refs: Set[str] = set()
    external_symbols: List[Dict[str, object]] = []

    for ident in identifiers:
        if ident in KEYWORDS or ident in TYPE_AND_C_KEYWORDS:
            continue
        if ident in known_functions:
            continue
        if ident in local_vars:
            continue
        if ident in seen_refs:
            continue
        seen_refs.add(ident)
        symbol = symbol_index.get(ident)
        if symbol:
            external_symbols.append(
                {
                    "function": name,
                    "name": ident,
                    "kind": symbol.get("kind", "external"),
                    "resolved": True,
                    "defined_in": symbol.get("file"),
                    "line": symbol.get("line"),
                }
            )
        else:
            external_symbols.append(
                {
                    "function": name,
                    "name": ident,
                    "kind": "external",
                    "resolved": False,
                }
            )

    unresolved_calls: List[Dict[str, object]] = []
    for unknown_call in find_unknown_calls(body, known_functions):
        unresolved_calls.append(
            {
                "caller": name,
                "callee": unknown_call,
                "source_file": source_file,
            }
        )

    calls, call_metadata = find_function_calls(
        body,
        known_functions,
        analysis_options=analysis_options,
        source_path=Path(source_file),
    )

    return FunctionAnalysisResult(
        name=name,
        block=block,
        operations=block_operations,
        signals=op_edges,
        calls=calls,
        call_metadata=call_metadata,
        unresolved_calls=unresolved_calls,
        external_symbols=external_symbols,
        function_def_meta={"file": source_file},
    )
