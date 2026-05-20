from __future__ import annotations

import os
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

from nowbox.terminal import SandboxTerminal
from nowbox.types import Command, SandboxResult, SandboxStatus
from nowbox.utils import normalize_command


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


class LocalSandbox(Sandbox):
    def __init__(
        self,
        *,
        name: str = "local",
        root: str | os.PathLike[str] | None = None,
        id: str | None = None,
    ) -> None:
        self._name = name
        self._root = Path(root) if root is not None else Path.cwd()
        self._id = id or f"local-{uuid.uuid4().hex[:12]}"
        self._created_at = time.time()
        self._status: SandboxStatus = "running"
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
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
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
            raise subprocess.CalledProcessError(
                result.exit_code, result.command, output=result.stdout, stderr=result.stderr
            )
        return result


class AppleContainerSandbox(Sandbox):
    """Sandbox backed by the Apple Container runtime (macOS 26+).

    Lifecycle: ``start()`` pulls the image and creates a long-lived
    container; ``stop()`` stops and deletes it.  Use as a context manager
    to handle this automatically::

        with AppleContainerSandbox("python:3.13", name="my-box") as sb:
            result = sb.run(["python", "--version"])
    """

    def __init__(
        self,
        image: str,
        *,
        name: str | None = None,
        root: str | os.PathLike[str] = "/",
        id: str | None = None,
    ) -> None:
        self._image = image
        self._name = name or f"nowbox-{uuid.uuid4().hex[:12]}"
        self._root = Path(root)
        self._id = id or f"container-{uuid.uuid4().hex[:12]}"
        self._created_at = time.time()
        self._status: SandboxStatus = "created"
        self._terminal = SandboxTerminal(self)

    # --- lifecycle ---

    def start(self) -> None:
        """Pull the image and start a detached container."""
        subprocess.run(
            [
                "container", "run",
                "--name", self._name,
                "--detach",
                self._image,
                "--", "sleep", "infinity",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._status = "running"

    def stop(self) -> None:
        """Stop and delete the container."""
        subprocess.run(["container", "stop", self._name], capture_output=True)
        subprocess.run(["container", "delete", self._name], capture_output=True)
        self._status = "stopped"

    def __enter__(self) -> AppleContainerSandbox:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # --- Sandbox ABC ---

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def backend(self) -> str:
        return "apple-container"

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
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        normalized = normalize_command(command)
        working_dir = Path(cwd) if cwd is not None else self.root
        exec_cmd = self._container_exec_cmd(normalized, working_dir, env)
        started = time.monotonic()
        completed = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
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
            raise subprocess.CalledProcessError(
                result.exit_code, result.command, output=result.stdout, stderr=result.stderr
            )
        return result

    # --- internal helpers ---

    def _container_exec_cmd(
        self,
        command: list[str] | str,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> list[str]:
        args: list[str] = ["container", "exec", "--workdir", str(cwd)]
        if env:
            for k, v in env.items():
                args += ["--env", f"{k}={v}"]
        args += [self._name, "--"]
        if isinstance(command, list):
            args += command
        else:
            args += ["sh", "-c", command]
        return args

    def _build_exec(
        self,
        command: list[str] | str,
        cwd: Path,
    ) -> tuple[list[str] | str, Path]:
        return self._container_exec_cmd(command, cwd), Path.home()
