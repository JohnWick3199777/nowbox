from __future__ import annotations

import os
import platform as _platform
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from nowbox.sandbox.base import Sandbox
from nowbox.terminal import SandboxTerminal
from nowbox.types import Command, SandboxResult, SandboxStatus
from nowbox.utils import normalize_command


class LocalSandbox(Sandbox):
    def __init__(
        self, *, name: str = "local", root: str | os.PathLike[str] | None = None, id: str | None = None, volumes: list[str] | None = None
    ) -> None:
        self._name = name
        self._root = Path(root) if root is not None else Path.cwd()
        self._id = id or f"local-{uuid.uuid4().hex[:12]}"
        self._created_at = time.time()
        self._status: SandboxStatus = "running"
        self._volumes = volumes or []
        self._terminal = SandboxTerminal(self)

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def backend(self) -> str:
        return "local"

    @property
    def platform(self) -> str | None:
        return _platform.platform()

    @property
    def volumes(self) -> list[str]:
        return list(self._volumes)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def status(self) -> SandboxStatus:
        return self._status

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def terminal(self) -> SandboxTerminal:
        return self._terminal

    def run(
        self, command: Command, *, cwd: str | os.PathLike[str] | None = None, env: Mapping[str, str] | None = None, check: bool = False
    ) -> SandboxResult:
        normalized = normalize_command(command)
        working_dir = Path(cwd) if cwd is not None else self.root
        started = time.monotonic()
        completed = subprocess.run(
            normalized,
            cwd=working_dir,
            env={**os.environ, **env} if env is not None else None,
            capture_output=True,
            text=True,
            shell=isinstance(normalized, str),
        )
        duration = time.monotonic() - started
        result = SandboxResult(
            sandbox_id=self.id,
            command=normalized,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
            cwd=working_dir,
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(result.exit_code, result.command, output=result.stdout, stderr=result.stderr)
        return result
