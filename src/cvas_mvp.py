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
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from c_ast_utils import parse_statement, parse_translation_unit

# ============================================================================
# Constants
# ============================================================================

MARKER_START = "CVAS_START"
MARKER_END = "CVAS_END"

# Operator precedence (higher = higher priority, aligned with C standard)
OPERATOR_PRECEDENCE = {
    "*": 11,
    "/": 11,
    "%": 11,  # Multiplicative
    "+": 10,
    "-": 10,  # Additive
    "<<": 9,
    ">>": 9,  # Shift
    "<": 8,
    ">": 8,
    "<=": 8,
    ">=": 8,  # Relational
    "==": 7,
    "!=": 7,  # Equality
    "&": 6,  # Bitwise AND
    "^": 5,  # Bitwise XOR
    "|": 4,  # Bitwise OR
    "&&": 3,  # Logical AND
    "||": 2,  # Logical OR
    "?:": 1,  # Ternary conditional
}

OPERATORS = set(OPERATOR_PRECEDENCE.keys())

# Keywords to exclude from function call detection
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


# ============================================================================
# Data Models - Enhanced
# ============================================================================


@dataclass
class CycleRules:
    """Hardware cycle estimation rules - Extended for new operation types."""

    add_per_cycle: int = 4
    compare_per_cycle: int = 4
    logic_per_cycle: int = 4
    mul_per_cycle: int = 1
    copy_per_cycle: int = 8  # Simple assignments are very fast
    shift_per_cycle: int = 2  # Bit shifts
    bitwise_per_cycle: int = 4  # Bitwise operations
    const_per_cycle: int = 8  # Literal assignments
    load_per_cycle: int = 4  # Memory reads
    store_per_cycle: int = 4  # Memory writes

    @classmethod
    def from_json(cls, path: Path) -> "CycleRules":
        """Load cycle rules from JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            add_per_cycle=int(data.get("add_per_cycle", cls.add_per_cycle)),
            compare_per_cycle=int(data.get("compare_per_cycle", cls.compare_per_cycle)),
            logic_per_cycle=int(data.get("logic_per_cycle", cls.logic_per_cycle)),
            mul_per_cycle=int(data.get("mul_per_cycle", cls.mul_per_cycle)),
            copy_per_cycle=int(data.get("copy_per_cycle", cls.copy_per_cycle)),
            shift_per_cycle=int(data.get("shift_per_cycle", cls.shift_per_cycle)),
            bitwise_per_cycle=int(data.get("bitwise_per_cycle", cls.bitwise_per_cycle)),
            const_per_cycle=int(data.get("const_per_cycle", cls.const_per_cycle)),
            load_per_cycle=int(data.get("load_per_cycle", cls.load_per_cycle)),
            store_per_cycle=int(data.get("store_per_cycle", cls.store_per_cycle)),
        )

    def validate(self) -> None:
        """Validate cycle rules are positive."""
        rules = [
            self.add_per_cycle,
            self.compare_per_cycle,
            self.logic_per_cycle,
            self.mul_per_cycle,
            self.copy_per_cycle,
            self.shift_per_cycle,
            self.bitwise_per_cycle,
            self.const_per_cycle,
            self.load_per_cycle,
            self.store_per_cycle,
        ]
        if any(r <= 0 for r in rules):
            raise ValueError("All cycle rules must be positive integers")


@dataclass
class OpSummary:
    """Summary of operations by type - Extended."""

    add: int = 0
    compare: int = 0
    logic: int = 0
    multiply: int = 0
    copy: int = 0  # Simple assignments
    shift: int = 0  # Bit shift operations
    bitwise: int = 0  # Bitwise AND/OR/XOR
    const: int = 0  # Literal assignments
    load: int = 0  # Memory reads
    store: int = 0  # Memory writes

    def total(self) -> int:
        """Return total operation count."""
        return (
            self.add
            + self.compare
            + self.logic
            + self.multiply
            + self.copy
            + self.shift
            + self.bitwise
            + self.const
            + self.load
            + self.store
        )


@dataclass
class Operation:
    """Single operation node within a block."""

    op_id: str
    op_type: str  # "add", "compare", "logic", "multiply", "copy", "shift", "bitwise", "const", "load", "store"
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
    successors: List[str]  # Next block IDs
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
    analysis_confidence: str
    analysis_coverage: float
    analysis_limitations: List[str]


# ============================================================================
# P2: Call Graph
# ============================================================================


@dataclass
class CallGraphNode:
    """Node in function call graph."""

    function_name: str
    block_id: str
    callers: List[str]  # Functions that call this
    callees: List[str]  # Functions this calls
    call_depth: int  # Depth from entry points
    is_recursive: bool
    self_cycles: int  # Estimated cycles for this function only
    total_cycles: int  # Including called functions


@dataclass
class CallGraph:
    """Complete function call graph."""

    nodes: Dict[str, CallGraphNode]
    entry_functions: List[str]  # Functions not called by others
    call_chains: List[List[str]]  # All possible execution paths
    critical_path: List[str]  # Longest execution path
    max_depth: int
    has_recursion: bool
    analysis_confidence: str
    analysis_coverage: float
    analysis_limitations: List[str]


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
    position: Dict[str, str] = field(
        default_factory=lambda: {"x": "TBD by drawing tool", "y": "TBD by drawing tool"}
    )
    cfg: Optional[ControlFlowGraph] = None  # NEW: Control flow graph


@dataclass
class Flow:
    """Execution flow metadata - Enhanced."""

    execution_order: List[str]
    parallelism: str = "unknown"
    call_graph: Optional[CallGraph] = None  # NEW: Function call graph
    call_sequence: Optional[List[Dict[str, object]]] = None  # NEW: Ordered call sequence


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
        r"//.*?$" r"|/\*.*?\*/" r"|\"(\\.|[^\\\"])*\"" r"|'(\\.|[^\\'])*'",
        re.DOTALL | re.MULTILINE,
    )
    return re.sub(pattern, replacer, source)


def expand_simple_function_macros(source: str) -> str:
    """Expand simple function-like macros.

    Only handles simple patterns like:
        #define FUNC(x) real_func((x))
        #define MAX(a,b) ((a) > (b) ? (a) : (b))

    Does NOT handle:
        - Multi-line macros
        - Macros with ## or # operators
        - Complex nested macros

    Returns source with simple macros expanded inline.
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
    """Normalize compound operators in a single statement to simple form.

    Examples:
        i++ → i = i + 1
        i += 2 → i = i + 2
        x *= y → x = x * y

    This simplification allows the main parser to handle all operations uniformly.
    """
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
                return text[open_index + 1 : index]
    return None


