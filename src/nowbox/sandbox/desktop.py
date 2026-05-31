from __future__ import annotations

import atexit
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from nowbox.sandbox.base import Sandbox
from nowbox.types import Command, SandboxResult, SandboxStatus
from nowbox.utils import normalize_command, strip_ansi

_NOWBOX_LABEL = "nowbox.managed=true"


class DesktopSandbox(Sandbox):
    """Sandbox backed by an Apple Container running a full X11 desktop (Xvfb + xterm + VNC).

    The container must be built from ``container/Dockerfile.desktop`` and tagged
    as ``nowbox-desktop:latest`` (or pass a custom ``image``).

    Lifecycle: ``start()`` creates a detached container and waits for the VNC
    server to accept connections; ``stop()`` tears it down.  Use as a context
    manager::

        with DesktopSandbox("nowbox-desktop:latest", name="demo") as sb:
            result = sb.run(["echo", "hello"])   # programmatic, no VNC
            t = sb.terminal
            t.start_recording("out.mp4")
            t.type("ls -la").key("enter")
            t.stop_recording()
    """

    def __init__(
        self,
        image: str = "nowbox-desktop:latest",
        *,
        name: str | None = None,
        root: str | os.PathLike[str] = "/",
        id: str | None = None,
        volumes: list[str] | None = None,
        vnc_port: int = 5900,
    ) -> None:
        self._image = image
        self._name = name or f"nowbox-desktop-{uuid.uuid4().hex[:12]}"
        self._root = Path(root)
        self._id = id or f"desktop-{uuid.uuid4().hex[:12]}"
        self._created_at = time.time()
        self._status: SandboxStatus = "created"
        self._volumes = volumes or []
        self._vnc_port = vnc_port
        self._terminal: object | None = None  # lazy — avoids circular import

    # --- lifecycle ---

    def start(self) -> None:
        """Pull the image, start a detached container, and wait for VNC."""
        subprocess.run(["container", "stop", "--time", "1", self._name], capture_output=True)
        subprocess.run(["container", "delete", self._name], capture_output=True)

        cmd = [
            "container", "run",
            "--name", self._name,
            "--label", _NOWBOX_LABEL,
            "--publish", f"{self._vnc_port}:5900",
            "--detach",
        ]
        for v in self._volumes:
            cmd += ["--volume", v]
        cmd += [self._image]

        subprocess.run(cmd, check=True, capture_output=True, text=True)
        self._status = "running"
        atexit.register(self._atexit_cleanup)
        self._wait_for_vnc(timeout=20)

    def _wait_for_vnc(self, *, timeout: float) -> None:
        """Block until the VNC server inside the container accepts connections."""
        import socket as _socket
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                s = _socket.create_connection(("127.0.0.1", self._vnc_port), timeout=1)
                s.close()
                return
            except OSError:
                time.sleep(0.3)
        raise TimeoutError(f"VNC server on port {self._vnc_port} did not start within {timeout}s")

    def _atexit_cleanup(self) -> None:
        if self._status == "running":
            subprocess.run(["container", "stop", "--time", "1", self._name], capture_output=True)
            subprocess.run(["container", "delete", self._name], capture_output=True)

    def stop(self) -> None:
        """Stop and delete the container."""
        if self._terminal is not None:
            try:
                self._terminal.close()  # type: ignore[union-attr]
            except Exception:
                pass
        subprocess.run(["container", "stop", "--time", "1", self._name], capture_output=True)
        subprocess.run(["container", "delete", self._name], capture_output=True)
        self._status = "stopped"

    def __enter__(self) -> DesktopSandbox:
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
        return "desktop"

    @property
    def image(self) -> str:
        return self._image

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
    def terminal(self):  # -> VNCTerminal (avoid circular import at class level)
        if self._terminal is None:
            from nowbox.terminal_vnc import VNCTerminal
            self._terminal = VNCTerminal(self, host="127.0.0.1", port=self._vnc_port)
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
            output=strip_ansi(completed.stdout).strip(),
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(result.exit_code, result.command, output=result.stdout, stderr=result.stderr)
        return result

    # --- helpers ---

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

    def _container_read_file(self, path: str) -> str:
        """Read a file inside the container and return its content as a string."""
        result = subprocess.run(
            ["container", "exec", self._name, "cat", path],
            capture_output=True, text=True,
        )
        return result.stdout
