from __future__ import annotations

import atexit
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from nowbox.sandbox.base import Sandbox
from nowbox.terminal import SandboxTerminal
from nowbox.types import Command, SandboxResult, SandboxStatus
from nowbox.utils import normalize_command

_NOWBOX_LABEL = "nowbox.managed=true"


class AppleContainerSandbox(Sandbox):
    """Sandbox backed by the Apple Container runtime (macOS 26+).

    Lifecycle: ``start()`` pulls the image and creates a long-lived
    container; ``stop()`` stops and deletes it.  Use as a context manager
    to handle this automatically::

        with AppleContainerSandbox("python:3.13", name="my-box") as sb:
            result = sb.run(["python", "--version"])
    """

    def __init__(self, image: str, *, name: str | None = None, root: str | os.PathLike[str] = "/", id: str | None = None, volumes: list[str] | None = None) -> None:
        self._image = image
        self._name = name or f"nowbox-{uuid.uuid4().hex[:12]}"
        self._root = Path(root)
        self._id = id or f"container-{uuid.uuid4().hex[:12]}"
        self._created_at = time.time()
        self._status: SandboxStatus = "created"
        self._volumes = volumes or []
        self._terminal = SandboxTerminal(self)

    # --- lifecycle ---

    def start(self) -> None:
        """Pull the image and start a detached container."""
        # Clean up any leftover container with the same name before starting.
        subprocess.run(["container", "stop", self._name], capture_output=True)
        subprocess.run(["container", "delete", self._name], capture_output=True)
        cmd = ["container", "run", "--name", self._name, "--label", _NOWBOX_LABEL, "--detach"]
        for v in self._volumes:
            cmd += ["--volume", v]
        cmd += [self._image, "sleep", "infinity"]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        self._status = "running"
        atexit.register(self._atexit_cleanup)

    def _atexit_cleanup(self) -> None:
        if self._status == "running":
            subprocess.run(["container", "stop", self._name], capture_output=True)
            subprocess.run(["container", "delete", self._name], capture_output=True)

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
    def image(self) -> str | None:
        return self._image

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
        exec_cmd = self._container_exec_cmd(normalized, working_dir, env)
        started = time.monotonic()
        completed = subprocess.run(exec_cmd, capture_output=True, text=True)
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

    # --- internal helpers ---

    def _container_exec_cmd(self, command: list[str] | str, cwd: Path, env: Mapping[str, str] | None = None) -> list[str]:
        args: list[str] = ["container", "exec", "--workdir", str(cwd)]
        if env:
            for k, v in env.items():
                args += ["--env", f"{k}={v}"]
        args += [self._name]
        if isinstance(command, list):
            args += command
        else:
            args += ["sh", "-c", command]
        return args

    def _build_exec(self, command: list[str] | str, cwd: Path) -> tuple[list[str] | str, Path]:
        return self._container_exec_cmd(command, cwd), Path.home()
