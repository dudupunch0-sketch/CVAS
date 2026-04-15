from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_CLANG_IMPORT_ERROR: Optional[Exception] = None
_LIBCLANG_CONFIGURED = False

try:
    from clang import cindex
except Exception as exc:  # pragma: no cover - exercised only when clang is missing
    cindex = None  # type: ignore[assignment]
    _CLANG_IMPORT_ERROR = exc


class ClangUnavailableError(RuntimeError):
    """Raised when libclang is unavailable for full analysis mode."""


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


def ensure_clang_available() -> None:
    """Raise a clear error when the clang Python bindings are unusable."""
    if cindex is None:
        raise ClangUnavailableError(
            "clang Python bindings are not available for full analysis mode"
        ) from _CLANG_IMPORT_ERROR

    _configure_libclang()

    try:
        cindex.Index.create()
    except Exception as exc:  # pragma: no cover - environment-specific path
        raise ClangUnavailableError(
            "libclang could not be initialized for full analysis mode"
        ) from exc


def _libclang_search_paths() -> List[Path]:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates: List[Path] = []

    env_path = os.environ.get("LIBCLANG_PATH")
    if env_path:
        candidates.append(Path(env_path))

    if cindex is not None:
        candidates.append(Path(cindex.__file__).resolve().parent / "native")

    for root in (
        Path("/usr/local"),
        Path("/usr"),
        Path(sys.prefix),
        Path(sys.base_prefix),
    ):
        candidates.append(root / "lib" / version / "dist-packages" / "clang" / "native")
        candidates.append(root / "lib" / version / "site-packages" / "clang" / "native")

    unique: List[Path] = []
    seen = set()
    for path in candidates:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _configure_libclang() -> None:
    global _LIBCLANG_CONFIGURED
    if _LIBCLANG_CONFIGURED or cindex is None:
        return
    if cindex.Config.library_file or cindex.Config.library_path:
        _LIBCLANG_CONFIGURED = True
        return

    for candidate in _libclang_search_paths():
        libclang = candidate / "libclang.so"
        if libclang.exists():
            cindex.Config.set_library_path(str(candidate))
            _LIBCLANG_CONFIGURED = True
            return


