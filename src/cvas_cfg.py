from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from cvas_model import BasicBlock, ControlFlowGraph, LoopInfo, Operation
from cvas_source import strip_comments_and_strings


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
    """Analyze control flow and build CFG."""
    cleaned = strip_comments_and_strings(body)

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
                    if maybe_else is not None and cleaned.startswith("else", maybe_else):
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
