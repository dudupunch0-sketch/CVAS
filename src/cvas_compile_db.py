from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CompileCommandEntry:
    """A single compile_commands.json entry normalized for lookup."""

    compile_db_path: Path
    directory: Path
    source_file: Path
    raw_args: Tuple[str, ...]


def discover_compile_database(
    *,
    explicit_path: Optional[Path],
    project_root: Optional[Path],
    source_path: Optional[Path],
) -> Optional[Path]:
    """Find the compile_commands.json file for a source path."""
    if explicit_path is not None:
        candidate = explicit_path.resolve()
        return candidate if candidate.is_file() else None

    if project_root is not None:
        candidate = project_root.resolve() / "compile_commands.json"
        if candidate.is_file():
            return candidate

    if source_path is None:
        return None

    current = source_path.resolve().parent
    while True:
        candidate = current / "compile_commands.json"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


@lru_cache(maxsize=8)
def _load_compile_commands(path_str: str) -> Tuple[CompileCommandEntry, ...]:
    compile_db_path = Path(path_str).resolve()
    raw_entries = json.loads(compile_db_path.read_text(encoding="utf-8"))
    entries: List[CompileCommandEntry] = []

    for entry in raw_entries:
        directory = Path(entry["directory"]).resolve()
        source_file = _resolve_entry_path(directory, entry["file"])
        raw_args = _extract_raw_args(entry)
        entries.append(
            CompileCommandEntry(
                compile_db_path=compile_db_path,
                directory=directory,
                source_file=source_file,
                raw_args=raw_args,
            )
        )
    return tuple(entries)


def load_compile_commands(path: Path) -> Tuple[CompileCommandEntry, ...]:
    """Load and cache compile_commands.json entries."""
    return _load_compile_commands(str(path.resolve()))


def match_compile_command(
    source_path: Path,
    *,
    explicit_path: Optional[Path],
    project_root: Optional[Path],
) -> Optional[CompileCommandEntry]:
    """Find the best compile_commands entry for a source file."""
    compile_db_path = discover_compile_database(
        explicit_path=explicit_path,
        project_root=project_root,
        source_path=source_path,
    )
    if compile_db_path is None:
        return None

    target = source_path.resolve()
    entries = load_compile_commands(compile_db_path)

    for entry in entries:
        if entry.source_file == target:
            return entry

    target_norm = str(target)
    for entry in entries:
        if str(entry.source_file) == target_norm:
            return entry

    return None


def normalize_compile_command_args(
    raw_args: Sequence[str], source_path: Path, directory: Path
) -> Tuple[str, ...]:
    """Convert a compiler-driver command into libclang-friendly parse args."""
    if not raw_args:
        return ()

    source_resolved = source_path.resolve()
    normalized: List[str] = []
    idx = 1  # skip compiler executable

    while idx < len(raw_args):
        token = raw_args[idx]

        if _is_source_arg(token, source_resolved, directory):
            idx += 1
            continue

        if token in {"-c", "-S", "-E", "-M", "-MM", "-MD", "-MMD", "-MG", "-MP"}:
            idx += 1
            continue

        if token in {"-o", "-MF", "-MT", "-MQ", "-MJ"}:
            idx += 2
            continue

        if token.startswith(("-o", "-MF", "-MT", "-MQ", "-MJ")) and len(token) > 2:
            idx += 1
            continue

        if token == "--output":
            idx += 2
            continue

        if token in {"-I", "-isystem", "-iquote", "-include", "-imacros", "-idirafter"}:
            if idx + 1 < len(raw_args):
                normalized.extend((token, _resolve_flag_path(raw_args[idx + 1], directory)))
            idx += 2
            continue

        rewritten = _rewrite_inline_path_flag(token, directory)
        if rewritten is not None:
            normalized.append(rewritten)
            idx += 1
            continue

        normalized.append(token)
        idx += 1

    return tuple(normalized)


def _extract_raw_args(entry: dict) -> Tuple[str, ...]:
    arguments = entry.get("arguments")
    if isinstance(arguments, list):
        return tuple(str(arg) for arg in arguments)

    command = entry.get("command")
    if isinstance(command, str):
        return tuple(shlex.split(command))

    raise ValueError("compile_commands entry must contain 'arguments' or 'command'")


def _resolve_entry_path(directory: Path, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path.resolve()
    return (directory / path).resolve()


def _is_source_arg(token: str, source_path: Path, directory: Path) -> bool:
    if token.startswith("-"):
        return False

    token_path = Path(token)
    if token_path.is_absolute():
        return token_path.resolve() == source_path
    return (directory / token_path).resolve() == source_path


def _resolve_flag_path(path_value: str, directory: Path) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str((directory / path).resolve())


def _rewrite_inline_path_flag(token: str, directory: Path) -> Optional[str]:
    for prefix in ("-I", "-isystem", "-iquote", "-include", "-imacros", "-idirafter"):
        if token.startswith(prefix) and token != prefix:
            return f"{prefix}{_resolve_flag_path(token[len(prefix):], directory)}"
    if token.startswith("--sysroot="):
        return f"--sysroot={_resolve_flag_path(token.split('=', 1)[1], directory)}"
    return None
