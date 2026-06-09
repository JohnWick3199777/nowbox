from __future__ import annotations

import atexit
import json
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from nowbox.sandbox.base import Sandbox
from nowbox.types import Command, SandboxResult, SandboxStatus
from nowbox.utils import normalize_command, strip_ansi

if TYPE_CHECKING:
    from nowbox.terminal_vnc import VNCTerminal


class DesktopMachineSandbox(Sandbox):
    """Desktop sandbox backed by Apple Container's ``container machine`` runtime."""

    def __init__(
        self,
        image: str = "nowbox-desktop-machine:latest",
        *,
        name: str | None = None,
        root: str | os.PathLike[str] = "/",
        id: str | None = None,
        cpus: int = 2,
        memory: str = "2G",
        home_mount: str = "none",
        vnc_port: int = 5900,
    ) -> None:
        self._image = image
        self._name = name or f"nowbox-desktop-machine-{uuid.uuid4().hex[:12]}"
        self._root = Path(root)
        self._id = id or f"desktop-machine-{uuid.uuid4().hex[:12]}"
        self._created_at = time.time()
        self._status: SandboxStatus = "created"
        self._cpus = cpus
        self._memory = memory
        self._home_mount = home_mount
        self._vnc_port = vnc_port
        self._ip_address: str | None = None
        self._terminal: VNCTerminal | None = None

    # --- lifecycle ---

    def start(self) -> None:
        """Create and boot the machine, then wait for its VNC server."""
        self._delete_existing_machine()

        cmd = [
            "container",
            "machine",
            "create",
            self._image,
            "--name",
            self._name,
            "--cpus",
            str(self._cpus),
            "--memory",
            self._memory,
            "--home-mount",
            self._home_mount,
        ]
        try:
            self._run_checked(cmd, f"Failed to create desktop machine {self._name!r}")
        except Exception:
            self._delete_existing_machine()
            raise
        self._status = "running"
        atexit.register(self._atexit_cleanup)
        self._ip_address = self._wait_for_ip(timeout=30)
        self._start_desktop()
        self._wait_for_vnc(timeout=30)

    def _delete_existing_machine(self) -> None:
        subprocess.run(["container", "machine", "stop", self._name], capture_output=True)
        subprocess.run(["container", "machine", "delete", self._name], capture_output=True)

    def _atexit_cleanup(self) -> None:
        if self._status == "running":
            subprocess.run(["container", "machine", "stop", self._name], capture_output=True)
            subprocess.run(["container", "machine", "delete", self._name], capture_output=True)

    def stop(self) -> None:
        if self._terminal is not None:
            try:
                self._terminal.close()
            except Exception:
                pass
        subprocess.run(["container", "machine", "stop", self._name], capture_output=True)
        subprocess.run(["container", "machine", "delete", self._name], capture_output=True)
        self._status = "stopped"

    def __enter__(self) -> DesktopMachineSandbox:
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
        return "desktop-machine"

    @property
    def image(self) -> str:
        return self._image

    @property
    def platform(self) -> str | None:
        return "linux/arm64"

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
    def terminal(self):
        if self._terminal is None:
            from nowbox.terminal_vnc import VNCTerminal

            if self._ip_address is None:
                self._ip_address = self._wait_for_ip(timeout=10)
            self._terminal = VNCTerminal(self, host=self._ip_address, port=self._vnc_port)
        return self._terminal

    def run(
        self, command: Command, *, cwd: str | os.PathLike[str] | None = None, env: Mapping[str, str] | None = None, check: bool = False
    ) -> SandboxResult:
        normalized = normalize_command(command)
        working_dir = Path(cwd) if cwd is not None else self.root
        exec_cmd = self._machine_run_cmd(normalized, cwd=working_dir, env=env)
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

    # --- desktop terminal hooks ---

    def _desktop_tmux_send_keys(self, keys: list[str]) -> None:
        self._desktop_tmux_command(["send-keys", "-t", "nowbox", *keys])

    def _desktop_tmux_send_text(self, text: str) -> None:
        self._desktop_tmux_command(["send-keys", "-t", "nowbox", "-l", text])

    def _desktop_tmux_command(self, args: list[str]) -> None:
        subprocess.run(self._machine_run_cmd(["tmux", *args]), capture_output=True)

    def _desktop_screenshot_bytes(self) -> bytes | None:
        result = subprocess.run(self._machine_run_cmd(["scrot", "--silent", "-"], env={"DISPLAY": ":1"}), capture_output=True)
        return result.stdout if result.returncode == 0 and result.stdout else None

    def _container_read_file(self, path: str) -> str:
        result = subprocess.run(self._machine_run_cmd(["cat", path]), capture_output=True, text=True)
        return result.stdout

    # --- helpers ---

    def _machine_run_cmd(self, command: list[str] | str, *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> list[str]:
        args = ["container", "machine", "run", "--name", self._name, "--root", "--workdir", str(cwd or self.root)]
        if env:
            for key, value in env.items():
                args += ["--env", f"{key}={value}"]
        args.append("--")
        if isinstance(command, list):
            args += command
        else:
            args += ["sh", "-c", command]
        return args

    def _start_desktop(self) -> None:
        self._run_checked(
            ["container", "machine", "run", "--name", self._name, "--root", "--workdir", "/", "--detach", "--", "/entrypoint.sh"],
            f"Failed to start desktop process in machine {self._name!r}",
        )

    def _inspect(self) -> dict[str, object]:
        result = self._run_checked(["container", "machine", "inspect", self._name], f"Failed to inspect desktop machine {self._name!r}")
        data = json.loads(result.stdout)
        if not data:
            raise RuntimeError(f"Desktop machine {self._name!r} was not found")
        return data[0]

    def _wait_for_ip(self, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = self._inspect()
            ip_address = info.get("ipAddress")
            if isinstance(ip_address, str) and ip_address:
                return ip_address
            time.sleep(0.3)
        raise TimeoutError(f"Desktop machine {self._name!r} did not get an IP address within {timeout}s")

    def _wait_for_vnc(self, *, timeout: float) -> None:
        import socket as _socket

        assert self._ip_address is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock = _socket.create_connection((self._ip_address, self._vnc_port), timeout=1)
                sock.close()
                return
            except OSError:
                time.sleep(0.3)
        raise TimeoutError(f"VNC server on {self._ip_address}:{self._vnc_port} did not start within {timeout}s")

    def _run_checked(self, cmd: list[str], message: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result
        details = "\n".join(part for part in (result.stderr.strip(), result.stdout.strip()) if part)
        if not details:
            details = f"command exited with status {result.returncode}"
        raise RuntimeError(f"{message}:\n{details}")
