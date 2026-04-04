from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from cvas_model import Operation, Signal

OPERATOR_PRECEDENCE = {
    "*": 11,
    "/": 11,
    "%": 11,
    "+": 10,
    "-": 10,
    "<<": 9,
    ">>": 9,
    "<": 8,
    ">": 8,
    "<=": 8,
    ">=": 8,
    "==": 7,
    "!=": 7,
    "&": 6,
    "^": 5,
    "|": 4,
    "&&": 3,
    "||": 2,
    "?:": 1,
}

OPERATORS = set(OPERATOR_PRECEDENCE.keys())

OPERAND_PATTERN = (
    r"(?:"
    r"(?:\([A-Za-z_]\w*(?:\s*\*+)?\)\s*)*"
    r"(?:[+\-!~*&]+)?"
    r"(?:[A-Za-z_]\w*|0x[0-9A-Fa-f]+|\d+)"
    r"(?:\s*(?:\[[^\]]+\]|\.\w+|->\w+))*"
    r")"
)
OPERAND_REGEX = re.compile(rf"^{OPERAND_PATTERN}$")
CAST_PATTERN = re.compile(r"^\(\s*[A-Za-z_]\w*(?:\s*\*+)?\s*\)\s*")
NUMERIC_LITERAL_PATTERN = re.compile(r"^(?:0x[0-9A-Fa-f]+|\d+)$")


def tokenize_expression(expr: str) -> List[str]:
    """Tokenize expression including bitwise and shift operators."""
    pattern = re.compile(
        rf"{OPERAND_PATTERN}|"
        r"<<=|>>=|"
        r"<<|>>|"
        r"<=|>=|==|!=|"
        r"&&|\|\||"
        r"\?|:|"
        r"[+\-*/%<>&|^]|"
        r"\(|\)"
    )
    return [token.strip() for token in pattern.findall(expr) if token.strip()]


def is_operand(token: str) -> bool:
    """Check if token is an operand."""
    return bool(OPERAND_REGEX.match(token) or re.match(r"tmp_\d+|cond_\d+", token))


def _strip_casts(token: str) -> str:
    """Strip leading C-style casts from a token."""
    stripped = token.strip()
    while True:
        match = CAST_PATTERN.match(stripped)
        if not match:
            break
        stripped = stripped[match.end() :].lstrip()
    return stripped


def classify_operand(token: str) -> str:
    """Classify operand as literal, memory, or variable."""
    stripped = _strip_casts(token)
    if stripped.startswith("&"):
        return "variable"

    unary_match = re.match(r"^([+\-!~*]+)\s*(.*)$", stripped)
    if unary_match:
        unary_ops = unary_match.group(1)
        base = unary_match.group(2)
    else:
        unary_ops = ""
        base = stripped

    if (
        NUMERIC_LITERAL_PATTERN.fullmatch(base)
        and "*" not in unary_ops
        and "&" not in unary_ops
    ):
        return "literal"

    if "*" in unary_ops or "[" in base or "." in base or "->" in base:
        return "memory"

    return "variable"


def is_store_target(token: str) -> bool:
    """Check if the assignment target should be treated as a store."""
    return classify_operand(token) == "memory"


def classify_operator(op: str) -> str:
    """Classify operator into operation type."""
    if op in {"<", ">", "<=", ">=", "==", "!="}:
        return "compare"
    if op in {"&&", "||", "?:"}:
        return "logic"
    if op in {"*", "/", "%"}:
        return "multiply"
    if op in {"+", "-"}:
        return "add"
    if op in {"<<", ">>"}:
        return "shift"
    if op in {"&", "|", "^"}:
        return "bitwise"
    return "unknown"


