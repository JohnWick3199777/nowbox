from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

Command = str | Sequence[str | os.PathLike[str]]
SandboxStatus = str


@dataclass(frozen=True)
class RecordingOptions:
    path: Path
    duration_seconds: float = 3.0
    width: int = 1280
    height: int = 720
    font_size: int = 24


@dataclass(frozen=True)
class SandboxResult:
    sandbox_id: str
    command: list[str] | str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    cwd: Path | None = None
    recording_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0
