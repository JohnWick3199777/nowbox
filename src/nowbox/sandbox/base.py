from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from nowbox.types import Command, SandboxResult, SandboxStatus

if TYPE_CHECKING:
    from nowbox.terminal import SandboxTerminal


class Sandbox(ABC):
    @property
    @abstractmethod
    def id(self) -> str:
        """Stable identifier for this sandbox instance."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable sandbox name."""

    @property
    @abstractmethod
    def backend(self) -> str:
        """Sandbox backend name, e.g. `local`, `apple-container`, or `docker`."""

    @property
    @abstractmethod
    def root(self) -> Path:
        """Default working directory for commands in this sandbox."""

    @property
    @abstractmethod
    def status(self) -> SandboxStatus:
        """Current lifecycle status for this sandbox."""

    @property
    @abstractmethod
    def created_at(self) -> float:
        """Unix timestamp for when this sandbox was created."""

    @property
    @abstractmethod
    def terminal(self) -> SandboxTerminal:
        """PTY-backed terminal emulator for interactive command execution."""

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

    def _build_exec(
        self,
        command: list[str] | str,
        cwd: Path,
    ) -> tuple[list[str] | str, Path]:
        """Return (exec_command, host_cwd) for PTY execution.

        Subclasses override this to wrap the command for their backend
        (e.g. prefixing with ``container exec``).
        """
        return command, cwd