def parse_expression_ops(
    expr: str,
    block_id: str,
    op_index_start: int,
    var_producers: Dict[str, Tuple[str, str]],
    output_target: Optional[str] = None,
) -> Tuple[List[Operation], int, Optional[str], List[Signal]]:
    """Parse expression using Shunting-yard algorithm."""
    tokens = tokenize_expression(expr)
    ops: List[Operation] = []
    edges: List[Signal] = []
    op_index = op_index_start

    output_queue: List[str] = []
    operator_stack: List[str] = []
    right_associative = {"?:"}

    def should_pop(stack_op: str, incoming_op: str) -> bool:
        if stack_op not in OPERATOR_PRECEDENCE:
            return False
        if incoming_op in right_associative:
            return OPERATOR_PRECEDENCE[stack_op] > OPERATOR_PRECEDENCE[incoming_op]
        return OPERATOR_PRECEDENCE[stack_op] >= OPERATOR_PRECEDENCE[incoming_op]

    for token in tokens:
        if is_operand(token):
            output_queue.append(token)
        elif token in OPERATORS:
            while (
                operator_stack
                and operator_stack[-1] in OPERATORS
                and should_pop(operator_stack[-1], token)
            ):
                output_queue.append(operator_stack.pop())
            operator_stack.append(token)
        elif token == "?":
            operator_stack.append(token)
        elif token == ":":
            while operator_stack and operator_stack[-1] != "?":
                output_queue.append(operator_stack.pop())
            if operator_stack and operator_stack[-1] == "?":
                operator_stack.pop()
                while (
                    operator_stack
                    and operator_stack[-1] in OPERATORS
                    and should_pop(operator_stack[-1], "?:")
                ):
                    output_queue.append(operator_stack.pop())
                operator_stack.append("?:")
        elif token == "(":
            operator_stack.append(token)
        elif token == ")":
            while operator_stack and operator_stack[-1] != "(":
                output_queue.append(operator_stack.pop())
            if operator_stack and operator_stack[-1] == "(":
                operator_stack.pop()

    while operator_stack:
        op_token = operator_stack.pop()
        if op_token not in {"(", "?"}:
            output_queue.append(op_token)

    eval_stack: List[str] = []

    for token in output_queue:
        if is_operand(token):
            eval_stack.append(token)
            continue

        if token not in OPERATORS:
            continue

        if token == "?:":
            if len(eval_stack) < 3:
                continue
            false_branch = eval_stack.pop()
            true_branch = eval_stack.pop()
            condition = eval_stack.pop()
            inputs = [condition, true_branch, false_branch]
        else:
            if len(eval_stack) < 2:
                continue
            right = eval_stack.pop()
            left = eval_stack.pop()
            inputs = [left, right]

        op_type = classify_operator(token)
        op_id = f"{block_id}_op_{op_index}"
        op_index += 1
        output_name = f"tmp_{op_index}"

        operation = Operation(
            op_id=op_id,
            op_type=op_type,
            inputs=inputs,
            outputs=[output_name],
            parent_block_id=block_id,
        )
        ops.append(operation)

        for input_token in inputs:
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
        eval_stack.append(output_name)

    last_output = eval_stack[-1] if eval_stack else None

    if output_target and ops:
        ops[-1].outputs = [output_target]
        var_producers[output_target] = ("operation", ops[-1].op_id)
        last_output = output_target
    elif output_target and last_output:
        op_id = f"{block_id}_op_{op_index}"
        op_index += 1
        operand_kind = classify_operand(last_output)
        op_type = "copy"
        comment = "copy flow"
        if operand_kind == "literal":
            op_type = "const"
            comment = "const flow"
        elif operand_kind == "memory":
            op_type = "load"
            comment = "load flow"

        operation = Operation(
            op_id=op_id,
            op_type=op_type,
            inputs=[last_output],
            outputs=[output_target],
            parent_block_id=block_id,
        )
        ops.append(operation)

        producer = var_producers.get(last_output)
        if producer:
            source_type, source_id = producer
            edges.append(
                Signal(
                    source_id=source_id,
                    source_type=source_type,
                    destination_id=op_id,
                    destination_type="operation",
                    signal_name=last_output,
                    direction="internal",
                    comment=comment,
                )
            )

        var_producers[output_target] = ("operation", op_id)
        last_output = output_target

    return ops, op_index, last_output, edges