def extract_keyword_condition(statement: str, keyword: str) -> Optional[str]:
    """Extract condition expression from a keyword statement like if/while."""
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


# ============================================================================
# Function Parsing
# ============================================================================


def _compute_line_starts(source: str) -> List[int]:
    """Compute line start indices for a source string."""
    starts = [0]
    for idx, char in enumerate(source):
        if char == "\n":
            starts.append(idx + 1)
    return starts


def _find_function_body_from_coord(
    cleaned_source: str, coord_line: int, coord_column: int
) -> Optional[str]:
    """Locate and extract a function body using line/column coordinates."""
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
    """Find the matching opening parenthesis for a closing index."""
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
    """Return text between indices with attributes removed."""
    segment = source[start:end]
    segment = re.sub(
        r"__attribute__\s*\(\((?:.|\n)*?\)\)", " ", segment, flags=re.DOTALL
    )
    segment = re.sub(r"__declspec\s*\([^)]*\)", " ", segment, flags=re.DOTALL)
    return segment


def _extract_name_before_paren(
    source: str, open_paren: int
) -> Optional[Tuple[str, int]]:
    """Extract function name and its start index before the parameter list."""
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
    """Regex/scan-based fallback for finding function definitions."""
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


def find_function_definitions(source: str) -> List[Tuple[str, str, str, str]]:
    """Find all function definitions in source code."""
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
    for param in split_top_level_commas(params):
        param = param.strip()
        if not param:
            continue

        tokens = param.split()
        name = tokens[-1].replace("*", "").strip()
        result.append(name)

    return result


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


# ============================================================================
# Expression Parsing (Shunting-yard Algorithm) - Enhanced
# ============================================================================

