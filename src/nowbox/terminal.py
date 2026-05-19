from __future__ import annotations

import os
import pty
import select
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from nowbox.recording import record_terminal_mp4, write_cast
from nowbox.types import Command, RecordingOptions, SandboxResult
from nowbox.utils import normalize_command

if TYPE_CHECKING:
    from nowbox.sandbox import LocalSandbox


class SandboxTerminal:
    def __init__(self, sandbox: LocalSandbox) -> None:
        self._sandbox = sandbox
        self._recording_path: Path | None = None
        self._recording_started_at: float | None = None
        self._events: list[tuple[float, str, str]] = []
        self._transcript: list[str] = []
        self._line_started = False
        self._pending_text = ""
        self._pending_command: list[str] | str | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording_path is not None

    def start_recording(self, path: str | os.PathLike[str] | None = None) -> Path:
        self._recording_path = Path(path) if path is not None else self._sandbox.root / "artifacts" / "terminal.mp4"
        self._recording_started_at = time.monotonic()
        self._events.clear()
        self._transcript.clear()
        self._line_started = False
        self._pending_text = ""
        self._pending_command = None
        return self._recording_path

    def start_record(self, path: str | os.PathLike[str] | None = None) -> Path:
        return self.start_recording(path)

    def stop_recording(self) -> Path | None:
        path = self._recording_path
        if path is None:
            return None
        if path.suffix == ".cast":
            write_cast(path, self._events)
        else:
            record_terminal_mp4(events=self._events, path=path, options=RecordingOptions(path=path))
        self._recording_path = None
        self._recording_started_at = None
        return path

    def stop_record(self) -> Path | None:
        return self.stop_recording()

    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        normalized = normalize_command(command)
        return self._type_command(normalized).enter(cwd=cwd, env=env, check=check)

    def type(self, text: str) -> SandboxTerminal:
        self._ensure_prompt()
        for char in text:
            self._pending_text += char
            self._record("k", char)
        self._pending_command = self._pending_text
        return self

    def key(
        self,
        key: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> SandboxTerminal | SandboxResult:
        normalized = key.lower()
        if normalized in {"enter", "return"}:
            return self.enter(cwd=cwd, env=env, check=check)
        if normalized in {"backspace", "delete"}:
            if self._pending_text:
                self._pending_text = self._pending_text[:-1]
                self._pending_command = self._pending_text
                self._record("k", "\b")
            return self
        if normalized == "tab":
            self._pending_text += "\t"
            self._pending_command = self._pending_text
            self._record("k", "\t")
            return self
        if len(key) == 1:
            return self.type(key)
        raise ValueError(f"unknown key: {key}")

    def enter(
        self,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        command = self._pending_command if self._pending_command is not None else self._pending_text
        if not command:
            self._ensure_prompt()
            self._record("k", "\n")
            self._line_started = False
            return SandboxResult(
                sandbox_id=self._sandbox.id,
                command="",
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0,
                cwd=Path(cwd) if cwd is not None else self._sandbox.root,
                recording_path=self._recording_path,
            )
        self._record("k", "\n")
        self._line_started = False
        self._pending_text = ""
        self._pending_command = None
        return self._execute(command, cwd=cwd, env=env, check=check)

    def _type_command(self, command: list[str] | str) -> SandboxTerminal:
        from nowbox.utils import command_text

        self._pending_command = command
        return self.type(command_text(command))

    def _ensure_prompt(self) -> None:
        if not self._line_started:
            self._record("o", "$ ")
            self._line_started = True

    def _record(self, stream: str, text: str) -> None:
        if self._recording_path is None or self._recording_started_at is None:
            return
        elapsed = time.monotonic() - self._recording_started_at
        self._events.append((elapsed, stream, text))
        self._transcript.append(text)

    def _execute(
        self,
        command: list[str] | str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        normalized = normalize_command(command)
        working_dir = Path(cwd) if cwd is not None else self._sandbox.root
        started = time.monotonic()
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                normalized,
                cwd=working_dir,
                env={**os.environ, **env} if env is not None else None,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                text=False,
                shell=isinstance(normalized, str),
                close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = -1
            chunks: list[str] = []
            while True:
                readable, _, _ = select.select([master_fd], [], [], 0.05)
                if master_fd in readable:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        data = b""
                    if data:
                        text = data.decode(errors="replace")
                        chunks.append(text)
                        self._record("o", text)
                if proc.poll() is not None:
                    while True:
                        readable, _, _ = select.select([master_fd], [], [], 0)
                        if master_fd not in readable:
                            break
                        try:
                            data = os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not data:
                            break
                        text = data.decode(errors="replace")
                        chunks.append(text)
                        self._record("o", text)
                    break
            exit_code = proc.wait()
        finally:
            if slave_fd >= 0:
                os.close(slave_fd)
            os.close(master_fd)

        duration = time.monotonic() - started
        result = SandboxResult(
            sandbox_id=self._sandbox.id,
            command=normalized,
            exit_code=exit_code,
            stdout="".join(chunks),
            stderr="",
            duration_seconds=duration,
            cwd=working_dir,
            recording_path=self._recording_path,
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(
                result.exit_code, result.command, output=result.stdout, stderr=result.stderr
            )
        return result
