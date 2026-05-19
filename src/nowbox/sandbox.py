from __future__ import annotations

import os
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

Command = str | Sequence[str | os.PathLike[str]]


@dataclass(frozen=True)
class SandboxResult:
    command: list[str] | str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Sandbox(ABC):
    @abstractmethod
    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        """Run a command in the sandbox and return its captured result."""


class LocalSandbox(Sandbox):
    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        normalized = _normalize_command(command)
        started = time.monotonic()
        completed = subprocess.run(
            normalized,
            cwd=Path(cwd) if cwd is not None else None,
            env={**os.environ, **env} if env is not None else None,
            capture_output=True,
            text=True,
            shell=isinstance(normalized, str),
        )
        duration = time.monotonic() - started
        result = SandboxResult(
            command=normalized,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(
                result.exit_code,
                result.command,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result


def _normalize_command(command: Command) -> list[str] | str:
    if isinstance(command, str):
        return command
    return [str(part) for part in command]


def shell(command: str) -> list[str]:
    return shlex.split(command)