OPERAND_PATTERN = (
    r"(?:"
    r"(?:\([A-Za-z_]\w*(?:\s*\*+)?\)\s*)*"  # Optional casts like (int) or (struct Foo*)
    r"(?:[+\-!~*&]+)?"  # Optional unary operators (no whitespace)
    r"(?:[A-Za-z_]\w*|0x[0-9A-Fa-f]+|\d+)"  # Base identifier or numeric literal
    r"(?:\s*(?:\[[^\]]+\]|\.\w+|->\w+))*"  # Indexing and member access
    r")"
)
OPERAND_REGEX = re.compile(rf"^{OPERAND_PATTERN}$")
CAST_PATTERN = re.compile(r"^\(\s*[A-Za-z_]\w*(?:\s*\*+)?\s*\)\s*")
NUMERIC_LITERAL_PATTERN = re.compile(r"^(?:0x[0-9A-Fa-f]+|\d+)$")


def tokenize_expression(expr: str) -> List[str]:
    """Tokenize expression including bitwise and shift operators."""
    pattern = re.compile(
        rf"{OPERAND_PATTERN}|"  # Operands with indexing/member access/casts/unary ops
        r"<<=|>>=|"  # Shift assignment (not used after normalization)
        r"<<|>>|"  # Shift operators
        r"<=|>=|==|!=|"  # Comparison operators
        r"&&|\|\||"  # Logical operators
        r"\?|:|"  # Ternary tokens
        r"[+\-*/%<>&|^]|"  # Arithmetic and bitwise
        r"\(|\)"  # Parentheses
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

    # Phase 2: Evaluate postfix and create operations
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

        # Track data flow
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


# ============================================================================
# P1: Simple Assignment Handling
# ============================================================================


def handle_simple_assignment(
    lhs: str,
    rhs: str,
    block_id: str,
    op_index: int,
    var_producers: Dict[str, Tuple[str, str]],
) -> Tuple[List[Operation], int, List[Signal]]:
    """Handle simple assignment: var = operand.

    Creates a "copy" operation to maintain complete data flow tracking.
    """
    operations = []
    edges = []

    # rhs must be a single operand (identifier, deref/member/index, literal)
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
                        comment=comment,
                    )
                )

            var_producers[lhs] = ("operation", op_id)

    return operations, op_index, edges


# ============================================================================
# Operation Extraction - Enhanced
# ============================================================================


def extract_operations(
    body: str, block_id: str, block_inputs: List[str], has_return: bool
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
            statement,
        )
        if declaration_match:
            lhs = re.sub(r"\s*\*\s*", "", declaration_match.group("lhs"))
            lhs = re.sub(r"\[.*?\]", "", lhs).strip()
            statement = f"{lhs} = " f"{declaration_match.group('rhs').strip()}"

        # Assignment: var = expr
        assignment_match = re.match(
            r"(?P<lhs>[^=]+?)\s*=(?!=)\s*(?P<rhs>.+)", statement
        )
        if assignment_match:
            lhs = assignment_match.group("lhs").strip()
            rhs = assignment_match.group("rhs").strip()

            # Check if simple assignment
            if is_operand(rhs):
                # Simple: a = b
                ops, op_index, new_edges = handle_simple_assignment(
                    lhs, rhs, block_id, op_index, var_producers
                )
                operations.extend(ops)
                edges.extend(new_edges)
            else:
                if is_store_target(lhs):
                    # Expression: *p = b + c
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
            condition_expr = extract_keyword_condition(statement, keyword)
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

    # Build summary
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


# ============================================================================
# Cycle Estimation - Enhanced
# ============================================================================


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


