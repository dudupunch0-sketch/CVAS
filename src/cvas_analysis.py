from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from cvas_compile_db import match_compile_command, normalize_compile_command_args


@dataclass(frozen=True)
class AnalysisOptions:
    """Configuration that selects the parser/analyzer backend."""

    mode: str = "fast"
    clang_args: Tuple[str, ...] = field(default_factory=tuple)
    language_override: Optional[str] = None
    compile_db_path: Optional[str] = None
    project_root: Optional[str] = None

    def __post_init__(self) -> None:
        if self.mode not in {"fast", "full"}:
            raise ValueError(f"Unsupported analysis mode: {self.mode}")
        object.__setattr__(self, "clang_args", tuple(self.clang_args))
        if self.language_override is not None:
            normalized = normalize_language(self.language_override)
            if normalized is None:
                raise ValueError(f"Unsupported language override: {self.language_override}")
            object.__setattr__(self, "language_override", normalized)

    @property
    def backend(self) -> str:
        if self.mode == "full":
            return "tree-sitter+pycparser+gcc-dump"
        return "pycparser"

    @property
    def compile_db_path_obj(self) -> Optional[Path]:
        if self.compile_db_path is None:
            return None
        return Path(self.compile_db_path)

    @property
    def project_root_obj(self) -> Optional[Path]:
        if self.project_root is None:
            return None
        return Path(self.project_root)

    @classmethod
    def from_values(
        cls,
        *,
        mode: str = "fast",
        clang_args: Sequence[str] | None = None,
        language_override: Optional[str] = None,
        compile_db_path: Optional[str] = None,
        project_root: Optional[str] = None,
    ) -> "AnalysisOptions":
        return cls(
            mode=mode,
            clang_args=tuple(clang_args or ()),
            language_override=language_override,
            compile_db_path=compile_db_path,
            project_root=project_root,
        )


@dataclass(frozen=True)
class ResolvedClangConfig:
    """Concrete clang configuration for one source file or helper wrapper."""

    source_path: Optional[Path]
    language: str
    standard: str
    wrapper_suffix: str
    compile_db_path: Optional[Path]
    compile_db_command_file: Optional[Path]
    compile_db_raw_args: Tuple[str, ...]
    user_clang_args: Tuple[str, ...]
    final_clang_args: Tuple[str, ...]
    flag_sources: Dict[str, str]


def resolve_clang_config(
    analysis_options: AnalysisOptions, *, source_path: Optional[Path] = None
) -> ResolvedClangConfig:
    """Resolve the final clang configuration for a source file."""
    user_clang_args = tuple(analysis_options.clang_args)
    compile_command = None
    compile_db_args: Tuple[str, ...] = ()
    compile_db_path = None
    compile_db_command_file = None

    if source_path is not None:
        compile_command = match_compile_command(
            source_path,
            explicit_path=analysis_options.compile_db_path_obj,
            project_root=analysis_options.project_root_obj,
        )
        if compile_command is not None:
            compile_db_path = compile_command.compile_db_path
            compile_db_command_file = compile_command.source_file
            compile_db_args = normalize_compile_command_args(
                compile_command.raw_args,
                compile_command.source_file,
                compile_command.directory,
            )

    user_language = _extract_language_arg(user_clang_args)
    compile_language = _extract_language_arg(compile_db_args)
    inferred_language = infer_language_from_path(source_path)
    language, language_source = _resolve_language(
        analysis_options.language_override,
        user_language,
        inferred_language,
        compile_language,
    )

    user_standard = _extract_standard_arg(user_clang_args)
    compile_standard = _extract_standard_arg(compile_db_args)
    standard, standard_source = _resolve_standard(
        language,
        user_standard=user_standard,
        compile_standard=compile_standard,
    )

    stripped_compile_args = _strip_language_and_standard_args(compile_db_args)
    stripped_user_args = _strip_language_and_standard_args(user_clang_args)
    final_clang_args = (
        f"-x{language_flag(language)}",
        f"-std={standard}",
        *stripped_compile_args,
        *stripped_user_args,
    )

    flag_sources = {
        "language": language_source,
        "standard": standard_source,
    }
    if compile_db_path is not None:
        flag_sources["compile_db"] = str(compile_db_path)
    if source_path is not None:
        flag_sources["source_path"] = str(source_path)

    return ResolvedClangConfig(
        source_path=source_path,
        language=language,
        standard=standard,
        wrapper_suffix=".cpp" if language == "c++" else ".c",
        compile_db_path=compile_db_path,
        compile_db_command_file=compile_db_command_file,
        compile_db_raw_args=compile_db_args,
        user_clang_args=user_clang_args,
        final_clang_args=tuple(final_clang_args),
        flag_sources=flag_sources,
    )


def infer_language_from_path(source_path: Optional[Path]) -> Optional[str]:
    """Infer source language from a file extension."""
    if source_path is None:
        return None
    suffix = source_path.suffix.lower()
    if suffix == ".c":
        return "c"
    if suffix in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}:
        return "c++"
    if suffix == ".h":
        return None
    return None


def normalize_language(value: str) -> Optional[str]:
    """Normalize language names to 'c' or 'c++'."""
    normalized = value.strip().lower()
    if normalized in {"c", "c-header"}:
        return "c"
    if normalized in {
        "c++",
        "cpp",
        "cxx",
        "c++-header",
        "c++-cpp-output",
        "c++-header-unit",
    }:
        return "c++"
    return None


def language_flag(language: str) -> str:
    return "c++" if language == "c++" else "c"


def _resolve_language(
    explicit_override: Optional[str],
    user_language: Optional[str],
    inferred_language: Optional[str],
    compile_language: Optional[str],
) -> Tuple[str, str]:
    if explicit_override is not None:
        return explicit_override, "analysis option --language"
    if user_language is not None:
        return user_language, "user --clang-arg"
    if inferred_language is not None:
        return inferred_language, "source file extension"
    if compile_language is not None:
        return compile_language, "compile_commands.json"
    return "c", "default"


def _resolve_standard(
    language: str,
    *,
    user_standard: Optional[str],
    compile_standard: Optional[str],
) -> Tuple[str, str]:
    if user_standard is not None:
        return user_standard, "user --clang-arg"
    if compile_standard is not None:
        return compile_standard, "compile_commands.json"
    if language == "c++":
        return "c++11", "default"
    return "c11", "default"


def _extract_language_arg(args: Sequence[str]) -> Optional[str]:
    for idx, token in enumerate(args):
        if token == "-x" and idx + 1 < len(args):
            normalized = normalize_language(args[idx + 1])
            if normalized is not None:
                return normalized
            continue
        if token.startswith("-x") and token != "-x":
            normalized = normalize_language(token[2:])
            if normalized is not None:
                return normalized
    return None


def _extract_standard_arg(args: Sequence[str]) -> Optional[str]:
    for idx, token in enumerate(args):
        if token == "-std" and idx + 1 < len(args):
            return args[idx + 1].strip()
        if token.startswith("-std="):
            return token.split("=", 1)[1].strip()
    return None


def _strip_language_and_standard_args(args: Sequence[str]) -> Tuple[str, ...]:
    stripped = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "-x":
            idx += 2
            continue
        if token.startswith("-x") and token != "-x":
            idx += 1
            continue
        if token == "-std":
            idx += 2
            continue
        if token.startswith("-std="):
            idx += 1
            continue
        stripped.append(token)
        idx += 1
    return tuple(stripped)
