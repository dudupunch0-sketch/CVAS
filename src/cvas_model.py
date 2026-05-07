from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CycleRules:
    """Hardware cycle estimation rules."""

    add_per_cycle: int = 4
    compare_per_cycle: int = 4
    logic_per_cycle: int = 4
    mul_per_cycle: int = 1
    copy_per_cycle: int = 8
    shift_per_cycle: int = 2
    bitwise_per_cycle: int = 4
    const_per_cycle: int = 8
    load_per_cycle: int = 4
    store_per_cycle: int = 4

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
    """Summary of operations by type."""

    add: int = 0
    compare: int = 0
    logic: int = 0
    multiply: int = 0
    copy: int = 0
    shift: int = 0
    bitwise: int = 0
    const: int = 0
    load: int = 0
    store: int = 0

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
    op_type: str
    inputs: List[str]
    outputs: List[str]
    parent_block_id: str
    source_line: Optional[int] = None


@dataclass
class Signal:
    """Connection between blocks or operations."""

    source_id: str
    source_type: str
    destination_id: str
    destination_type: str
    signal_name: str
    direction: str
    comment: Optional[str] = None
    signal_id: Optional[str] = None
    kind: Optional[str] = None
    role: Optional[str] = None
    call_id: Optional[str] = None
    arg_index: Optional[int] = None
    param: Optional[str] = None
    expr: Optional[str] = None
    target: Optional[str] = None
    source_function: Optional[str] = None
    destination_function: Optional[str] = None
    provenance: Optional[Dict[str, object]] = None


@dataclass
class BasicBlock:
    """Basic block in control flow graph."""

    block_id: str
    parent_function: str
    operations: List[str]
    predecessors: List[str]
    successors: List[str]
    block_type: str


@dataclass
class LoopInfo:
    """Loop structure information."""

    loop_id: str
    header_block: str
    body_blocks: List[str]
    exit_blocks: List[str]
    nesting_level: int
    estimated_iterations: str


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


@dataclass
class CallGraphNode:
    """Node in function call graph."""

    function_name: str
    block_id: str
    callers: List[str]
    callees: List[str]
    call_depth: int
    is_recursive: bool
    self_cycles: int
    total_cycles: int


@dataclass
class CallGraph:
    """Complete function call graph."""

    nodes: Dict[str, CallGraphNode]
    entry_functions: List[str]
    call_chains: List[List[str]]
    critical_path: List[str]
    max_depth: int
    has_recursion: bool
    analysis_confidence: str
    analysis_coverage: float
    analysis_limitations: List[str]


@dataclass
class CallArgument:
    """One argument bound to a call instance."""

    arg_index: int
    param: Optional[str]
    expr: str
    signal_id: Optional[str] = None


@dataclass
class CallAssignment:
    """Return assignment target for a call instance."""

    target: str
    signal_id: Optional[str] = None


@dataclass
class CallInstance:
    """Stable direct-call occurrence used by schema v3."""

    call_id: str
    caller_block_id: str
    caller_function: str
    callee_block_id: Optional[str]
    callee_function: str
    ordinal_in_caller: int
    args: List[CallArgument]
    assigned: Optional[CallAssignment]
    source: Dict[str, object]
    provenance: Dict[str, object]


@dataclass
class SequenceTimelineStep:
    """Viewer-ready sequence timeline step for one function block."""

    step_id: str
    order_index: int
    block_id: str
    function: str
    call_ids_as_caller: List[str]
    call_ids_as_callee: List[str]
    incoming_signal_ids: List[str]
    outgoing_signal_ids: List[str]
    read_write_summary: Dict[str, List[Dict[str, object]]]


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
    cfg: Optional[ControlFlowGraph] = None


@dataclass
class Flow:
    """Execution flow metadata."""

    execution_order: List[str]
    parallelism: str = "unknown"
    execution_order_meta: Optional[Dict[str, object]] = None
    call_graph: Optional[CallGraph] = None
    call_sequence: Optional[List[Dict[str, object]]] = None
    call_instances: Optional[List[CallInstance]] = None
    sequence_timeline: Optional[List[SequenceTimelineStep]] = None
    function_io: Optional[Dict[str, object]] = None
    dependencies: Optional[Dict[str, List[Dict[str, object]]]] = None
    function_defs: Optional[Dict[str, Dict[str, object]]] = None
    unresolved_calls: Optional[List[Dict[str, object]]] = None
    external_symbols: Optional[List[Dict[str, object]]] = None