def analyze_control_flow(
    body: str, function_name: str, operations: List[Operation]
) -> ControlFlowGraph:
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

    analysis_limitations: List[str] = []

    def build_pairs(text: str, open_char: str, close_char: str) -> Dict[int, int]:
        stack: List[int] = []
        pairs: Dict[int, int] = {}
        for idx, ch in enumerate(text):
            if ch == open_char:
                stack.append(idx)
            elif ch == close_char:
                if stack:
                    start = stack.pop()
                    pairs[start] = idx
        if stack:
            analysis_limitations.append(
                f"unmatched '{open_char}' detected; control body ranges may be incomplete"
            )
        return pairs

    paren_pairs = build_pairs(cleaned, "(", ")")
    brace_pairs = build_pairs(cleaned, "{", "}")

    def next_nonspace(start: int) -> Optional[int]:
        idx = start
        while idx < len(cleaned) and cleaned[idx].isspace():
            idx += 1
        return idx if idx < len(cleaned) else None

    def is_keyword_at(start: int, keyword: str) -> bool:
        end = start + len(keyword)
        if not cleaned.startswith(keyword, start):
            return False
        before = cleaned[start - 1] if start > 0 else ""
        after = cleaned[end] if end < len(cleaned) else ""
        return (not before.isalnum() and before != "_") and (
            not after.isalnum() and after != "_"
        )

    def find_simple_statement_end(start: int) -> Optional[int]:
        stmt_end = start
        paren_depth = 0
        brace_depth = 0
        saw_brace = False
        while stmt_end < len(cleaned):
            ch = cleaned[stmt_end]
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
            elif ch == "{":
                brace_depth += 1
                saw_brace = True
            elif ch == "}":
                if brace_depth > 0:
                    brace_depth -= 1
                    if brace_depth == 0 and saw_brace and paren_depth == 0:
                        return stmt_end
            elif ch == ";" and paren_depth == 0 and brace_depth == 0:
                return stmt_end
            stmt_end += 1
        return None

    def find_statement_end(start: int) -> Optional[int]:
        stmt_start = next_nonspace(start)
        if stmt_start is None:
            return None
        for keyword in ("if", "for", "while", "do"):
            if is_keyword_at(stmt_start, keyword):
                paren_start = next_nonspace(stmt_start + len(keyword))
                if keyword == "do":
                    body_start = next_nonspace(stmt_start + len(keyword))
                else:
                    if paren_start is None or cleaned[paren_start] != "(":
                        return None
                    if paren_start not in paren_pairs:
                        return None
                    paren_end = paren_pairs[paren_start]
                    body_start = next_nonspace(paren_end + 1)
                if body_start is None:
                    return None
                if cleaned[body_start] == "{":
                    body_end = brace_pairs.get(body_start)
                else:
                    body_end = find_statement_end(body_start)
                if body_end is None:
                    return None
                if keyword == "do":
                    after_body = next_nonspace(body_end + 1)
                    if after_body is not None and is_keyword_at(after_body, "while"):
                        while_paren = next_nonspace(after_body + len("while"))
                        if while_paren is None or cleaned[while_paren] != "(":
                            return body_end
                        if while_paren not in paren_pairs:
                            return body_end
                        while_paren_end = paren_pairs[while_paren]
                        while_end = find_simple_statement_end(while_paren_end + 1)
                        return while_end if while_end is not None else body_end
                    return body_end
                if keyword == "if":
                    maybe_else = next_nonspace(body_end + 1)
                    if maybe_else is not None and cleaned.startswith(
                        "else", maybe_else
                    ):
                        after_else = next_nonspace(maybe_else + len("else"))
                        if after_else is None:
                            return body_end
                        if cleaned[after_else] == "{":
                            else_end = brace_pairs.get(after_else)
                        else:
                            else_end = find_statement_end(after_else)
                        if else_end is not None:
                            return else_end
                return body_end
        return find_simple_statement_end(stmt_start)

    control_pattern = re.compile(r"\b(if|for|while|do)\b")
    control_matches = list(control_pattern.finditer(cleaned))
    controls: List[Dict[str, object]] = []

    for match in control_matches:
        keyword = match.group(1)
        body_start: Optional[int] = None
        body_end: Optional[int] = None
        has_else = False
        else_body_start: Optional[int] = None
        else_body_end: Optional[int] = None

        if keyword in {"if", "for", "while"}:
            paren_start = next_nonspace(match.end())
            if paren_start is None or cleaned[paren_start] != "(":
                analysis_limitations.append(
                    f"missing '(' after {keyword}; control range not resolved"
                )
                continue
            if paren_start not in paren_pairs:
                analysis_limitations.append(
                    f"unmatched parentheses in {keyword} condition; control range not resolved"
                )
                continue
            paren_end = paren_pairs[paren_start]
            body_start = next_nonspace(paren_end + 1)
            if body_start is None:
                analysis_limitations.append(
                    f"missing {keyword} body; control range not resolved"
                )
                continue
            if cleaned[body_start] == "{":
                body_end = brace_pairs.get(body_start)
                if body_end is None:
                    analysis_limitations.append(
                        f"unmatched '{{' in {keyword} body; control range not resolved"
                    )
                    continue
            else:
                body_end = find_statement_end(body_start)
                if body_end is None:
                    analysis_limitations.append(
                        f"{keyword} single-statement body has no terminating ';' or matching '}}'"
                    )
                    continue

            if keyword == "if":
                maybe_else = next_nonspace(body_end + 1)
                if maybe_else is not None and cleaned.startswith("else", maybe_else):
                    has_else = True
                    after_else = next_nonspace(maybe_else + len("else"))
                    if after_else is not None and cleaned[after_else] == "{":
                        else_body_start = after_else
                        else_body_end = brace_pairs.get(after_else)
                        if else_body_end is None:
                            analysis_limitations.append(
                                "unmatched '{' in else body; else range not resolved"
                            )
                    elif after_else is not None:
                        else_body_start = after_else
                        else_body_end = find_statement_end(after_else)
                        if else_body_end is None:
                            analysis_limitations.append(
                                "else single-statement body has no terminating ';' or matching '}'"
                            )
                        elif is_keyword_at(after_else, "if"):
                            analysis_limitations.append(
                                "else-if chains are flattened in CFG"
                            )

        elif keyword == "do":
            body_start = next_nonspace(match.end())
            if body_start is None:
                analysis_limitations.append(
                    "missing do body; control range not resolved"
                )
                continue
            if cleaned[body_start] == "{":
                body_end = brace_pairs.get(body_start)
                if body_end is None:
                    analysis_limitations.append(
                        "unmatched '{' in do body; control range not resolved"
                    )
                    continue
            else:
                body_end = find_statement_end(body_start)
                if body_end is None:
                    analysis_limitations.append(
                        "do single-statement body has no terminating ';' or matching '}'"
                    )
                    continue

        if body_start is None or body_end is None:
            continue

        controls.append(
            {
                "keyword": keyword,
                "start": match.start(),
                "body_start": body_start,
                "body_end": body_end,
                "has_else": has_else,
                "else_body_start": else_body_start,
                "else_body_end": else_body_end,
            }
        )

    blocks: List[BasicBlock] = []
    loops: List[LoopInfo] = []

    block_index = 0
    loop_index = 0
    pending_ops = [op.op_id for op in operations]

    def make_block(
        block_type: str, operations_list: Optional[List[str]] = None
    ) -> BasicBlock:
        nonlocal block_index
        block_index += 1
        block = BasicBlock(
            block_id=f"{function_name}_b{block_index}",
            parent_function=function_name,
            operations=operations_list or [],
            predecessors=[],
            successors=[],
            block_type=block_type,
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

    for control in sorted(controls, key=lambda item: item["start"]):
        keyword = control["keyword"]
        if pending_ops and current == entry:
            seq_block = make_block("sequential")
            assign_pending_ops(seq_block)
            connect(current, seq_block)
            current = seq_block

        if keyword == "if":
            has_else_branch = bool(control["has_else"])

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

            loops.append(
                LoopInfo(
                    loop_id=f"{function_name}_loop_{loop_index}",
                    header_block=header_block.block_id,
                    body_blocks=[body_block.block_id],
                    exit_blocks=[exit_block.block_id],
                    nesting_level=1,
                    estimated_iterations="unknown",
                )
            )

            current = exit_block

    if pending_ops:
        tail_block = make_block("sequential")
        assign_pending_ops(tail_block)
        connect(current, tail_block)
        current = tail_block

    exit_block = make_block("exit")
    connect(current, exit_block)

    nesting_events: List[Tuple[int, int]] = []
    for control in controls:
        body_start = control["body_start"]
        body_end = control["body_end"]
        if isinstance(body_start, int) and isinstance(body_end, int):
            nesting_events.append((body_start, 1))
            nesting_events.append((body_end, -1))

    depth = 0
    max_depth = 0
    for _, delta in sorted(nesting_events, key=lambda item: (item[0], -item[1])):
        if delta == 1:
            depth += 1
            max_depth = max(max_depth, depth)
        else:
            depth = max(0, depth - 1)

    total_controls = len(control_matches)
    resolved_controls = len(controls)
    if total_controls == 0:
        analysis_coverage = 1.0
    else:
        analysis_coverage = resolved_controls / total_controls

    limitation_penalty = min(0.1 * len(analysis_limitations), 0.6)
    confidence_score = max(0.0, analysis_coverage * (1 - limitation_penalty))

    if confidence_score >= 0.85:
        analysis_confidence = "high"
    elif confidence_score >= 0.6:
        analysis_confidence = "medium"
    else:
        analysis_confidence = "low"

    return ControlFlowGraph(
        function_name=function_name,
        basic_blocks=blocks,
        entry_block=entry.block_id,
        exit_blocks=[exit_block.block_id],
        loops=loops,
        has_branches=has_if,
        max_nesting_depth=max_depth,
        analysis_confidence=analysis_confidence,
        analysis_coverage=round(analysis_coverage, 3),
        analysis_limitations=sorted(set(analysis_limitations)),
    )


# ============================================================================
# P2: Function Call Analysis
# ============================================================================


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
    functions: List[Tuple[str, str, str, str]],
    block_ids: Dict[str, str],
    blocks: List[Block],
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
            total_cycles=0,
        )

    total_analyses = len(functions)
    ast_analyses = 0
    analysis_limitations: List[str] = []

    # Build call relationships
    for _, caller_name, _, body in functions:
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
    functions: List[Tuple[str, str, str, str]],
    known_functions: Iterable[str],
) -> List[Dict[str, object]]:
    """Build ordered call sequences per function.

    Each call retains args/assigned info for dependency analysis.
    """
    known = set(known_functions)
    params_by_name = {name: parse_params(params) for _, name, params, _ in functions}
    sequences: List[Dict[str, object]] = []

    for _, caller_name, _, body in functions:
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

