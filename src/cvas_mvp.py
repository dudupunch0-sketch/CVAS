#!/usr/bin/env python3
"""CVAS MVP - C-model Block Diagram Parser

Parses C code between CVAS_START and CVAS_END markers to generate
structured JSON for block diagram visualization tools.

Features:
- Shunting-yard algorithm for accurate expression parsing
- Complete data flow tracking with intermediate variables
- Configurable cycle estimation rules
- Rich signal metadata (source/destination types, directions)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ============================================================================
# Constants
# ============================================================================

MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"

# Operator precedence (higher = higher priority)
OPERATOR_PRECEDENCE = {
    "*": 3, "/": 3,           # Multiply, divide
    "+": 2, "-": 2,           # Add, subtract
    "<": 1, ">": 1,           # Comparison
    "<=": 1, ">=": 1,
    "==": 1, "!=": 1,
}

OPERATORS = set(OPERATOR_PRECEDENCE.keys())

# Keywords to exclude from function call detection
KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "do"}


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class CycleRules:
    """Hardware cycle estimation rules."""
    add_per_cycle: int = 4
    compare_per_cycle: int = 4
    mul_per_cycle: int = 1

    @classmethod
    def from_json(cls, path: Path) -> "CycleRules":
        """Load cycle rules from JSON file."""
        data = json.loads(path.read_text(encoding='utf-8'))
        return cls(
            add_per_cycle=int(data.get("add_per_cycle", cls.add_per_cycle)),
            compare_per_cycle=int(data.get("compare_per_cycle", cls.compare_per_cycle)),
            mul_per_cycle=int(data.get("mul_per_cycle", cls.mul_per_cycle)),
        )

    def validate(self) -> None:
        """Validate cycle rules are positive."""
        if self.add_per_cycle <= 0 or self.compare_per_cycle <= 0 or self.mul_per_cycle <= 0:
            raise ValueError("All cycle rules must be positive integers")


@dataclass
class OpSummary:
    """Summary of operations by type."""
    add: int = 0
    compare: int = 0
    multiply: int = 0

    def total(self) -> int:
        """Return total operation count."""
        return self.add + self.compare + self.multiply


@dataclass
class Operation:
    """Single operation node within a block."""
    op_id: str
    op_type: str  # "add", "compare", "multiply"
    inputs: List[str]
    outputs: List[str]
    parent_block_id: str


@dataclass
class Block:
    """Function represented as a block."""
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


@dataclass
class Flow:
    """Execution flow metadata."""
    execution_order: List[str]
    parallelism: str = "unknown"


# ============================================================================
# Preprocessing Functions
# ============================================================================

def extract_cvas_region(source: str) -> Tuple[str, bool]:
    """Extract code between CVAS_START and CVAS_END markers.

    Returns:
        (extracted_code, success)
    """
    start_index = source.find(MARKER_START)
    end_index = source.find(MARKER_END)

    if start_index == -1 or end_index == -1:
        return "", False

    if end_index <= start_index:
        print(f"WARNING: {MARKER_END} appears before {MARKER_START}", file=sys.stderr)
        return "", False

    return source[start_index + len(MARKER_START) : end_index], True


def strip_comments_and_strings(source: str) -> str:
    """Remove comments and string literals while preserving positions.

    This is important for accurate line/column tracking in error messages.
    """
    def replacer(match: re.Match[str]) -> str:
        # Replace with spaces to preserve character positions
        return " " * len(match.group(0))

    # Combined pattern for comments and strings
    pattern = re.compile(
        r"//.*?$"                    # Line comments
        r"|/\*.*?\*/"                # Block comments
        r"|\"(\\.|[^\\\"])*\""       # Double-quoted strings
        r"|'(\\.|[^\\'])*'",         # Single-quoted chars
        re.DOTALL | re.MULTILINE,
    )
    return re.sub(pattern, replacer, source)


# ============================================================================
# Function Parsing
# ============================================================================

def find_function_definitions(source: str) -> List[Tuple[str, str, str, str]]:
    """Find all function definitions in source code.

    Returns:
        List of (return_type, name, params, body) tuples
    """
    functions = []
    cleaned = strip_comments_and_strings(source)

    # Match function signature
    pattern = re.compile(
        r"(?P<ret>[\w\s\*]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*\{",
        re.MULTILINE,
    )

    for match in pattern.finditer(cleaned):
        name = match.group("name")

        # Skip C keywords that look like function calls
        if name in KEYWORDS:
            continue

        start = match.end() - 1  # Position of opening brace
        body, end_index = extract_brace_block(cleaned, start)

        if body is None:
            continue

        ret = " ".join(match.group("ret").split())
        params = match.group("params")

        functions.append((ret, name, params, body))

    return functions


def extract_brace_block(source: str, start_index: int) -> Tuple[Optional[str], int]:
    """Extract content within matching braces.

    Returns:
        (block_content, end_position) or (None, start_index) on failure
    """
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
    """Extract parameter names from function signature.

    Examples:
        "int a, float b" -> ["a", "b"]
        "void" -> []
        "char *str, int len" -> ["str", "len"]
    """
    params = params.strip()
    if not params or params == "void":
        return []

    result = []
    for param in params.split(","):
        param = param.strip()
        if not param:
            continue

        # Last token is the parameter name
        tokens = param.split()
        name = tokens[-1].replace("*", "").strip()
        result.append(name)

    return result


# ============================================================================
# Expression Parsing (Shunting-yard Algorithm)
# ============================================================================

def tokenize_expression(expr: str) -> List[str]:
    """Tokenize expression into identifiers, numbers, and operators.

    Examples:
        "a + b * 2" -> ["a", "+", "b", "*", "2"]
        "x <= y" -> ["x", "<=", "y"]
    """
    # Pattern matches: identifiers, numbers, operators (including <=, >=, ==, !=), parens
    token_pattern = re.compile(r"[A-Za-z_]\w*|\d+|<=|>=|==|!=|[+\-*/<>]|\(|\)")
    return token_pattern.findall(expr)


def is_operand(token: str) -> bool:
    """Check if token is an operand (variable, number, or temp var)."""
    return bool(re.match(r"[A-Za-z_]\w*|\d+|tmp_\d+|cond_\d+", token))


def classify_operator(op: str) -> str:
    """Classify operator into operation type."""
    if op in {"<", ">", "<=", ">=", "==", "!="}:
        return "compare"
    elif op in {"*", "/"}:
        return "multiply"
    elif op in {"+", "-"}:
        return "add"
    else:
        return "unknown"


def parse_expression_ops(
    expr: str,
    block_id: str,
    op_index_start: int,
    var_producers: Dict[str, Tuple[str, str]],
    output_target: Optional[str] = None,
) -> Tuple[List[Operation], int, Optional[str], List[Signal]]:
    """Parse expression using Shunting-yard algorithm.

    This converts infix notation to postfix (RPN) then evaluates it,
    creating Operation nodes and tracking data flow.

    Args:
        expr: Expression to parse (e.g., "a + b * c")
        block_id: Parent block identifier
        op_index_start: Starting index for operation IDs
        var_producers: Map of variable -> (source_type, source_id)
        output_target: Optional target variable name for result

    Returns:
        (operations, next_op_index, result_var, signals)
    """
    tokens = tokenize_expression(expr)
    ops: List[Operation] = []
    edges: List[Signal] = []
    op_index = op_index_start

    # Phase 1: Convert infix to postfix using Shunting-yard
    output_queue: List[str] = []
    operator_stack: List[str] = []

    for token in tokens:
        if is_operand(token):
            output_queue.append(token)
        elif token in OPERATORS:
            # Pop higher or equal precedence operators
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
            # Pop until matching '('
            while operator_stack and operator_stack[-1] != "(":
                output_queue.append(operator_stack.pop())
            if operator_stack and operator_stack[-1] == "(":
                operator_stack.pop()

    # Pop remaining operators
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

        # Must be an operator with 2 operands
        if token not in OPERATORS or len(eval_stack) < 2:
            continue

        right = eval_stack.pop()
        left = eval_stack.pop()

        op_type = classify_operator(token)
        op_id = f"{block_id}_op_{op_index}"
        op_index += 1
        output_name = f"tmp_{op_index}"

        # Create operation
        operation = Operation(
            op_id=op_id,
            op_type=op_type,
            inputs=[left, right],
            outputs=[output_name],
            parent_block_id=block_id,
        )
        ops.append(operation)

        # Track data flow for each input
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

        # Register this operation as producer of output
        var_producers[output_name] = ("operation", op_id)
        eval_stack.append(output_name)

    # Get final result
    last_output = eval_stack[-1] if eval_stack else None

    # Rename last operation's output if target specified
    if output_target and ops:
        ops[-1].outputs = [output_target]
        var_producers[output_target] = ("operation", ops[-1].op_id)
        last_output = output_target

    return ops, op_index, last_output, edges


# ============================================================================
# Operation Extraction
# ============================================================================

def extract_operations(
    body: str,
    block_id: str,
    block_inputs: List[str],
    has_return: bool
) -> Tuple[List[Operation], List[Signal], OpSummary]:
    """Extract all operations from function body.

    Processes:
    - Assignments: var = expr
    - Return statements: return expr
    - Conditionals: if (expr), while (expr)

    Returns:
        (operations, signals, operation_summary)
    """
    cleaned = strip_comments_and_strings(body)
    operations: List[Operation] = []
    edges: List[Signal] = []

    # Track which operation produces each variable
    var_producers: Dict[str, Tuple[str, str]] = {
        name: ("block", block_id) for name in block_inputs
    }

    op_index = 1
    condition_counter = 1

    # Split into statements (simple approach - may need refinement)
    statements = [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]

    for statement in statements:
        # Assignment: var = expr
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

        # Return statement
        return_match = re.match(r"\breturn\b(?P<expr>.+)", statement)
        if return_match and has_return:
            expr = return_match.group("expr").strip()

            ops, op_index, _, new_edges = parse_expression_ops(
                expr, block_id, op_index, var_producers, output_target="return"
            )
            operations.extend(ops)
            edges.extend(new_edges)

            if ops:
                # Add signal from last operation to block output
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

        # Conditional expression
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

    # Build operation summary
    summary = OpSummary()
    for op in operations:
        if op.op_type == "add":
            summary.add += 1
        elif op.op_type == "compare":
            summary.compare += 1
        elif op.op_type == "multiply":
            summary.multiply += 1

    return operations, edges, summary


# ============================================================================
# Cycle Estimation
# ============================================================================

def estimate_cycles(summary: OpSummary, rules: CycleRules) -> int:
    """Estimate execution cycles based on operation counts.

    Uses ceiling division: ceil(count / per_cycle)
    Implemented as: (count + per_cycle - 1) // per_cycle
    """
    add_cycles = (summary.add + rules.add_per_cycle - 1) // rules.add_per_cycle
    compare_cycles = (summary.compare + rules.compare_per_cycle - 1) // rules.compare_per_cycle
    mul_cycles = (summary.multiply + rules.mul_per_cycle - 1) // rules.mul_per_cycle

    return add_cycles + compare_cycles + mul_cycles


# ============================================================================
# Control Flow Detection
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


# ============================================================================
# Function Call Analysis
# ============================================================================

def find_function_calls(
    body: str,
    known_functions: Iterable[str]
) -> List[Tuple[str, List[str], Optional[str]]]:
    """Find function calls within known functions.

    Returns:
        List of (function_name, arguments, assigned_variable)
    """
    cleaned = strip_comments_and_strings(body)
    known = set(known_functions)
    calls = []

    # Match: [lhs =] function_name(args)
    call_pattern = re.compile(
        r"(?P<lhs>\w+\s*=\s*)?(?P<name>\w+)\s*\((?P<args>[^)]*)\)"
    )

    for match in call_pattern.finditer(cleaned):
        name = match.group("name")

        # Skip keywords
        if name in KEYWORDS:
            continue

        # Only track known functions
        if name not in known:
            continue

        # Parse arguments
        args = [arg.strip() for arg in match.group("args").split(",") if arg.strip()]

        # Get assigned variable if present
        lhs = match.group("lhs")
        assigned = lhs.split("=")[0].strip() if lhs else None

        calls.append((name, args, assigned))

    return calls


# ============================================================================
# Model Building
# ============================================================================

def build_model(source: str, rules: CycleRules) -> Dict[str, object]:
    """Build complete block diagram model from C source.

    Main orchestration function that:
    1. Extracts CVAS region
    2. Finds function definitions
    3. Creates blocks and operations
    4. Tracks signals and data flow
    5. Analyzes function calls
    """
    # Extract CVAS region
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

    # Find all functions
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

    # Assign block IDs
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

        # Extract operations and internal signals
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

        # Create block
        blocks.append(
            Block(
                block_id=block_id,
                block_name=name,
                inputs=inputs,
                outputs=outputs,
                internal_ops_summary=summary,
                estimated_cycles=cycles,
                note=note,
            )
        )

    # Analyze function calls for inter-block signals
    for _, caller_name, _, body in functions:
        caller_id = block_ids[caller_name]
        calls = find_function_calls(body, block_ids.keys())

        for callee_name, args, assigned in calls:
            callee_id = block_ids[callee_name]

            # Create signals for arguments
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

            # Create signal for return value if assigned
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

    # Build flow metadata
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
    """Serialize block with nested OpSummary."""
    data = asdict(block)
    data["internal_ops_summary"] = asdict(block.internal_ops_summary)
    return data


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="CVAS MVP - C-model block diagram parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s model.c -o output.json
  %(prog)s model.c --cycle-config cycle.json
  %(prog)s model.c --add-per-cycle 8 --mul-per-cycle 2
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

    # Validate rules
    try:
        rules.validate()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Read input file
    if not args.input.exists():
        print(f"ERROR: Input file '{args.input}' not found", file=sys.stderr)
        sys.exit(1)

    try:
        source = args.input.read_text(encoding='utf-8')
    except UnicodeDecodeError as e:
        print(f"ERROR: Failed to read input file: {e}", file=sys.stderr)
        sys.exit(1)

    # Build model
    model = build_model(source, rules)

    # Output JSON
    output = json.dumps(model, indent=2, ensure_ascii=False)

    if args.output:
        try:
            args.output.write_text(output, encoding='utf-8')
            print(f"Analysis complete. Output written to {args.output}", file=sys.stderr)
        except IOError as e:
            print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output)


if __name__ == "__main__":
    main()
