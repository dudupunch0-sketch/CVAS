from __future__ import annotations

import re
from typing import List, Optional

from cvas_source import split_top_level_commas, strip_comments_and_strings

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

TYPE_AND_C_KEYWORDS = {
    "auto",
    "bool",
    "break",
    "case",
    "char",
    "const",
    "continue",
    "default",
    "do",
    "double",
    "else",
    "enum",
    "extern",
    "float",
    "for",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "register",
    "restrict",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "struct",
    "switch",
    "typedef",
    "union",
    "unsigned",
    "void",
    "volatile",
    "while",
}


def extract_identifier_tokens(body: str) -> List[str]:
    """Extract identifier-like tokens from function body."""
    cleaned = strip_comments_and_strings(body)
    return re.findall(r"\b[A-Za-z_]\w*\b", cleaned)


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


def split_statements(body: str) -> List[str]:
    """Split a function body into rough statements."""
    statements: List[str] = []
    current: List[str] = []
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
    """Extract content inside parentheses starting at open_index."""
    if open_index >= len(text) or text[open_index] != "(":
        return None

    depth = 0
    for idx in range(open_index, len(text)):
        if text[idx] == "(":
            depth += 1
        elif text[idx] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : idx]

    return None


def split_top_level_semicolons(text: str) -> List[str]:
    """Split a string by semicolons at the top level."""
    parts = []
    depth = 0
    start = 0
    for idx, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            parts.append(text[start:idx])
            start = idx + 1
    parts.append(text[start:])
    return parts
