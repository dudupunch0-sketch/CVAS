#!/usr/bin/env python3
"""CVAS Enhanced - C-model Block Diagram Parser with Advanced Analysis

Version 2.0 - Enhanced with:
- P1: Complete data flow tracking (simple assignments, compound operators, bitwise)
- P2: Control Flow Graph (CFG), Call Graph, advanced analysis
- P3: Memory tracking (TODO - requires user annotation)

Features:
- Shunting-yard algorithm for accurate expression parsing
- Complete data flow tracking including simple assignments
- Control Flow Graph with basic block analysis
- Function call graph with critical path detection
- Configurable cycle estimation rules
- Rich signal metadata with full dependency tracking
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


# ============================================================================
# Constants
# ============================================================================

MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"

# Operator precedence (higher = higher priority)
OPERATOR_PRECEDENCE = {
    "<<": 5, ">>": 5,     # Shift (highest)
    "*": 4, "/": 4, "%": 4,  # Multiply, divide, modulo
    "+": 3, "-": 3,       # Add, subtract
    "&": 2,               # Bitwise AND
    "^": 2,               # Bitwise XOR
    "|": 2,               # Bitwise OR
    "<": 1, ">": 1,       # Comparison
    "<=": 1, ">=": 1,
    "==": 1, "!=": 1,
}

OPERATORS = set(OPERATOR_PRECEDENCE.keys())

# Keywords to exclude from function call detection
KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "do", "else", "break", "continue"}


# ============================================================================
# Data Models - Enhanced
# ============================================================================

@dataclass
class CycleRules:
    """Hardware cycle estimation rules - Extended for new operation types."""
    add_per_cycle: int = 4
    compare_per_cycle: int = 4
    mul_per_cycle: int = 1
    copy_per_cycle: int = 8      # Simple assignments are very fast
    shift_per_cycle: int = 2     # Bit shifts
    bitwise_per_cycle: int = 4   # Bitwise operations

    @classmethod
    def from_json(cls, path: Path) -> "CycleRules":
        """Load cycle rules from JSON file."""
        data = json.loads(path.read_text(encoding='utf-8'))
        return cls(
            add_per_cycle=int(data.get("add_per_cycle", cls.add_per_cycle)),
            compare_per_cycle=int(data.get("compare_per_cycle", cls.compare_per_cycle)),
            mul_per_cycle=int(data.get("mul_per_cycle", cls.mul_per_cycle)),
            copy_per_cycle=int(data.get("copy_per_cycle", cls.copy_per_cycle)),
            shift_per_cycle=int(data.get("shift_per_cycle", cls.shift_per_cycle)),
            bitwise_per_cycle=int(data.get("bitwise_per_cycle", cls.bitwise_per_cycle)),
        )

    def validate(self) -> None:
        """Validate cycle rules are positive."""
        rules = [
            self.add_per_cycle, self.compare_per_cycle, self.mul_per_cycle,
            self.copy_per_cycle, self.shift_per_cycle, self.bitwise_per_cycle
        ]
        if any(r <= 0 for r in rules):
            raise ValueError("All cycle rules must be positive integers")


@dataclass
class OpSummary:
    """Summary of operations by type - Extended."""
    add: int = 0
    compare: int = 0
    multiply: int = 0
    copy: int = 0         # Simple assignments
    shift: int = 0        # Bit shift operations
    bitwise: int = 0      # Bitwise AND/OR/XOR

    def total(self) -> int:
        """Return total operation count."""
        return (self.add + self.compare + self.multiply +
                self.copy + self.shift + self.bitwise)


@dataclass
class Operation:
    """Single operation node within a block."""
    op_id: str
    op_type: str  # "add", "compare", "multiply", "copy", "shift", "bitwise"
    inputs: List[str]
    outputs: List[str]
    parent_block_id: str
    source_line: Optional[int] = None  # For debugging


@dataclass
class Signal:
    """Connection between blocks or operations."""
    source_id: str
    source_type: str  # "block" or "operation"
    destination_id: str
    destination_type: str  # "block" or "operation"
    signal_name: str
    direction: str  # "in", "out", "internal"
    comment: Optional[str] = None


# ============================================================================
# P2: Control Flow Graph
# ============================================================================

@dataclass
class BasicBlock:
    """Basic block in control flow graph."""
    block_id: str
    parent_function: str
    operations: List[str]  # Operation IDs
    predecessors: List[str]  # Previous block IDs
    successors: List[str]    # Next block IDs
    block_type: str  # "entry", "sequential", "conditional_branch", "loop_header", "loop_body", "exit"


@dataclass
class LoopInfo:
    """Loop structure information."""
    loop_id: str
    header_block: str
    body_blocks: List[str]
    exit_blocks: List[str]
    nesting_level: int
    estimated_iterations: str  # "unknown", "constant:N", "bounded:var"


@dataclass
class ControlFlowGraph:
    """Function-level control flow graph."""
    function_name: str
    basic_blocks: List[BasicBlock]
    entry_block: str
    exit_blocks: List[str]
    loops: List[LoopInfo]
    has_branches: bool
    max_nesting_depth: int


# ============================================================================
# P2: Call Graph
# ============================================================================

@dataclass
class CallGraphNode:
    """Node in function call graph."""
    function_name: str
    block_id: str
    callers: List[str]      # Functions that call this
    callees: List[str]      # Functions this calls
    call_depth: int         # Depth from entry points
    is_recursive: bool
    self_cycles: int        # Estimated cycles for this function only
    total_cycles: int       # Including called functions


@dataclass
class CallGraph:
    """Complete function call graph."""
    nodes: Dict[str, CallGraphNode]
    entry_functions: List[str]  # Functions not called by others
    call_chains: List[List[str]]  # All possible execution paths
    critical_path: List[str]      # Longest execution path
    max_depth: int
    has_recursion: bool


# ============================================================================
# Enhanced Block with CFG
# ============================================================================

@dataclass
class Block:
    """Function represented as a block with CFG."""
    block_id: str
    block_name: str
    inputs: List[str]
    outputs: List[str]
    internal_ops_summary: OpSummary
    estimated_cycles: int
    note: str
    position: Dict[str, str] = field(default_factory=lambda: {
        "x": "TBD by drawing tool",
        "y": "TBD by drawing tool"
    })
    cfg: Optional[ControlFlowGraph] = None  # NEW: Control flow graph


@dataclass
class Flow:
    """Execution flow metadata - Enhanced."""
    execution_order: List[str]
    parallelism: str = "unknown"
    call_graph: Optional[CallGraph] = None  # NEW: Function call graph


# ============================================================================
# Preprocessing Functions
# ============================================================================

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
        r"//.*?$"
        r"|/\*.*?\*/"
        r"|\"(\\.|[^\\\"])*\""
        r"|'(\\.|[^\\'])*'",
        re.DOTALL | re.MULTILINE,
    )
    return re.sub(pattern, replacer, source)


def normalize_compound_operators(statement: str) -> str:
    """Normalize compound operators in a single statement to simple form.

    Examples:
        i++ → i = i + 1
        i += 2 → i = i + 2
        x *= y → x = x * y

    This simplification allows the main parser to handle all operations uniformly.
    """
    post_inc = re.match(r"^\s*(?P<var>\w+)\s*(?P<op>\+\+|--)\s*$", statement)
    if post_inc:
        var = post_inc.group("var")
        op = "+" if post_inc.group("op") == "++" else "-"
        return f"{var} = {var} {op} 1"

    pre_inc = re.match(r"^\s*(?P<op>\+\+|--)\s*(?P<var>\w+)\s*$", statement)
    if pre_inc:
        var = pre_inc.group("var")
        op = "+" if pre_inc.group("op") == "++" else "-"
        return f"{var} = {var} {op} 1"

    compound = re.match(
        r"^\s*(?P<lhs>\w+)\s*(?P<op>[+\-*/%&|^]|<<|>>)=\s*(?P<rhs>.+)\s*$",
        statement,
    )
    if compound:
        lhs = compound.group("lhs")
        op = compound.group("op")
        rhs = compound.group("rhs").strip()
        return f"{lhs} = {lhs} {op} {rhs}"

    return statement


def split_statements(body: str) -> List[str]:
    """Split statements by semicolons, ignoring those within brackets/parentheses."""
    statements = []
    current = []
    paren_depth = 0
    bracket_depth = 0

    for char in body:
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(paren_depth - 1, 0)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(bracket_depth - 1, 0)
        elif char == "{":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue
        elif char == "}":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue

        if char == ";" and paren_depth == 0 and bracket_depth == 0:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def extract_parenthesized_content(text: str, open_index: int) -> Optional[str]:
    """Extract content within matching parentheses starting at open_index."""
    if open_index < 0 or open_index >= len(text) or text[open_index] != "(":
        return None
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index]
    return None


def extract_keyword_condition(statement: str, keyword: str) -> Optional[str]:
    """Extract condition expression from a keyword statement like if/while."""
    match = re.search(rf"\\b{re.escape(keyword)}\\b", statement)
    if not match:
        return None
    open_index = statement.find("(", match.end())
    if open_index == -1:
        return None
    return extract_parenthesized_content(statement, open_index)


def split_top_level_semicolons(text: str) -> List[str]:
    """Split text by semicolons, ignoring nested parentheses."""
    parts = []
    current = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        if char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def extract_for_condition(statement: str) -> Optional[str]:
    """Extract the condition expression from a for loop statement."""
    match = re.search(r"\\bfor\\b", statement)
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


# ============================================================================
# Function Parsing
# ============================================================================

def find_function_definitions(source: str) -> List[Tuple[str, str, str, str]]:
    """Find all function definitions in source code."""
    functions = []
    cleaned = strip_comments_and_strings(source)

    pattern = re.compile(
        r"(?P<ret>[\w\s\*]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*\{",
        re.MULTILINE,
    )

    for match in pattern.finditer(cleaned):
        name = match.group("name")

        # Skip C keywords
        if name in KEYWORDS:
            continue

        start = match.end() - 1
        body, end_index = extract_brace_block(cleaned, start)

        if body is None:
            continue

        ret = " ".join(match.group("ret").split())
        params = match.group("params")

        functions.append((ret, name, params, body))

    return functions


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


def parse_params(params: str) -> List[str]:
    """Extract parameter names from function signature."""
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


# ============================================================================
# Expression Parsing (Shunting-yard Algorithm) - Enhanced
# ============================================================================

OPERAND_PATTERN = (
    r'(?:'
    r'(?:\([A-Za-z_]\w*(?:\s*\*+)?\)\s*)*'  # Optional casts like (int) or (struct Foo*)
    r'(?:[+\-!~*&]+)?'                      # Optional unary operators (no whitespace)
    r'(?:[A-Za-z_]\w*|0x[0-9A-Fa-f]+|\d+)'  # Base identifier or numeric literal
    r'(?:\s*(?:\[[^\]]+\]|\.\w+|->\w+))*'   # Indexing and member access
    r')'
)
OPERAND_REGEX = re.compile(rf"^{OPERAND_PATTERN}$")


def tokenize_expression(expr: str) -> List[str]:
    """Tokenize expression including bitwise and shift operators."""
    pattern = re.compile(
        rf'{OPERAND_PATTERN}|'  # Operands with indexing/member access/casts/unary ops
        r'<<=|>>=|'             # Shift assignment (not used after normalization)
        r'<<|>>|'               # Shift operators
        r'<=|>=|==|!=|'         # Comparison operators
        r'[+\-*/%<>&|^]|'       # Arithmetic and bitwise
        r'\(|\)'                # Parentheses
    )
    return [token.strip() for token in pattern.findall(expr) if token.strip()]


def is_operand(token: str) -> bool:
    """Check if token is an operand."""
    return bool(OPERAND_REGEX.match(token) or re.match(r"tmp_\d+|cond_\d+", token))


def classify_operator(op: str) -> str:
    """Classify operator into operation type."""
    if op in {"<", ">", "<=", ">=", "==", "!="}:
        return "compare"
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
    """Parse expression using Shunting-yard algorithm.

    Enhanced to support bitwise and shift operators.
    """
    tokens = tokenize_expression(expr)
    ops: List[Operation] = []
    edges: List[Signal] = []
    op_index = op_index_start

    # Phase 1: Convert infix to postfix
    output_queue: List[str] = []
    operator_stack: List[str] = []

    for token in tokens:
        if is_operand(token):
            output_queue.append(token)
        elif token in OPERATORS:
            while (
                operator_stack
                and operator_stack[-1] in OPERATORS
                and OPERATOR_PRECEDENCE[operator_stack[-1]] >= OPERATOR_PRECEDENCE[token]
            ):
                output_queue.append(operator_stack.pop())
            operator_stack.append(token)
        elif token == "(":
            operator_stack.append(token)
        elif token == ")":
            while operator_stack and operator_stack[-1] != "(":
                output_queue.append(operator_stack.pop())
            if operator_stack and operator_stack[-1] == "(":
                operator_stack.pop()

    while operator_stack:
        op_token = operator_stack.pop()
        if op_token != "(":
            output_queue.append(op_token)

    # Phase 2: Evaluate postfix and create operations
    eval_stack: List[str] = []

    for token in output_queue:
        if is_operand(token):
            eval_stack.append(token)
            continue

        if token not in OPERATORS or len(eval_stack) < 2:
            continue

        right = eval_stack.pop()
        left = eval_stack.pop()

        op_type = classify_operator(token)
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

        # Track data flow
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
        eval_stack.append(output_name)

    last_output = eval_stack[-1] if eval_stack else None

    if output_target and ops:
        ops[-1].outputs = [output_target]
        var_producers[output_target] = ("operation", ops[-1].op_id)
        last_output = output_target

    return ops, op_index, last_output, edges


# ============================================================================
# P1: Simple Assignment Handling
# ============================================================================

def handle_simple_assignment(
    lhs: str,
    rhs: str,
    block_id: str,
    op_index: int,
    var_producers: Dict[str, Tuple[str, str]]
) -> Tuple[List[Operation], int, List[Signal]]:
    """Handle simple assignment: var = other_var.

    Creates a "copy" operation to maintain complete data flow tracking.
    """
    operations = []
    edges = []

    # rhs must be a simple identifier
    if re.match(r'^\w+$', rhs.strip()):
        op_id = f"{block_id}_op_{op_index}"
        op_index += 1

        operation = Operation(
            op_id=op_id,
            op_type="copy",
            inputs=[rhs],
            outputs=[lhs],
            parent_block_id=block_id
        )
        operations.append(operation)

        # Track data flow
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
                    comment="copy flow"
                )
            )

        var_producers[lhs] = ("operation", op_id)

    return operations, op_index, edges


# ============================================================================
# Operation Extraction - Enhanced
# ============================================================================

def extract_operations(
    body: str,
    block_id: str,
    block_inputs: List[str],
    has_return: bool
) -> Tuple[List[Operation], List[Signal], OpSummary]:
    """Extract all operations from function body.

    Enhanced with:
    - Compound operator normalization
    - Simple assignment handling
    - Bitwise and shift operation support
    """
    # 1. Remove comments and strings
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
        # Normalize C-style declarations with assignment: "int y = x" -> "y = x"
        declaration_match = re.match(
            r"(?P<type>(?:"
            r"(?:typedef|const|volatile|unsigned|signed|short|long|int|float|double|"
            r"char|bool|void|size_t|ssize_t|uint\d+_t|int\d+_t|[\w_]+)\s+|"
            r"struct\s+\w+\s+|enum\s+\w+\s+|\s*\*\s*"
            r")+)"
            r"(?P<lhs>[\w_\s\*\[\]]+)\s*=\s*(?P<rhs>.+)",
            statement
        )
        if declaration_match:
            lhs = re.sub(r"\s*\*\s*", "", declaration_match.group("lhs"))
            lhs = re.sub(r"\[.*?\]", "", lhs).strip()
            statement = (
                f"{lhs} = "
                f"{declaration_match.group('rhs').strip()}"
            )

        # Assignment: var = expr
        assignment_match = re.match(r"(?P<lhs>\w+)\s*=(?!=)\s*(?P<rhs>.+)", statement)
        if assignment_match:
            lhs = assignment_match.group("lhs")
            rhs = assignment_match.group("rhs").strip()

            # Check if simple assignment
            if re.match(r'^\w+$', rhs):
                # Simple: a = b
                ops, op_index, new_edges = handle_simple_assignment(
                    lhs, rhs, block_id, op_index, var_producers
                )
                operations.extend(ops)
                edges.extend(new_edges)
            else:
                # Expression: a = b + c
                ops, op_index, _, new_edges = parse_expression_ops(
                    rhs, block_id, op_index, var_producers, output_target=lhs
                )
                operations.extend(ops)
                edges.extend(new_edges)

                if ops:
                    var_producers[lhs] = ("operation", ops[-1].op_id)
            continue

        # Return statement
        return_match = re.match(r"\breturn\b(?P<expr>.+)", statement)
        if return_match and has_return:
            expr = return_match.group("expr").strip()

            # Simple return variable
            if re.match(r'^\w+$', expr):
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
                            comment="direct return"
                        )
                    )
            else:
                # Return expression
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

        # Conditional expression (for/if/while)
        for_condition = extract_for_condition(statement)
        if for_condition is not None:
            output_name = f"cond_{condition_counter}"
            condition_counter += 1
            ops, op_index, _, new_edges = parse_expression_ops(
                for_condition, block_id, op_index, var_producers, output_target=output_name
            )
            operations.extend(ops)
            edges.extend(new_edges)
            continue

        for keyword in ("if", "while"):
            condition_expr = extract_keyword_condition(statement, keyword)
            if condition_expr is not None:
                output_name = f"cond_{condition_counter}"
                condition_counter += 1
                ops, op_index, _, new_edges = parse_expression_ops(
                    condition_expr, block_id, op_index, var_producers, output_target=output_name
                )
                operations.extend(ops)
                edges.extend(new_edges)
                break

    # Build summary
    summary = OpSummary()
    for op in operations:
        if op.op_type == "add":
            summary.add += 1
        elif op.op_type == "compare":
            summary.compare += 1
        elif op.op_type == "multiply":
            summary.multiply += 1
        elif op.op_type == "copy":
            summary.copy += 1
        elif op.op_type == "shift":
            summary.shift += 1
        elif op.op_type == "bitwise":
            summary.bitwise += 1

    return operations, edges, summary


# ============================================================================
# Cycle Estimation - Enhanced
# ============================================================================

def estimate_cycles(summary: OpSummary, rules: CycleRules) -> int:
    """Estimate execution cycles based on operation counts."""
    cycles = 0
    cycles += (summary.add + rules.add_per_cycle - 1) // rules.add_per_cycle
    cycles += (summary.compare + rules.compare_per_cycle - 1) // rules.compare_per_cycle
    cycles += (summary.multiply + rules.mul_per_cycle - 1) // rules.mul_per_cycle
    cycles += (summary.copy + rules.copy_per_cycle - 1) // rules.copy_per_cycle
    cycles += (summary.shift + rules.shift_per_cycle - 1) // rules.shift_per_cycle
    cycles += (summary.bitwise + rules.bitwise_per_cycle - 1) // rules.bitwise_per_cycle
    return cycles


# ============================================================================
# P2: Control Flow Analysis
# ============================================================================

def detect_control_notes(body: str) -> str:
    """Detect loops and conditionals in function body."""
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


def analyze_control_flow(body: str, function_name: str, operations: List[Operation]) -> ControlFlowGraph:
    """Analyze control flow and build CFG.

    This is a simplified CFG builder that identifies:
    - Sequential execution
    - Conditional branches (if statements)
    - Loops (for, while)

    Note: Full CFG would require more sophisticated parsing.
    For now, we provide structural analysis.
    """
    cleaned = strip_comments_and_strings(body)

    # Detect control structures
    has_if = bool(re.search(r"\bif\b", cleaned))
    has_loop = bool(re.search(r"\b(for|while|do)\b", cleaned))

    # Count nesting depth (simplified)
    max_depth = cleaned.count("{")

    blocks: List[BasicBlock] = []
    loops: List[LoopInfo] = []

    block_index = 0
    loop_index = 0
    pending_ops = [op.op_id for op in operations]

    def make_block(block_type: str, operations_list: Optional[List[str]] = None) -> BasicBlock:
        nonlocal block_index
        block_index += 1
        block = BasicBlock(
            block_id=f"{function_name}_b{block_index}",
            parent_function=function_name,
            operations=operations_list or [],
            predecessors=[],
            successors=[],
            block_type=block_type
        )
        blocks.append(block)
        return block

    def connect(from_block: BasicBlock, to_block: BasicBlock) -> None:
        if to_block.block_id not in from_block.successors:
            from_block.successors.append(to_block.block_id)
        if from_block.block_id not in to_block.predecessors:
            to_block.predecessors.append(from_block.block_id)

    def assign_pending_ops(target_block: BasicBlock) -> None:
        nonlocal pending_ops
        if pending_ops:
            target_block.operations = pending_ops
            pending_ops = []

    entry = make_block("entry")
    current = entry

    control_pattern = re.compile(r"\b(if|else|for|while|do)\b")
    matches = list(control_pattern.finditer(cleaned))

    def has_else_between(start: int, end: int) -> bool:
        return bool(re.search(r"\belse\b", cleaned[start:end]))

    for idx, match in enumerate(matches):
        keyword = match.group(1)
        if keyword == "else":
            continue

        if pending_ops and current == entry:
            seq_block = make_block("sequential")
            assign_pending_ops(seq_block)
            connect(current, seq_block)
            current = seq_block

        if keyword == "if":
            next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned)
            has_else_branch = has_else_between(match.end(), next_start)

            cond_block = make_block("conditional_branch")
            connect(current, cond_block)

            then_block = make_block("sequential")
            assign_pending_ops(then_block)
            connect(cond_block, then_block)

            if has_else_branch:
                else_block = make_block("sequential")
                connect(cond_block, else_block)
            else:
                else_block = None

            merge_block = make_block("sequential")
            connect(then_block, merge_block)
            if else_block:
                connect(else_block, merge_block)
            else:
                connect(cond_block, merge_block)

            current = merge_block

        elif keyword in {"for", "while", "do"}:
            loop_index += 1
            header_block = make_block("loop_header")
            connect(current, header_block)

            body_block = make_block("loop_body")
            assign_pending_ops(body_block)
            connect(header_block, body_block)

            exit_block = make_block("sequential")
            connect(header_block, exit_block)
            connect(body_block, header_block)

            loops.append(LoopInfo(
                loop_id=f"{function_name}_loop_{loop_index}",
                header_block=header_block.block_id,
                body_blocks=[body_block.block_id],
                exit_blocks=[exit_block.block_id],
                nesting_level=1,
                estimated_iterations="unknown"
            ))

            current = exit_block

    if pending_ops:
        tail_block = make_block("sequential")
        assign_pending_ops(tail_block)
        connect(current, tail_block)
        current = tail_block

    exit_block = make_block("exit")
    connect(current, exit_block)

    return ControlFlowGraph(
        function_name=function_name,
        basic_blocks=blocks,
        entry_block=entry.block_id,
        exit_blocks=[exit_block.block_id],
        loops=loops,
        has_branches=has_if,
        max_nesting_depth=max(1, max_depth // 2)
    )


# ============================================================================
# P2: Function Call Analysis
# ============================================================================

def find_function_calls(
    body: str,
    known_functions: Iterable[str]
) -> List[Tuple[str, List[str], Optional[str]]]:
    """Find function calls within known functions."""
    cleaned = strip_comments_and_strings(body)
    known = set(known_functions)
    calls = []

    call_pattern = re.compile(
        r"(?P<lhs>\w+\s*=\s*)?(?P<name>\w+)\s*\((?P<args>[^)]*)\)"
    )

    for match in call_pattern.finditer(cleaned):
        name = match.group("name")

        if name in KEYWORDS:
            continue

        if name not in known:
            continue

        args = [arg.strip() for arg in match.group("args").split(",") if arg.strip()]

        lhs = match.group("lhs")
        assigned = lhs.split("=")[0].strip() if lhs else None

        calls.append((name, args, assigned))

    return calls


def build_call_graph(
    functions: List[Tuple[str, str, str, str]],
    block_ids: Dict[str, str],
    blocks: List[Block]
) -> CallGraph:
    """Build function call graph with dependency analysis.

    Analyzes:
    - Call relationships
    - Call depth
    - Recursion detection
    - Critical path (longest execution chain)
    """
    nodes: Dict[str, CallGraphNode] = {}

    # Initialize nodes
    for _, name, _, _ in functions:
        block = next((b for b in blocks if b.block_name == name), None)
        nodes[name] = CallGraphNode(
            function_name=name,
            block_id=block_ids[name],
            callers=[],
            callees=[],
            call_depth=0,
            is_recursive=False,
            self_cycles=block.estimated_cycles if block else 0,
            total_cycles=0
        )

    # Build call relationships
    for _, caller_name, _, body in functions:
        calls = find_function_calls(body, block_ids.keys())

        for callee_name, _, _ in calls:
            if callee_name not in nodes:
                continue

            nodes[caller_name].callees.append(callee_name)
            nodes[callee_name].callers.append(caller_name)

    # Find entry functions (not called by anyone)
    entry_functions = [name for name, node in nodes.items() if not node.callers]

    # Calculate call depth and detect recursion using DFS with recursion stack
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

    # Calculate total cycles (bottom-up)
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

    # Find critical path (DFS from entry points)
    critical_path = []
    max_cycles = 0

    def find_longest_path(func_name: str, path: List[str]) -> Tuple[List[str], int]:
        if func_name in path:  # Recursion
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

    # Find all call chains (simplified - just entry to leaf)
    call_chains = []
    for entry in entry_functions:
        chain, _ = find_longest_path(entry, [])
        call_chains.append(chain)

    has_recursion = any(node.is_recursive for node in nodes.values())

    return CallGraph(
        nodes=nodes,
        entry_functions=entry_functions,
        call_chains=call_chains,
        critical_path=critical_path,
        max_depth=max_depth,
        has_recursion=has_recursion
    )


# ============================================================================
# Model Building - Enhanced
# ============================================================================

def build_model(source: str, rules: CycleRules) -> Dict[str, object]:
    """Build complete enhanced model with P1+P2 features."""

    region, found = extract_cvas_region(source)
    if not found:
        print(f"WARNING: {MARKER_START} ~ {MARKER_END} region not found", file=sys.stderr)
        return {
            "blocks": [],
            "operations": [],
            "signals": [],
            "flow": {"execution_order": [], "parallelism": "unknown"},
            "diagram_hint": {"layout": "TBD by drawing tool"},
            "note": f"{MARKER_START}/{MARKER_END} region not found or empty",
        }

    functions = find_function_definitions(region)
    if not functions:
        print("WARNING: No functions found in CVAS region", file=sys.stderr)
        return {
            "blocks": [],
            "operations": [],
            "signals": [],
            "flow": {"execution_order": [], "parallelism": "unknown"},
            "diagram_hint": {"layout": "TBD by drawing tool"},
            "note": "No functions found in CVAS region",
        }

    block_ids = {name: f"B{idx + 1}" for idx, (_, name, _, _) in enumerate(functions)}

    blocks: List[Block] = []
    operations: List[Operation] = []
    signals: List[Signal] = []

    # Process each function
    for ret_type, name, params, body in functions:
        inputs = parse_params(params)
        outputs = []
        if ret_type and ret_type.strip() != "void":
            outputs.append("return")

        block_id = block_ids[name]

        # Extract operations with P1 enhancements
        block_operations, op_edges, summary = extract_operations(
            body, block_id, inputs, bool(outputs)
        )
        operations.extend(block_operations)
        signals.extend(op_edges)

        # Estimate cycles
        cycles = estimate_cycles(summary, rules)

        # Build note
        control_note = detect_control_notes(body)
        note = f"{control_note}; internal op nodes emitted"

        # P2: Build CFG
        cfg = analyze_control_flow(body, name, block_operations)

        # Create block with CFG
        blocks.append(
            Block(
                block_id=block_id,
                block_name=name,
                inputs=inputs,
                outputs=outputs,
                internal_ops_summary=summary,
                estimated_cycles=cycles,
                note=note,
                cfg=cfg
            )
        )

    # Analyze function calls for inter-block signals
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

    # P2: Build call graph
    call_graph = build_call_graph(functions, block_ids, blocks)

    # Enhanced flow with call graph
    flow = Flow(
        execution_order=[block.block_id for block in blocks],
        parallelism="sequential",  # Can be enhanced with dependency analysis
        call_graph=call_graph
    )

    return {
        "blocks": [serialize_block(block) for block in blocks],
        "operations": [asdict(operation) for operation in operations],
        "signals": [asdict(signal) for signal in signals],
        "flow": serialize_flow(flow),
        "diagram_hint": {"layout": "TBD by drawing tool"},
        "note": "Enhanced with P1+P2: complete data flow, CFG, call graph",
        "analysis_version": "2.0"
    }


def serialize_block(block: Block) -> Dict[str, object]:
    """Serialize block with nested structures."""
    data = asdict(block)
    data["internal_ops_summary"] = asdict(block.internal_ops_summary)

    # Serialize CFG if present
    if block.cfg:
        data["cfg"] = {
            "function_name": block.cfg.function_name,
            "basic_blocks": [asdict(bb) for bb in block.cfg.basic_blocks],
            "entry_block": block.cfg.entry_block,
            "exit_blocks": block.cfg.exit_blocks,
            "loops": [asdict(loop) for loop in block.cfg.loops],
            "has_branches": block.cfg.has_branches,
            "max_nesting_depth": block.cfg.max_nesting_depth
        }

    return data


def serialize_flow(flow: Flow) -> Dict[str, object]:
    """Serialize flow with call graph."""
    data = {
        "execution_order": flow.execution_order,
        "parallelism": flow.parallelism
    }

    if flow.call_graph:
        data["call_graph"] = {
            "nodes": {
                name: asdict(node)
                for name, node in flow.call_graph.nodes.items()
            },
            "entry_functions": flow.call_graph.entry_functions,
            "call_chains": flow.call_graph.call_chains,
            "critical_path": flow.call_graph.critical_path,
            "max_depth": flow.call_graph.max_depth,
            "has_recursion": flow.call_graph.has_recursion
        }

    return data


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="CVAS Enhanced v2.0 - C-model block diagram parser with advanced analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s model.c -o output.json
  %(prog)s model.c --cycle-config cycle.json
  %(prog)s model.c --add-per-cycle 8 --mul-per-cycle 2

New in v2.0:
  - Complete data flow tracking (simple assignments, compound operators)
  - Control Flow Graph (CFG) analysis
  - Function call graph with critical path detection
  - Bitwise and shift operation support
        """
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Path to C source file"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output JSON path (default: stdout)"
    )
    parser.add_argument(
        "--cycle-config",
        type=Path,
        help="JSON file with cycle rules"
    )
    parser.add_argument(
        "--add-per-cycle",
        type=int,
        help="Override add operations per cycle"
    )
    parser.add_argument(
        "--compare-per-cycle",
        type=int,
        help="Override compare operations per cycle"
    )
    parser.add_argument(
        "--mul-per-cycle",
        type=int,
        help="Override multiply operations per cycle"
    )
    parser.add_argument(
        "--copy-per-cycle",
        type=int,
        help="Override copy operations per cycle"
    )
    parser.add_argument(
        "--shift-per-cycle",
        type=int,
        help="Override shift operations per cycle"
    )
    parser.add_argument(
        "--bitwise-per-cycle",
        type=int,
        help="Override bitwise operations per cycle"
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Load cycle rules
    rules = CycleRules()

    if args.cycle_config:
        if not args.cycle_config.exists():
            print(f"ERROR: Cycle config file '{args.cycle_config}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            rules = CycleRules.from_json(args.cycle_config)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"ERROR: Invalid cycle config: {e}", file=sys.stderr)
            sys.exit(1)

    # Apply CLI overrides
    if args.add_per_cycle is not None:
        rules.add_per_cycle = args.add_per_cycle
    if args.compare_per_cycle is not None:
        rules.compare_per_cycle = args.compare_per_cycle
    if args.mul_per_cycle is not None:
        rules.mul_per_cycle = args.mul_per_cycle
    if args.copy_per_cycle is not None:
        rules.copy_per_cycle = args.copy_per_cycle
    if args.shift_per_cycle is not None:
        rules.shift_per_cycle = args.shift_per_cycle
    if args.bitwise_per_cycle is not None:
        rules.bitwise_per_cycle = args.bitwise_per_cycle

    # Validate
    try:
        rules.validate()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Read input
    if not args.input.exists():
        print(f"ERROR: Input file '{args.input}' not found", file=sys.stderr)
        sys.exit(1)

    try:
        source = args.input.read_text(encoding='utf-8')
    except UnicodeDecodeError as e:
        print(f"ERROR: Failed to read input file: {e}", file=sys.stderr)
        sys.exit(1)

    # Build model
    print("Building enhanced model with P1+P2 analysis...", file=sys.stderr)
    model = build_model(source, rules)

    # Output JSON
    output = json.dumps(model, indent=2, ensure_ascii=False)

    if args.output:
        try:
            args.output.write_text(output, encoding='utf-8')
            print(f"✓ Analysis complete. Output written to {args.output}", file=sys.stderr)

            # Print summary
            num_blocks = len(model.get('blocks', []))
            num_ops = len(model.get('operations', []))
            num_signals = len(model.get('signals', []))

            print(f"✓ Analyzed {num_blocks} functions", file=sys.stderr)
            print(f"✓ Extracted {num_ops} operations", file=sys.stderr)
            print(f"✓ Tracked {num_signals} data flows", file=sys.stderr)

            flow = model.get('flow', {})
            if 'call_graph' in flow:
                cg = flow['call_graph']
                print(f"✓ Call graph: {len(cg.get('nodes', {}))} nodes, depth {cg.get('max_depth', 0)}", file=sys.stderr)
                if cg.get('critical_path'):
                    print(f"✓ Critical path: {' → '.join(cg['critical_path'])}", file=sys.stderr)

        except IOError as e:
            print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output)


if __name__ == "__main__":
    main()