# ============================================================================
# Model Building - Enhanced
# ============================================================================


def build_model(source: str, rules: CycleRules) -> Dict[str, object]:
    """Build complete enhanced model with P1+P2 features."""

    source = expand_simple_function_macros(source)

    region, found = extract_cvas_region(source)
    if not found:
        print(
            f"WARNING: {MARKER_START} ~ {MARKER_END} region not found", file=sys.stderr
        )
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
                cfg=cfg,
            )
        )

    # Analyze function calls for inter-block signals
    for _, caller_name, _, body in functions:
        caller_id = block_ids[caller_name]
        calls, _ = find_function_calls(body, block_ids.keys())

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
    call_sequence = build_call_sequence(functions, block_ids.keys())

    # Enhanced flow with call graph
    flow = Flow(
        execution_order=[block.block_id for block in blocks],
        parallelism="sequential",  # Can be enhanced with dependency analysis
        call_graph=call_graph,
        call_sequence=call_sequence,
    )

    return {
        "blocks": [serialize_block(block) for block in blocks],
        "operations": [asdict(operation) for operation in operations],
        "signals": [asdict(signal) for signal in signals],
        "flow": serialize_flow(flow),
        "diagram_hint": {"layout": "TBD by drawing tool"},
        "note": "Enhanced with P1+P2: complete data flow, CFG, call graph",
        "analysis_version": "2.0",
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
            "max_nesting_depth": block.cfg.max_nesting_depth,
            "analysis_confidence": block.cfg.analysis_confidence,
            "analysis_coverage": block.cfg.analysis_coverage,
            "analysis_limitations": block.cfg.analysis_limitations,
        }

    return data


