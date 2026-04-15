from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence, Tuple


@dataclass(frozen=True)
class AnalysisOptions:
    """Configuration that selects the parser/analyzer backend."""

    mode: str = "fast"
    clang_args: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.mode not in {"fast", "full"}:
            raise ValueError(f"Unsupported analysis mode: {self.mode}")
        object.__setattr__(self, "clang_args", tuple(self.clang_args))

    @property
    def backend(self) -> str:
        return "clang" if self.mode == "full" else "pycparser"

    @classmethod
    def from_values(
        cls, *, mode: str = "fast", clang_args: Sequence[str] | None = None
    ) -> "AnalysisOptions":
        return cls(mode=mode, clang_args=tuple(clang_args or ()))
