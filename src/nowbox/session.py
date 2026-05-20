"""Persistent interactive PTY session for a Sandbox.

Unlike the one-shot `SandboxTerminal._execute` path (which spawns a fresh
process per command), `TerminalSession` keeps a single bash process alive so
that shell state — completion functions, environment variables, `cd` — persists
across calls.
"""
from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nowbox.sandbox.base import Sandbox
    from nowbox.terminal import SandboxTerminal

# Sentinel printed by PROMPT_COMMAND before each bash prompt.
# Using printf so it never appears literally in the echo of our setup command.
_SENTINEL = "NOWBOX_READY"
_SENTINEL_RE = re.compile(re.escape(_SENTINEL))
# The setup we send into bash: PROMPT_COMMAND prints the sentinel; PS1 is plain.
_BASH_SETUP = "export PROMPT_COMMAND='printf NOWBOX_READY'; export PS1='$ '\n"


class TerminalSession:
    """A persistent interactive bash PTY session.

    Obtain via ``SandboxTerminal.session()`` — don't instantiate directly::

        with terminal.session() as sess:
            sess.type("ls -l").key("enter")
            output = sess.expect_prompt()
    """

    def __init__(self, sandbox: Sandbox, terminal: SandboxTerminal) -> None:
        self._sandbox = sandbox
        self._terminal = terminal
        self._master_fd: int = -1
        self._proc: subprocess.Popen[bytes] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> TerminalSession:
        cmd = self._sandbox._build_shell_cmd()
        master_fd, slave_fd = pty.openpty()
        self._proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            text=False,
            close_fds=True,
        )
        os.close(slave_fd)
        self._master_fd = master_fd
        # Suppress echo while we set up PROMPT_COMMAND so the sentinel string
        # never appears inside the echoed setup command itself.
        self._send("stty -echo\n")
        time.sleep(0.05)
        self._drain(timeout=0.2)
        self._send(_BASH_SETUP)
        self._send("stty echo\n")
        # Trigger one empty command to flush the first prompt.
        self._send("\n")
        self._read_until_sentinel(timeout=8)
        self._drain(timeout=0.15)  # consume trailing "$ "
        return self

    def stop(self) -> None:
        if self._master_fd >= 0:
            try:
                os.write(self._master_fd, b"exit\n")
            except OSError:
                pass
            time.sleep(0.05)
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = -1
        if self._proc is not None:
            self._proc.wait()
            self._proc = None

    def __enter__(self) -> TerminalSession:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def type(self, text: str) -> TerminalSession:
        """Send *text* as keystrokes, recording each character."""
        self._terminal._ensure_prompt()
        for ch in text:
            self._terminal._record("k", ch)
        self._send(text)
        return self

    def paste(self, text: str) -> TerminalSession:
        """Send *text* as a paste event (single recording entry)."""
        self._terminal._ensure_prompt()
        self._terminal._record("p", text)
        self._send(text)
        return self

    def key(self, key: str) -> TerminalSession:
        """Send a special key.

        Supported: ``enter``, ``return``, ``tab``, ``backspace``.
        Single printable characters are forwarded via :meth:`type`.
        """
        k = key.lower()
        if k in {"enter", "return"}:
            self._terminal._record("k", "\n")
            self._send("\n")
            self._terminal._line_started = False
        elif k == "tab":
            self._terminal._record("k", "\t")
            self._send("\t")
            # Give bash time to expand the completion, then capture output.
            time.sleep(0.15)
            expanded = self._drain(timeout=0.4)
            if expanded:
                self._terminal._record("o", expanded)
        elif k in {"backspace", "delete"}:
            self._terminal._record("k", "\b")
            self._send("\x7f")
        elif len(key) == 1:
            self.type(key)
        else:
            raise ValueError(f"unknown key: {key!r}")
        return self

    # ------------------------------------------------------------------
    # Output / synchronisation
    # ------------------------------------------------------------------

    def expect_prompt(self, *, timeout: float = 10.0) -> str:
        """Block until the shell prompt appears; return everything printed before it.

        Strips the PTY command echo (first line) and the sentinel so only the
        command's actual output is returned.
        """
        raw = self._read_until_sentinel(timeout=timeout)
        self._drain(timeout=0.1)  # consume trailing "$ "
        # Split on CRLF, skip first line (PTY echo of the command we typed),
        # stop before the sentinel line.
        lines = raw.replace("\r\n", "\n").split("\n")
        output_lines: list[str] = []
        skip_first = True
        for line in lines:
            if _SENTINEL in line:
                break
            if skip_first:
                skip_first = False
                continue
            output_lines.append(line)
        output = "\n".join(output_lines).strip()
        if output:
            self._terminal._record("o", output + "\n")
        # Reset so the next type()/paste() call emits a fresh prompt.
        self._terminal._line_started = False
        return output

    def run(self, command: str, *, timeout: float = 30.0) -> str:
        """Type *command*, press Enter, wait for the prompt, return output."""
        self.paste(command)
        self.key("enter")
        return self.expect_prompt(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, text: str) -> None:
        if self._master_fd < 0:
            raise RuntimeError("Session is not started")
        os.write(self._master_fd, text.encode())

    def _drain(self, *, timeout: float) -> str:
        """Read all available bytes up to *timeout* seconds."""
        chunks: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([self._master_fd], [], [], min(remaining, 0.05))
            if self._master_fd not in readable:
                if chunks:
                    break
                continue
            try:
                data = os.read(self._master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data.decode(errors="replace"))
        return "".join(chunks)

    def _read_until_sentinel(self, *, timeout: float) -> str:
        """Read bytes until the sentinel appears or timeout expires."""
        buf = ""
        deadline = time.monotonic() + timeout
        while _SENTINEL not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([self._master_fd], [], [], min(remaining, 0.1))
            if self._master_fd not in readable:
                continue
            try:
                data = os.read(self._master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data.decode(errors="replace")
        return buf
