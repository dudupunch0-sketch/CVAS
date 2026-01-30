#!/usr/bin/env python3
"""MVP parser for CVAS C-model blocks.

Parses only code between CVAS_START and CVAS_END markers and emits
structured JSON suitable for block diagram tooling.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"


@dataclass
class CycleRules:
    add_per_cycle: int = 4
    compare_per_cycle: int = 4
    mul_per_cycle: int = 1

    @classmethod
    def from_json(cls, path: Path) -> "CycleRules":
        data = json.loads(path.read_text())
        return cls(
            add_per_cycle=int(data.get("add_per_cycle", cls.add_per_cycle)),
            compare_per_cycle=int(data.get("compare_per_cycle", cls.compare_per_cycle)),
            mul_per_cycle=int(data.get("mul_per_cycle", cls.mul_per_cycle)),
        )


@dataclass
class OpSummary:
    add: int
    compare: int
    multiply: int


@dataclass
class Operation:
    op_id: str
    op_type: str
    inputs: List[str]
    outputs: List[str]
    parent_block_id: str


@dataclass
class Block:
    block_id: str
    block_name: str
    inputs: List[str]
    outputs: List[str]
    internal_ops_summary: OpSummary
    estimated_cycles: int
    note: str
    position: Dict[str, str]


@dataclass
class Signal:
    source_id: str
    source_type: str
    destination_id: str
    destination_type: str
    signal_name: str
    direction: str
    comment: Optional[str] = None


@dataclass
class Flow:
    execution_order: List[str]
    parallelism: str


def extract_cvas_region(source: str) -> str:
    start_index = source.find(MARKER_START)
    end_index = source.find(MARKER_END)
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        return ""
    return source[start_index + len(MARKER_START) : end_index]


def strip_comments_and_strings(source: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    pattern = re.compile(
        r"//.*?$|/\*.*?\*/|\"(\\.|[^\\\"])*\"|'(\\.|[^\\'])*'",
        re.DOTALL | re.MULTILINE,
    )
    return re.sub(pattern, replacer, source)


def find_function_definitions(source: str) -> List[Tuple[str, str, str, str]]:
    """Return list of (return_type, name, params, body)."""
    functions = []
    cleaned = strip_comments_and_strings(source)
    pattern = re.compile(
        r"(?P<ret>[\w\s\*]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(cleaned):
        start = match.end() - 1
        body, end_index = extract_brace_block(cleaned, start)
        if body is None:
            continue
        ret = " ".join(match.group("ret").split())
        name = match.group("name")
        params = match.group("params")
        functions.append((ret, name, params, body))
    return functions


def extract_brace_block(source: str, start_index: int) -> Tuple[Optional[str], int]:
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


def parse_params(params: str) -> List[str]:
    params = params.strip()
    if not params or params == "void":
        return []
    result = []
    for param in params.split(","):
        param = param.strip()
        if not param:
            continue
        tokens = param.split()
        name = tokens[-1].replace("*", "").strip()
        result.append(name)
    return result


def tokenize_expression(expr: str) -> List[str]:
    token_pattern = re.compile(r"[A-Za-z_]\w*|\d+|<=|>=|==|!=|[+\-*<>]|\(|\)")
    return token_pattern.findall(expr)


def parse_expression_ops(
    expr: str,
    block_id: str,
    op_index_start: int,
    var_producers: Dict[str, Tuple[str, str]],
    output_target: Optional[str] = None,
) -> Tuple[List[Operation], int, Optional[str], List[Signal]]:
    tokens = tokenize_expression(expr)
    ops: List[Operation] = []
    edges: List[Signal] = []
    op_index = op_index_start
    operators = {"+", "-", "*", "<", ">", "<=", ">=", "==", "!="}

    def is_operand(token: str) -> bool:
        return bool(re.match(r"[A-Za-z_]\w*|\d+|tmp_\d+", token))

    while True:
        op_pos = None
        for idx, token in enumerate(tokens):
            if token in operators:
                if idx > 0 and idx + 1 < len(tokens) and is_operand(tokens[idx - 1]) and is_operand(tokens[idx + 1]):
                    op_pos = idx
                    break
        if op_pos is None:
            break
        left = tokens[op_pos - 1]
        op_token = tokens[op_pos]
        right = tokens[op_pos + 1]
        op_type = "compare" if op_token in {"<", ">", "<=", ">=", "==", "!="} else "multiply" if op_token == "*" else "add"
        op_id = f"{block_id}_op_{op_index}"
        op_index += 1
        output_name = f"tmp_{op_index}"
        operation = Operation(
            op_id=op_id,
            op_type=op_type,
            inputs=[left, right],
            outputs=[output_name],
            parent_block_id=block_id,
        )
        ops.append(operation)

        for input_token in (left, right):
            producer = var_producers.get(input_token)
            if producer:
                source_type, source_id = producer
                edges.append(
                    Signal(
                        source_id=source_id,
                        source_type=source_type,
                        destination_id=op_id,
                        destination_type="operation",
                        signal_name=input_token,
                        direction="internal",
                        comment="operand flow",
                    )
                )

        var_producers[output_name] = ("operation", op_id)
        tokens = tokens[: op_pos - 1] + [output_name] + tokens[op_pos + 2 :]

    last_output = tokens[0] if tokens else None
    if output_target and ops:
        ops[-1].outputs = [output_target]
        var_producers[output_target] = ("operation", ops[-1].op_id)
        last_output = output_target
    return ops, op_index, last_output, edges


def extract_operations(
    body: str, block_id: str, block_inputs: List[str], has_return: bool
) -> Tuple[List[Operation], List[Signal], OpSummary]:
    cleaned = strip_comments_and_strings(body)
    operations: List[Operation] = []
    edges: List[Signal] = []
    var_producers: Dict[str, Tuple[str, str]] = {
        name: ("block", block_id) for name in block_inputs
    }
    op_index = 1
    summary = OpSummary(add=0, compare=0, multiply=0)
    condition_counter = 1

    statements = [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]
    for statement in statements:
        assignment_match = re.match(r"(?P<lhs>\w+)\s*=(?!=)\s*(?P<rhs>.+)", statement)
        if assignment_match:
            lhs = assignment_match.group("lhs")
            rhs = assignment_match.group("rhs")
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

        conditional_match = re.search(r"\b(if|while)\s*\((.+)\)", statement)
        if conditional_match:
            condition_expr = conditional_match.group(2)
            output_name = f"cond_{condition_counter}"
            condition_counter += 1
            ops, op_index, _, new_edges = parse_expression_ops(
                condition_expr, block_id, op_index, var_producers, output_target=output_name
            )
            operations.extend(ops)
            edges.extend(new_edges)
            continue

    for op in operations:
        if op.op_type == "add":
            summary.add += 1
        elif op.op_type == "compare":
            summary.compare += 1
        elif op.op_type == "multiply":
            summary.multiply += 1

    return operations, edges, summary


def estimate_cycles(summary: OpSummary, rules: CycleRules) -> int:
    add_cycles = (summary.add + rules.add_per_cycle - 1) // rules.add_per_cycle
    compare_cycles = (summary.compare + rules.compare_per_cycle - 1) // rules.compare_per_cycle
    mul_cycles = (summary.multiply + rules.mul_per_cycle - 1) // rules.mul_per_cycle
    return add_cycles + compare_cycles + mul_cycles


def detect_control_notes(body: str) -> str:
    cleaned = strip_comments_and_strings(body)
    has_loop = bool(re.search(r"\b(for|while|do)\b", cleaned))
    has_conditional = bool(re.search(r"\bif\b", cleaned))
    notes = []
    if has_loop:
        notes.append("contains loop")
    if has_conditional:
        notes.append("contains conditional")
    if not notes:
        notes.append("no loop/conditional detected")
    return "; ".join(notes)


def find_function_calls(body: str, known_functions: Iterable[str]) -> List[Tuple[str, List[str], Optional[str]]]:
    cleaned = strip_comments_and_strings(body)
    known = set(known_functions)
    calls = []
    call_pattern = re.compile(r"(?P<lhs>\w+\s*=\s*)?(?P<name>\w+)\s*\((?P<args>[^)]*)\)")
    keywords = {"if", "for", "while", "switch", "return", "sizeof"}
    for match in call_pattern.finditer(cleaned):
        name = match.group("name")
        if name in keywords:
            continue
        if name not in known:
            continue
        args = [arg.strip() for arg in match.group("args").split(",") if arg.strip()]
        lhs = match.group("lhs")
        assigned = lhs.split("=")[0].strip() if lhs else None
        calls.append((name, args, assigned))
    return calls


def build_model(source: str, rules: CycleRules) -> Dict[str, object]:
    region = extract_cvas_region(source)
    if not region:
        return {
            "blocks": [],
            "operations": [],
            "signals": [],
            "flow": {"execution_order": [], "parallelism": "unknown"},
            "diagram_hint": {"layout": "TBD by drawing tool"},
            "note": "CVAS_START/CVAS_END region not found or empty",
        }

    functions = find_function_definitions(region)
    block_ids = {name: f"B{idx + 1}" for idx, (_, name, _, _) in enumerate(functions)}
    blocks: List[Block] = []
    operations: List[Operation] = []
    signals: List[Signal] = []

    for ret_type, name, params, body in functions:
        inputs = parse_params(params)
        outputs = []
        if ret_type and ret_type.strip() != "void":
            outputs.append("return")
        block_id = block_ids[name]
        block_operations, op_edges, summary = extract_operations(
            body, block_id, inputs, bool(outputs)
        )
        operations.extend(block_operations)
        signals.extend(op_edges)
        cycles = estimate_cycles(summary, rules)
        note = f"{detect_control_notes(body)}; internal op nodes emitted"
        blocks.append(
            Block(
                block_id=block_id,
                block_name=name,
                inputs=inputs,
                outputs=outputs,
                internal_ops_summary=summary,
                estimated_cycles=cycles,
                note=note,
                position={"x": "TBD by drawing tool", "y": "TBD by drawing tool"},
            )
        )

    for _, caller_name, _, body in functions:
        caller_id = block_ids[caller_name]
        calls = find_function_calls(body, block_ids.keys())
        for callee_name, args, assigned in calls:
            callee_id = block_ids[callee_name]
            for arg in args:
                signals.append(
                    Signal(
                        source_id=caller_id,
                        source_type="block",
                        destination_id=callee_id,
                        destination_type="block",
                        signal_name=arg or "unknown",
                        direction="in",
                        comment="argument flow",
                    )
                )
            if assigned:
                signals.append(
                    Signal(
                        source_id=callee_id,
                        source_type="block",
                        destination_id=caller_id,
                        destination_type="block",
                        signal_name=assigned,
                        direction="out",
                        comment="return flow",
                    )
                )

    flow = Flow(
        execution_order=[block.block_id for block in blocks],
        parallelism="unknown",
    )

    return {
        "blocks": [serialize_block(block) for block in blocks],
        "operations": [asdict(operation) for operation in operations],
        "signals": [asdict(signal) for signal in signals],
        "flow": asdict(flow),
        "diagram_hint": {"layout": "TBD by drawing tool"},
        "note": "internal op nodes emitted",
    }


def serialize_block(block: Block) -> Dict[str, object]:
    data = asdict(block)
    data["internal_ops_summary"] = asdict(block.internal_ops_summary)
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CVAS MVP block-diagram parser")
    parser.add_argument("input", type=Path, help="Path to C source file")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path (default: stdout)")
    parser.add_argument("--cycle-config", type=Path, help="JSON file overriding cycle rules")
    parser.add_argument("--add-per-cycle", type=int, default=None)
    parser.add_argument("--compare-per-cycle", type=int, default=None)
    parser.add_argument("--mul-per-cycle", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = CycleRules()
    if args.cycle_config:
        rules = CycleRules.from_json(args.cycle_config)
    if args.add_per_cycle is not None:
        rules.add_per_cycle = args.add_per_cycle
    if args.compare_per_cycle is not None:
        rules.compare_per_cycle = args.compare_per_cycle
    if args.mul_per_cycle is not None:
        rules.mul_per_cycle = args.mul_per_cycle

    source = args.input.read_text()
    model = build_model(source, rules)
    output = json.dumps(model, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
