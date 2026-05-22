from __future__ import annotations

import os
import pty
import re
import select
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from nowbox.recording import record_terminal_mp4, write_cast
from nowbox.types import Command, RecordingMetadata, RecordingOptions, SandboxResult
from nowbox.utils import normalize_command, strip_ansi

if TYPE_CHECKING:
    from nowbox.sandbox.base import Sandbox

# ---------------------------------------------------------------------------
# PTY sentinel — printed by PROMPT_COMMAND so it never appears in command echo
# Format: NOWBOX_READY:<exit_code>:<cwd>
# ---------------------------------------------------------------------------
_SENTINEL_PREFIX = "NOWBOX_READY:"
_SENTINEL_RE = re.compile(r"NOWBOX_READY:(\d+):([^\r\n]*)")
_BASH_SETUP = (
    "stty -echo\n"
    r"""export PROMPT_COMMAND='printf "NOWBOX_READY:$?:$PWD\n"'""" + "\n"
    "export PS1=''\n"
    "bind 'set enable-bracketed-paste off' 2>/dev/null || true\n"
    # Echo stays OFF — we record keystrokes via _record(); PTY echo is redundant
    # and causes long commands to bleed into output when they wrap.
)


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
        # persistent PTY
        self._pty_master: int = -1
        self._pty_proc: subprocess.Popen[bytes] | None = None
        self._pty_has_input: bool = False  # True when PTY holds buffered text (e.g. after tab)

    # ------------------------------------------------------------------
    # PTY lifecycle
    # ------------------------------------------------------------------

    def _ensure_pty(self) -> None:
        if self._pty_master >= 0:
            return
        cmd = self._sandbox._build_shell_cmd()
        master_fd, slave_fd = pty.openpty()
        self._pty_proc = subprocess.Popen(
            cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, text=False, close_fds=True
        )
        os.close(slave_fd)
        self._pty_master = master_fd
        # Configure bash: silent sentinel via PROMPT_COMMAND, blank PS1
        self._pty_send("stty -echo\n")
        time.sleep(0.05)
        self._pty_drain(timeout=0.2)
        self._pty_send(_BASH_SETUP)
        # Trigger one empty command to flush the first sentinel
        self._pty_send("\n")
        raw = self._pty_read_until_sentinel(timeout=8)
        self._pty_drain(timeout=0.1)
        m = _SENTINEL_RE.search(raw)
        if m:
            self._current_cwd = Path(m.group(2)) if m.group(2) else self._sandbox.root

    def close(self) -> None:
        """Close the persistent PTY session."""
        if self._pty_master >= 0:
            try:
                os.write(self._pty_master, b"exit\n")
            except OSError:
                pass
            time.sleep(0.05)
            try:
                os.close(self._pty_master)
            except OSError:
                pass
            self._pty_master = -1
        if self._pty_proc is not None:
            self._pty_proc.wait()
            self._pty_proc = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # PTY I/O helpers
    # ------------------------------------------------------------------

    def _pty_send(self, text: str) -> None:
        os.write(self._pty_master, text.encode())

    def _pty_drain(self, *, timeout: float) -> str:
        chunks: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([self._pty_master], [], [], min(remaining, 0.05))
            if self._pty_master not in readable:
                if chunks:
                    break
                continue
            try:
                data = os.read(self._pty_master, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data.decode(errors="replace"))
        return "".join(chunks)

    def _pty_read_until_sentinel(self, *, timeout: float) -> str:
        buf = ""
        deadline = time.monotonic() + timeout
        while _SENTINEL_PREFIX not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([self._pty_master], [], [], min(remaining, 0.1))
            if self._pty_master not in readable:
                continue
            try:
                data = os.read(self._pty_master, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data.decode(errors="replace")
        return buf

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Input API
    # ------------------------------------------------------------------

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
            self._ensure_prompt()
            self._record("k", "\t")
            self._ensure_pty()
            # Send buffered text to PTY first so bash has something to complete
            if self._pending_text:
                self._pty_send(self._pending_text)
                self._pty_drain(timeout=0.05)
            self._pty_send("\t")
            time.sleep(0.15)
            expanded = self._pty_drain(timeout=0.4)
            if expanded:
                self._record("o", expanded)
                visible = strip_ansi(expanded).rstrip("\r\n")
                if visible and not visible.startswith("\x07"):  # ignore bell-only responses
                    self._pending_text += visible.lstrip(self._pending_text[-len(visible):] if self._pending_text else "")
                    self._pending_command = self._pending_text
            # PTY now holds the (possibly expanded) line; enter() only needs to send \n
            self._pty_has_input = True
            return self
        if len(key) == 1:
            return self.type(key)
        raise ValueError(f"unknown key: {key}")

    def enter(
        self, *, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None, check: bool = False
    ) -> SandboxResult:
        command = self._pending_command if self._pending_command is not None else self._pending_text
        self._record("k", "\n")
        self._line_started = False
        self._pending_text = ""
        self._pending_command = None

        if not command and not self._pty_has_input:
            return SandboxResult(
                sandbox_id=self._sandbox.id,
                command="",
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0,
                cwd=self._current_cwd or self._sandbox.root,
                recording_path=self._recording_path,
            )

        self._ensure_pty()
        started = time.monotonic()

        from nowbox.utils import command_text as _cmd_text
        cmd_str = (_cmd_text(command) if not isinstance(command, str) else command).strip()

        # Silent cwd change if caller overrides
        if cwd is not None and Path(cwd) != self._current_cwd:
            self._pty_send(f"cd {shlex.quote(str(cwd))}\n")
            raw_cd = self._pty_read_until_sentinel(timeout=5)
            self._pty_drain(timeout=0.1)
            m = _SENTINEL_RE.search(raw_cd)
            if m and m.group(2):
                self._current_cwd = Path(m.group(2))

        if self._pty_has_input:
            # PTY already holds the line from tab completion; just press enter
            self._pty_has_input = False
            self._pty_send("\n")
        else:
            # Prefix env vars inline if provided
            if env:
                prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
                cmd_str = f"{prefix} {cmd_str}"
            self._pty_send(cmd_str + "\n")
        raw = self._pty_read_until_sentinel(timeout=30)
        self._pty_drain(timeout=0.1)
        duration = time.monotonic() - started

        # Parse sentinel for exit code + new cwd
        m = _SENTINEL_RE.search(raw)
        exit_code = int(m.group(1)) if m else 0
        if m and m.group(2):
            self._current_cwd = Path(m.group(2))

        # Parse output: normalise newlines (handle \r\r\n from nested PTY),
        # take everything before the sentinel line. Echo is disabled so there
        # is no command echo to skip.
        normalised = raw.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
        output_lines: list[str] = []
        for line in normalised.split("\n"):
            if _SENTINEL_PREFIX in line:
                break
            output_lines.append(line)
        output = "\n".join(output_lines).strip()

        if output:
            self._record("o", output + "\n")
        if self._recording_path is not None:
            self._exit_codes.append(exit_code)

        result = SandboxResult(
            sandbox_id=self._sandbox.id,
            command=command,
            exit_code=exit_code,
            stdout=output,
            stderr="",
            duration_seconds=duration,
            cwd=self._current_cwd or self._sandbox.root,
            recording_path=self._recording_path,
            output=strip_ansi(output).strip(),
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(result.exit_code, result.command, output=result.stdout, stderr=result.stderr)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _type_command(self, command: list[str] | str) -> SandboxTerminal:
        from nowbox.utils import command_text
        self._pending_command = command
        return self.type(command_text(command))

    @property
    def _effective_cwd(self) -> Path:
        return self._current_cwd if self._current_cwd is not None else self._sandbox.root

    def _cwd_label(self) -> str:
        cwd = self._effective_cwd
        try:
            return "~/" + str(cwd.relative_to(Path.home())).lstrip(".")
        except ValueError:
            return str(cwd)

    def _ensure_prompt(self) -> None:
        if not self._line_started:
            cwd = self._cwd_label()
            self._record("o", f"\x1b[36m{cwd}\x1b[0m \x1b[33m$\x1b[0m ")
            self._line_started = True

    def _record(self, stream: str, text: str) -> None:
        if self._recording_path is None or self._recording_started_at is None:
            return
        elapsed = time.monotonic() - self._recording_started_at
        self._events.append((elapsed, stream, text))
        self._transcript.append(text)
