from __future__ import annotations

import importlib
import importlib.util
import re


def load_pycparser():
    """Return pycparser module if available, else None."""
    if importlib.util.find_spec("pycparser") is None:
        return None
    return importlib.import_module("pycparser")


def _blank_preserving_newlines(text: str) -> str:
    """Replace non-newline chars with spaces to keep line/column indices."""
    return re.sub(r"[^\n]", " ", text)


def normalize_c_source(source: str) -> str:
    """Normalize C source for parsing while preserving line numbers."""
    lines = []
    for line in source.splitlines(keepends=True):
        if line.lstrip().startswith("#"):
            lines.append("\n")
        else:
            lines.append(line)
    normalized = "".join(lines)

    normalized = re.sub(
        r"__attribute__\s*\(\((?:.|\n)*?\)\)",
        lambda match: _blank_preserving_newlines(match.group(0)),
        normalized,
        flags=re.DOTALL,
    )
    normalized = re.sub(
        r"__declspec\s*\([^)]*\)",
        lambda match: _blank_preserving_newlines(match.group(0)),
        normalized,
        flags=re.DOTALL,
    )
    return normalized


def parse_translation_unit(source: str):
    """Parse a translation unit and return (pycparser, ast, generator, normalized_source)."""
    pycparser_module = load_pycparser()
    if pycparser_module is None:
        return None
    try:
        from pycparser import c_generator
    except Exception as exc:
        raise ImportError(
            "Failed to import pycparser.c_generator. "
            "This can happen if a local file/folder shadows the 'pycparser' package."
        ) from exc
    normalized = normalize_c_source(source)
    parser = pycparser_module.CParser()
    try:
        ast = parser.parse(normalized)
    except Exception:
        return None
    generator = c_generator.CGenerator()
    return pycparser_module, ast, generator, normalized


def _wrap_statement_for_parse(statement: str) -> str:
    stripped = statement.strip()
    if not stripped:
        return stripped
    if re.match(r"^(if|for|while)\b", stripped) and not stripped.endswith((";", "}")):
        return f"{stripped};"
    if re.match(r"^do\b", stripped) and not stripped.endswith(";"):
        return f"{stripped};"
    return statement


def parse_statement(statement: str):
    """Parse a single statement and return (pycparser, node, generator) if possible."""
    pycparser_module = load_pycparser()
    if pycparser_module is None:
        return None
    try:
        from pycparser import c_generator
    except Exception as exc:
        raise ImportError(
            "Failed to import pycparser.c_generator. "
            "This can happen if a local file/folder shadows the 'pycparser' package."
        ) from exc
    wrapped = _wrap_statement_for_parse(statement)
    source = f"void __cvas_stmt(void) {{\n{wrapped}\n}}"
    normalized = normalize_c_source(source)
    parser = pycparser_module.CParser()
    try:
        ast = parser.parse(normalized)
    except Exception:
        return None
    if not ast.ext:
        return None
    func = ast.ext[0]
    body_items = func.body.block_items or []
    if not body_items:
        return None
    generator = c_generator.CGenerator()
    return pycparser_module, body_items[0], generator
