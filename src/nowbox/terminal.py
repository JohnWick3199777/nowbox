from __future__ import annotations

import os
import pty
import select
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from nowbox.recording import record_terminal_mp4, write_cast
from nowbox.session import TerminalSession
from nowbox.types import Command, RecordingMetadata, RecordingOptions, SandboxResult
from nowbox.utils import normalize_command, strip_ansi

if TYPE_CHECKING:
    from nowbox.sandbox.base import Sandbox


class SandboxTerminal:
    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox
        self._recording_path: Path | None = None
        self._recording_started_at: float | None = None
        self._recording_started_at_iso: str | None = None
        self._events: list[tuple[float, str, str]] = []
        self._transcript: list[str] = []
        self._exit_codes: list[int] = []
        self._line_started = False
        self._pending_text = ""
        self._pending_command: list[str] | str | None = None
        self._current_cwd: Path | None = None

    @property
    def _effective_cwd(self) -> Path:
        return self._current_cwd if self._current_cwd is not None else self._sandbox.root

    def _cwd_label(self) -> str:
        cwd = self._effective_cwd
        try:
            return "~/" + str(cwd.relative_to(Path.home())).lstrip(".")
        except ValueError:
            return str(cwd)

    @property
    def is_recording(self) -> bool:
        return self._recording_path is not None

    def start_recording(self, path: str | os.PathLike[str] | None = None) -> Path:
        self._recording_path = Path(path) if path is not None else self._sandbox.root / "artifacts" / "terminal.mp4"
        self._recording_started_at = time.monotonic()
        self._recording_started_at_iso = datetime.now(timezone.utc).isoformat()
        self._events.clear()
        self._transcript.clear()
        self._exit_codes.clear()
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
        ended_at = datetime.now(timezone.utc).isoformat()
        duration = time.monotonic() - self._recording_started_at if self._recording_started_at is not None else 0.0
        metadata = RecordingMetadata(
            sandbox_id=self._sandbox.id,
            sandbox_name=self._sandbox.name,
            sandbox_backend=self._sandbox.backend,
            started_at=self._recording_started_at_iso or ended_at,
            ended_at=ended_at,
            duration_seconds=round(duration, 3),
            exit_codes=list(self._exit_codes),
            image=self._sandbox.image,
            platform=self._sandbox.platform,
        )
        if path.suffix == ".cast":
            write_cast(path, self._events, metadata)
        else:
            record_terminal_mp4(events=self._events, path=path, options=RecordingOptions(path=path), metadata=metadata)
        self._recording_path = None
        self._recording_started_at = None
        self._recording_started_at_iso = None
        return path

    def stop_record(self) -> Path | None:
        return self.stop_recording()

    def session(self) -> TerminalSession:
        """Return a persistent interactive PTY session for this sandbox.

        Use as a context manager::

            with terminal.session() as sess:
                sess.type("git br").key("tab")
                output = sess.expect_prompt()
        """
        return TerminalSession(self._sandbox, self)

    def run(
        self, command: Command, *, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None, check: bool = False
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

    def paste(self, text: str) -> SandboxTerminal:
        self._ensure_prompt()
        self._pending_text += text
        self._record("p", text)
        self._pending_command = self._pending_text
        return self

    def key(
        self, key: str, *, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None, check: bool = False
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

    def enter(self, *, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None, check: bool = False) -> SandboxResult:
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
                cwd=Path(cwd) if cwd is not None else self._effective_cwd,
                recording_path=self._recording_path,
            )
        self._record("k", "\n")
        self._line_started = False
        self._pending_text = ""
        self._pending_command = None
        # Handle cd locally — there's no persistent shell between commands
        from nowbox.utils import command_text as _cmd_text
        cmd_str = (_cmd_text(command) if not isinstance(command, str) else command).strip()
        if cmd_str == "cd" or cmd_str.startswith("cd "):
            return self._execute_cd(cmd_str, cwd=cwd)
        return self._execute(command, cwd=cwd, env=env, check=check)

    def _type_command(self, command: list[str] | str) -> SandboxTerminal:
        from nowbox.utils import command_text

        self._pending_command = command
        return self.type(command_text(command))

    def _ensure_prompt(self) -> None:
        if not self._line_started:
            cwd = self._cwd_label()
            # cyan dir, reset, yellow $, reset
            self._record("o", f"\x1b[36m{cwd}\x1b[0m \x1b[33m$\x1b[0m ")
            self._line_started = True

    def _record(self, stream: str, text: str) -> None:
        if self._recording_path is None or self._recording_started_at is None:
            return
        elapsed = time.monotonic() - self._recording_started_at
        self._events.append((elapsed, stream, text))
        self._transcript.append(text)

    def _execute_cd(self, cmd_str: str, *, cwd: str | os.PathLike[str] | None = None) -> SandboxResult:
        base = Path(cwd) if cwd is not None else self._effective_cwd
        parts = cmd_str.split(None, 1)
        target = parts[1] if len(parts) > 1 else "~"
        if target == "~" or target == "$HOME":
            new_cwd = Path.home()
        elif target.startswith("~/"):
            new_cwd = Path.home() / target[2:]
        elif target.startswith("/"):
            new_cwd = Path(target)
        else:
            new_cwd = base / target
        self._current_cwd = new_cwd
        return SandboxResult(
            sandbox_id=self._sandbox.id,
            command=cmd_str,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0,
            cwd=new_cwd,
            recording_path=self._recording_path,
        )

    def _execute(
        self, command: list[str] | str, *, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None, check: bool = False
    ) -> SandboxResult:
        normalized = normalize_command(command)
        working_dir = Path(cwd) if cwd is not None else self._effective_cwd
        exec_cmd, host_cwd = self._sandbox._build_exec(normalized, working_dir)
        started = time.monotonic()
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                exec_cmd,
                cwd=host_cwd,
                env={**os.environ, **env} if env is not None else None,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                text=False,
                shell=isinstance(exec_cmd, str),
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
        if self._recording_path is not None:
            self._exit_codes.append(exit_code)
        self._current_cwd = working_dir
        raw = "".join(chunks)
        result = SandboxResult(
            sandbox_id=self._sandbox.id,
            command=normalized,
            exit_code=exit_code,
            stdout=raw,
            stderr="",
            duration_seconds=duration,
            cwd=working_dir,
            recording_path=self._recording_path,
            output=strip_ansi(raw).strip(),
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(result.exit_code, result.command, output=result.stdout, stderr=result.stderr)
        return result
