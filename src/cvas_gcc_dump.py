from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from cvas_analysis import AnalysisOptions, infer_language_from_path, resolve_clang_config


def _compiler_for_language(language: str) -> str:
    return "g++" if language == "c++" else "gcc"


def _gcc_language_flag(language: str) -> str:
    return "c++" if language == "c++" else "c"


def _dump_files(directory: Path) -> List[str]:
    return sorted(path.name for path in directory.glob("*.cfg"))


def run_gcc_dump(
    source: str,
    *,
    source_path: Optional[Path] = None,
    analysis_options: AnalysisOptions = AnalysisOptions(mode="full"),
) -> Dict[str, object]:
    """Run a lightweight GCC syntax/CFG dump pass and return JSON-safe metadata.

    CVAS full mode is intentionally non-fatal: GCC dump enriches diagnostics when
    available, while the main model still comes from the robust fast pipeline.
    """
    config = resolve_clang_config(analysis_options, source_path=source_path)
    language = config.language or infer_language_from_path(source_path) or "c"
    compiler = _compiler_for_language(language)
    compiler_path = shutil.which(compiler)
    metadata: Dict[str, object] = {
        "backend": compiler,
        "status": "unavailable",
        "language": language,
        "standard": config.standard,
        "dump_files": [],
        "diagnostics": [],
    }
    if compiler_path is None:
        metadata["diagnostics"] = [f"{compiler} not found on PATH"]
        return metadata

    suffix = ".cpp" if language == "c++" else ".c"
    with tempfile.TemporaryDirectory(prefix="cvas-gcc-dump-") as tmpdir:
        tmp_path = Path(tmpdir)
        if source_path is not None and source_path.exists():
            parse_target = source_path.resolve()
        else:
            parse_target = tmp_path / f"input{suffix}"
            parse_target.write_text(source, encoding="utf-8")
        cwd = tmp_path

        cmd = [
            compiler_path,
            "-c",
            "-o",
            str(tmp_path / "cvas-gcc-dump.o"),
            "-fdump-tree-cfg",
            "-x",
            _gcc_language_flag(language),
            f"-std={config.standard}",
            "-DCVAS_START=",
            "-DCVAS_END=",
        ]
        idx = 0
        while idx < len(config.final_clang_args):
            arg = config.final_clang_args[idx]
            if arg in {"-I", "-D", "-U", "-isystem"} and idx + 1 < len(config.final_clang_args):
                cmd.extend([arg, config.final_clang_args[idx + 1]])
                idx += 2
                continue
            if arg.startswith(("-I", "-D", "-U", "-isystem")):
                cmd.append(arg)
            idx += 1
        cmd.append(str(parse_target))

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            metadata["status"] = "failed"
            metadata["diagnostics"] = ["gcc dump timed out after 15 seconds"]
            return metadata
        except OSError as exc:
            metadata["status"] = "failed"
            metadata["diagnostics"] = [str(exc)]
            return metadata

        diagnostics = "\n".join(part for part in [result.stderr, result.stdout] if part)
        metadata.update(
            {
                "status": "ok" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
                "command": " ".join(cmd),
                "dump_files": _dump_files(cwd),
                "diagnostics": diagnostics.splitlines()[:40],
            }
        )
        return metadata