def _blank_preserving_newlines(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _strip_comments_and_strings(source: str) -> str:
    pattern = re.compile(
        r"//.*?$" r"|/\*.*?\*/" r"|\"(\\.|[^\\\"])*\"" r"|'(\\.|[^\\'])*'",
        re.DOTALL | re.MULTILINE,
    )
    return re.sub(pattern, lambda match: " " * len(match.group(0)), source)


def strip_cvas_markers(source: str) -> str:
    """Blank bare CVAS markers while preserving line/column offsets."""
    pattern = re.compile(r"(?m)^(?P<indent>\s*)(CVAS_START|CVAS_END)(?P<trail>\s*)$")
    return pattern.sub(lambda match: _blank_preserving_newlines(match.group(0)), source)


def _parse_translation_unit(
    source: str, *, filename: str, clang_args: Sequence[str]
):
    ensure_clang_available()
    normalized = strip_cvas_markers(source)
    index = cindex.Index.create()
    args = ["-xc", "-std=c11", *clang_args]
    tu = index.parse(
        filename,
        args=args,
        unsaved_files=[(filename, normalized)],
        options=0,
    )
    return tu, normalized


def _tu_has_errors(tu) -> bool:
    return any(diagnostic.severity >= 3 for diagnostic in tu.diagnostics)


def _diagnostic_limitations(tu) -> List[str]:
    limitations: List[str] = []
    for diagnostic in tu.diagnostics:
        if diagnostic.severity < 3:
            continue
        message = " ".join(diagnostic.spelling.split())
        limitation = f"clang parse error: {message}"
        if limitation not in limitations:
            limitations.append(limitation)
    return limitations


def _cursor_text(source: str, cursor) -> str:
    start = cursor.extent.start.offset
    end = cursor.extent.end.offset
    if start is None or end is None:
        return ""
    return source[start:end]


def _format_params(cursor, source: str) -> str:
    params = []
    for arg in cursor.get_arguments():
        text = _cursor_text(source, arg).strip()
        if text:
            params.append(" ".join(text.split()))
            continue
        label = arg.type.spelling
        if arg.spelling:
            label = f"{label} {arg.spelling}".strip()
        params.append(label)
    return ", ".join(params)


def _extract_body(compound_cursor, source: str) -> str:
    text = _cursor_text(source, compound_cursor)
    if text.startswith("{") and text.endswith("}"):
        return text[1:-1]
    return text


def find_function_definitions_with_clang(
    source: str,
    *,
    clang_args: Sequence[str] = (),
    filename: str = "__cvas_region__.c",
) -> List[Tuple[str, str, str, str]]:
    """Extract function definitions using clang's parser."""
    tu, normalized = _parse_translation_unit(
        source, filename=filename, clang_args=clang_args
    )
    functions: List[Tuple[str, str, str, str]] = []

    for cursor in tu.cursor.get_children():
        if cursor.kind != cindex.CursorKind.FUNCTION_DECL:
            continue
        if not cursor.is_definition():
            continue
        if cursor.location.file is None or cursor.location.file.name != filename:
            continue

        compound = next(
            (child for child in cursor.get_children() if child.kind == cindex.CursorKind.COMPOUND_STMT),
            None,
        )
        if compound is None:
            continue

        ret_type = cursor.result_type.spelling.strip()
        params = _format_params(cursor, normalized)
        body = _extract_body(compound, normalized)
        functions.append((ret_type, cursor.spelling, params, body))

    return functions


def _wrap_for_statement(statement: str) -> str:
    stripped = statement.strip()
    if not stripped:
        return stripped
    if re.match(r"^(if|for|while)\b", stripped) and not stripped.endswith((";", "}")):
        return f"{stripped};"
    if re.match(r"^do\b", stripped) and not stripped.endswith(";"):
        return f"{stripped};"
    return statement


def _function_like_names(text: str) -> List[str]:
    names: List[str] = []
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", text):
        name = match.group(1)
        if name in KEYWORDS or name in TYPE_AND_C_KEYWORDS:
            continue
        if name not in names:
            names.append(name)
    return names


def _pointer_like_names(text: str) -> List[str]:
    names: List[str] = []
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\[", text):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def _identifier_names(text: str) -> List[str]:
    names: List[str] = []
    for name in re.findall(r"\b[A-Za-z_]\w*\b", _strip_comments_and_strings(text)):
        if name in KEYWORDS or name in TYPE_AND_C_KEYWORDS:
            continue
        if name not in names:
            names.append(name)
    return names


def _build_wrapper_source(
    snippet: str, *, wrapper_name: str, function_names: Iterable[str]
) -> Tuple[str, str]:
    function_names = list(dict.fromkeys([*function_names, *_function_like_names(snippet)]))
    identifiers = _identifier_names(snippet)
    variable_names = [name for name in identifiers if name not in function_names]
    pointer_names = set(_pointer_like_names(snippet))

    preamble_lines = [
        "typedef unsigned char uint8_t;",
        "typedef unsigned short uint16_t;",
        "typedef unsigned int uint32_t;",
        "typedef unsigned long long uint64_t;",
        "typedef signed char int8_t;",
        "typedef short int16_t;",
        "typedef int int32_t;",
        "typedef long long int64_t;",
        "typedef unsigned long size_t;",
        "typedef long ssize_t;",
    ]
    for name in variable_names:
        if name in pointer_names:
            preamble_lines.append(f"int *{name};")
        else:
            preamble_lines.append(f"int {name};")
    preamble_lines.extend(f"int {name}();" for name in function_names)
    preamble = "\n".join(preamble_lines)
    wrapped = _wrap_for_statement(snippet)
    source = f"{preamble}\nint {wrapper_name}(void) {{\n{wrapped}\n}}\n"
    return source, wrapped


def _find_wrapper_statement_cursor(source: str, *, clang_args: Sequence[str]):
    tu, normalized = _parse_translation_unit(
        source, filename="__cvas_stmt__.c", clang_args=clang_args
    )
    if _tu_has_errors(tu):
        return None, normalized
    for cursor in tu.cursor.get_children():
        if cursor.kind != cindex.CursorKind.FUNCTION_DECL or cursor.spelling != "__cvas_stmt":
            continue
        compound = next(
            (child for child in cursor.get_children() if child.kind == cindex.CursorKind.COMPOUND_STMT),
            None,
        )
        if compound is None:
            return None, normalized
        for child in compound.get_children():
            return child, normalized
        return None, normalized
    return None, normalized


def _condition_from_cursor(statement_cursor, normalized_source: str) -> Optional[str]:
    children = list(statement_cursor.get_children())
    if statement_cursor.kind in {
        cindex.CursorKind.IF_STMT,
        cindex.CursorKind.WHILE_STMT,
    }:
        if not children:
            return None
        return _cursor_text(normalized_source, children[0]).strip() or None
    if statement_cursor.kind == cindex.CursorKind.DO_STMT:
        if len(children) < 2:
            return None
        return _cursor_text(normalized_source, children[1]).strip() or None
    if statement_cursor.kind == cindex.CursorKind.FOR_STMT:
        if len(children) < 2:
            return None
        return _cursor_text(normalized_source, children[1]).strip() or None
    return None


def extract_condition_with_clang(
    statement: str, keyword: str, *, clang_args: Sequence[str] = ()
) -> Optional[str]:
    """Extract if/while/do-while conditions from a single statement."""
    function_names = _function_like_names(statement)
    wrapper_source, _ = _build_wrapper_source(
        statement, wrapper_name="__cvas_stmt", function_names=function_names
    )
    statement_cursor, normalized = _find_wrapper_statement_cursor(
        wrapper_source, clang_args=clang_args
    )
    if statement_cursor is None:
        return None

    if keyword == "if" and statement_cursor.kind != cindex.CursorKind.IF_STMT:
        return None
    if keyword == "while" and statement_cursor.kind not in {
        cindex.CursorKind.WHILE_STMT,
        cindex.CursorKind.DO_STMT,
    }:
        return None

    return _condition_from_cursor(statement_cursor, normalized)


def extract_for_condition_with_clang(
    statement: str, *, clang_args: Sequence[str] = ()
) -> Optional[str]:
    """Extract the condition expression from a single for statement."""
    function_names = _function_like_names(statement)
    wrapper_source, _ = _build_wrapper_source(
        statement, wrapper_name="__cvas_stmt", function_names=function_names
    )
    statement_cursor, normalized = _find_wrapper_statement_cursor(
        wrapper_source, clang_args=clang_args
    )
    if statement_cursor is None or statement_cursor.kind != cindex.CursorKind.FOR_STMT:
        return None
    return _condition_from_cursor(statement_cursor, normalized)


def _call_args(cursor, source: str) -> List[str]:
    children = list(cursor.get_children())
    if len(children) <= 1:
        return []
    return [
        _cursor_text(source, child).strip()
        for child in children[1:]
        if _cursor_text(source, child).strip()
    ]


def _is_assignment_like(cursor, source: str) -> bool:
    if cursor.kind == cindex.CursorKind.COMPOUND_ASSIGNMENT_OPERATOR:
        return True
    if cursor.kind not in {
        cindex.CursorKind.BINARY_OPERATOR,
        cindex.CursorKind.UNEXPOSED_EXPR,
    }:
        return False
    tokens = [token.spelling for token in cursor.get_tokens()]
    return "=" in tokens


def find_function_calls_with_clang(
    body: str,
    known_functions: Iterable[str],
    *,
    clang_args: Sequence[str] = (),
) -> Tuple[List[Tuple[str, List[str], Optional[str]]], Dict[str, object]]:
    """Find function calls using clang AST traversal."""
    known = list(dict.fromkeys(known_functions))
    wrapper_source, _ = _build_wrapper_source(
        body, wrapper_name="__cvas_wrapper", function_names=known
    )
    tu, normalized = _parse_translation_unit(
        wrapper_source, filename="__cvas_calls__.c", clang_args=clang_args
    )
    if _tu_has_errors(tu):
        return [], {
            "parser": "clang_error",
            "limitations": _diagnostic_limitations(tu),
        }
    wrapper = next(
        (
            cursor
            for cursor in tu.cursor.get_children()
            if cursor.kind == cindex.CursorKind.FUNCTION_DECL
            and cursor.spelling == "__cvas_wrapper"
        ),
        None,
    )
    if wrapper is None:
        return [], {
            "parser": "clang",
            "limitations": ["clang wrapper function not found"],
        }

    known_set = set(known)
    calls: List[Tuple[str, List[str], Optional[str]]] = []

    def record_call(cursor, assigned: Optional[str]) -> None:
        name = cursor.spelling or _cursor_text(normalized, cursor).split("(", 1)[0].strip()
        if name in KEYWORDS or name not in known_set:
            return
        calls.append((name, _call_args(cursor, normalized), assigned))

    def walk(cursor) -> None:
        children = list(cursor.get_children())
        if cursor.kind == cindex.CursorKind.VAR_DECL:
            init = next((child for child in children if child.kind == cindex.CursorKind.CALL_EXPR), None)
            if init is not None:
                record_call(init, cursor.spelling or None)
                for arg in init.get_children():
                    walk(arg)
                return
        if _is_assignment_like(cursor, normalized) and len(children) >= 2:
            lhs_text = _cursor_text(normalized, children[0]).strip() or None
            rhs = children[1]
            if rhs.kind == cindex.CursorKind.CALL_EXPR:
                record_call(rhs, lhs_text)
                for arg in rhs.get_children():
                    walk(arg)
                return
        if cursor.kind == cindex.CursorKind.CALL_EXPR:
            record_call(cursor, None)
            for child in children[1:]:
                walk(child)
            return
        for child in children:
            walk(child)

    for child in wrapper.get_children():
        walk(child)

    return calls, {
        "parser": "clang",
        "limitations": [],
    }