def serialize_flow(flow: Flow) -> Dict[str, object]:
    """Serialize flow with call graph."""
    data = {"execution_order": flow.execution_order, "parallelism": flow.parallelism}

    if flow.call_graph:
        data["call_graph"] = {
            "nodes": {
                name: asdict(node) for name, node in flow.call_graph.nodes.items()
            },
            "entry_functions": flow.call_graph.entry_functions,
            "call_chains": flow.call_graph.call_chains,
            "critical_path": flow.call_graph.critical_path,
            "max_depth": flow.call_graph.max_depth,
            "has_recursion": flow.call_graph.has_recursion,
            "analysis_confidence": flow.call_graph.analysis_confidence,
            "analysis_coverage": flow.call_graph.analysis_coverage,
            "analysis_limitations": flow.call_graph.analysis_limitations,
        }

    if flow.call_sequence is not None:
        data["call_sequence"] = flow.call_sequence

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
        """,
    )

    parser.add_argument("input", type=Path, help="Path to C source file")
    parser.add_argument(
        "-o", "--output", type=Path, help="Output JSON path (default: stdout)"
    )
    parser.add_argument("--cycle-config", type=Path, help="JSON file with cycle rules")
    parser.add_argument(
        "--add-per-cycle", type=int, help="Override add operations per cycle"
    )
    parser.add_argument(
        "--compare-per-cycle", type=int, help="Override compare operations per cycle"
    )
    parser.add_argument(
        "--logic-per-cycle", type=int, help="Override logic operations per cycle"
    )
    parser.add_argument(
        "--mul-per-cycle", type=int, help="Override multiply operations per cycle"
    )
    parser.add_argument(
        "--copy-per-cycle", type=int, help="Override copy operations per cycle"
    )
    parser.add_argument(
        "--shift-per-cycle", type=int, help="Override shift operations per cycle"
    )
    parser.add_argument(
        "--bitwise-per-cycle", type=int, help="Override bitwise operations per cycle"
    )
    parser.add_argument(
        "--const-per-cycle", type=int, help="Override const operations per cycle"
    )
    parser.add_argument(
        "--load-per-cycle", type=int, help="Override load operations per cycle"
    )
    parser.add_argument(
        "--store-per-cycle", type=int, help="Override store operations per cycle"
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Load cycle rules
    rules = CycleRules()

    if args.cycle_config:
        if not args.cycle_config.exists():
            print(
                f"ERROR: Cycle config file '{args.cycle_config}' not found",
                file=sys.stderr,
            )
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
    if args.logic_per_cycle is not None:
        rules.logic_per_cycle = args.logic_per_cycle
    if args.mul_per_cycle is not None:
        rules.mul_per_cycle = args.mul_per_cycle
    if args.copy_per_cycle is not None:
        rules.copy_per_cycle = args.copy_per_cycle
    if args.shift_per_cycle is not None:
        rules.shift_per_cycle = args.shift_per_cycle
    if args.bitwise_per_cycle is not None:
        rules.bitwise_per_cycle = args.bitwise_per_cycle
    if args.const_per_cycle is not None:
        rules.const_per_cycle = args.const_per_cycle
    if args.load_per_cycle is not None:
        rules.load_per_cycle = args.load_per_cycle
    if args.store_per_cycle is not None:
        rules.store_per_cycle = args.store_per_cycle

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
        source = args.input.read_text(encoding="utf-8")
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
            args.output.write_text(output, encoding="utf-8")
            print(
                f"✓ Analysis complete. Output written to {args.output}", file=sys.stderr
            )

            # Print summary
            num_blocks = len(model.get("blocks", []))
            num_ops = len(model.get("operations", []))
            num_signals = len(model.get("signals", []))

            print(f"✓ Analyzed {num_blocks} functions", file=sys.stderr)
            print(f"✓ Extracted {num_ops} operations", file=sys.stderr)
            print(f"✓ Tracked {num_signals} data flows", file=sys.stderr)

            flow = model.get("flow", {})
            if "call_graph" in flow:
                cg = flow["call_graph"]
                print(
                    f"✓ Call graph: {len(cg.get('nodes', {}))} nodes, "
                    f"depth {cg.get('max_depth', 0)}",
                    file=sys.stderr,
                )
                if cg.get("critical_path"):
                    print(
                        f"✓ Critical path: {' → '.join(cg['critical_path'])}",
                        file=sys.stderr,
                    )

        except IOError as e:
            print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(output)


if __name__ == "__main__":
    main()
