from __future__ import annotations

import os
import pty
import re
import select
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

Command = str | Sequence[str | os.PathLike[str]]
SandboxStatus = str


@dataclass(frozen=True)
class RecordingOptions:
    path: Path
    duration_seconds: float = 3.0
    width: int = 1280
    height: int = 720
    font_size: int = 24


@dataclass(frozen=True)
class SandboxResult:
    sandbox_id: str
    command: list[str] | str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    cwd: Path | None = None
    recording_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


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
        """Sandbox backend name, for example `local`, `apple-container`, or `docker`."""

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
        """Unix timestamp for when this sandbox object was created."""

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
        recording: RecordingOptions | bool = False,
    ) -> SandboxResult:
        """Run a command in the sandbox and return its captured result."""


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
        recording: RecordingOptions | bool = False,
    ) -> SandboxResult:
        normalized = _normalize_command(command)
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
        recording_options = _normalize_recording(recording, working_dir)
        recording_path = _record_mp4(
            sandbox=self,
            command=normalized,
            cwd=working_dir,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
            options=recording_options,
        )
        result = SandboxResult(
            sandbox_id=self.id,
            command=normalized,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
            cwd=working_dir,
            recording_path=recording_path,
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(
                result.exit_code,
                result.command,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result


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

    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        normalized = _normalize_command(command)
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
        env: Mapping[str, str] | None = None,
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
        env: Mapping[str, str] | None = None,
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
        self._pending_command = command
        return self.type(_command_text(command))

    def _ensure_prompt(self) -> None:
        if not self._line_started:
            self._record("o", "$ ")
            self._line_started = True

    def _execute(
        self,
        command: list[str] | str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> SandboxResult:
        normalized = _normalize_command(command)
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
        output = "".join(chunks)
        result = SandboxResult(
            sandbox_id=self._sandbox.id,
            command=normalized,
            exit_code=exit_code,
            stdout=output,
            stderr="",
            duration_seconds=duration,
            cwd=working_dir,
            recording_path=self._recording_path,
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(
                result.exit_code,
                result.command,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result

    def stop_recording(self) -> Path | None:
        path = self._recording_path
        if path is None:
            return None
        if path.suffix == ".cast":
            self._write_cast(path)
        else:
            self._write_mp4(path)
        self._recording_path = None
        self._recording_started_at = None
        return path

    def stop_record(self) -> Path | None:
        return self.stop_recording()

    def _record(self, stream: str, text: str) -> None:
        if self._recording_path is None or self._recording_started_at is None:
            return
        elapsed = time.monotonic() - self._recording_started_at
        self._events.append((elapsed, stream, text))
        self._transcript.append(text)

    def _write_cast(self, path: Path) -> None:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({"version": 2, "width": 100, "height": 30, "timestamp": int(time.time())})]
        for elapsed, stream, text in self._events:
            lines.append(json.dumps([elapsed, stream, text]))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_mp4(self, path: Path) -> None:
        _record_terminal_mp4(events=self._events, path=path, options=RecordingOptions(path=path))


def _normalize_command(command: Command) -> list[str] | str:
    if isinstance(command, str):
        return command
    return [str(part) for part in command]


def _normalize_recording(recording: RecordingOptions | bool, cwd: Path) -> RecordingOptions | None:
    if recording is False:
        return None
    if recording is True:
        return RecordingOptions(path=cwd / "sandbox-run.mp4")
    return recording


def _record_mp4(
    *,
    sandbox: LocalSandbox,
    command: list[str] | str,
    cwd: Path,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_seconds: float,
    options: RecordingOptions | None,
) -> Path | None:
    if options is None:
        return None
    text = _recording_text(
        sandbox=sandbox,
        command=command,
        cwd=cwd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration_seconds,
    )
    return _record_text_mp4(text=text, path=options.path, options=options)


def _command_text(command: list[str] | str) -> str:
    return command if isinstance(command, str) else shlex.join(command)


def _record_terminal_mp4(
    *,
    events: list[tuple[float, str, str]],
    path: Path,
    options: RecordingOptions,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("MP4 recording requires ffmpeg on PATH")

    path.parent.mkdir(parents=True, exist_ok=True)
    frames = _terminal_frames(events, options)
    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = Path(tmp)
        for index, (text, key) in enumerate(frames):
            frame_path = frame_dir / f"frame-{index:04d}.ppm"
            _write_text_frame(
                text,
                frame_path,
                width=options.width,
                height=options.height,
                font_size=options.font_size,
                keyboard_key=key,
                show_keyboard=True,
            )
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-framerate",
                "8",
                "-i",
                str(frame_dir / "frame-%04d.ppm"),
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return path


def _terminal_frames(events: list[tuple[float, str, str]], options: RecordingOptions) -> list[tuple[str, str | None]]:
    keyboard_height = 240
    terminal_height = options.height - keyboard_height
    cols = max(20, (options.width - 64) // max(1, (6 * max(1, options.font_size // 8))))
    rows = max(5, (terminal_height - 64) // max(1, (9 * max(1, options.font_size // 8))))
    state = TerminalScreen(cols=cols, rows=rows)
    frames: list[tuple[str, str | None]] = [(state.render(cursor=True), None)]
    for _, stream, text in events:
        cleaned = _strip_ansi(text)
        if stream == "k":
            for char in cleaned:
                key = _key_label(char)
                state.write(char)
                frames.extend([(state.render(cursor=True), key)] * _key_hold_frames(key))
        else:
            for chunk in _chunks(cleaned, 12):
                state.write(chunk)
                frames.append((state.render(cursor=True), None))
    if len(frames) > 240:
        step = max(1, len(frames) // 240)
        frames = frames[::step]
    if len(frames) == 1:
        frames.append(frames[0])
    frames.extend([(frames[-1][0], None)] * 12)
    return frames


def _key_label(char: str) -> str:
    if char == "\n":
        return "ENTER"
    if char == "\t":
        return "TAB"
    if char in {"\b", "\x7f"}:
        return "BACKSPACE"
    if char == " ":
        return "SPACE"
    return char.upper()


def _key_hold_frames(key: str) -> int:
    if key in {"ENTER", "BACKSPACE", "TAB", "SPACE"}:
        return 8
    return 4


class TerminalScreen:
    def __init__(self, *, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.lines = [""]

    def write(self, text: str) -> None:
        for char in text:
            if char == "\r":
                continue
            if char == "\n":
                self.lines.append("")
                continue
            if char == "\b" or char == "\x7f":
                self.lines[-1] = self.lines[-1][:-1]
                continue
            if char == "\t":
                self.lines[-1] += "    "
            elif char.isprintable():
                self.lines[-1] += char
            while len(self.lines[-1]) > self.cols:
                overflow = self.lines[-1][self.cols :]
                self.lines[-1] = self.lines[-1][: self.cols]
                self.lines.append(overflow)
            if len(self.lines) > self.rows:
                self.lines = self.lines[-self.rows :]

    def render(self, *, cursor: bool = False) -> str:
        visible = self.lines[-self.rows :]
        if cursor:
            visible = [*visible[:-1], f"{visible[-1]}_"]
        return "\n".join(visible)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _record_text_mp4(*, text: str, path: Path, options: RecordingOptions) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("MP4 recording requires ffmpeg on PATH")

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "frame.ppm"
        _write_text_frame(text, frame_path, width=options.width, height=options.height, font_size=options.font_size)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-i",
                str(frame_path),
                "-t",
                str(options.duration_seconds),
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return path


def _recording_text(
    *,
    sandbox: LocalSandbox,
    command: list[str] | str,
    cwd: Path,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_seconds: float,
) -> str:
    command_text = _command_text(command)
    transcript = textwrap.dedent(f"""
        nowbox sandbox execution
        sandbox: {sandbox.name} ({sandbox.id})
        backend: {sandbox.backend}
        cwd: {cwd}
        command: {command_text}
        exit: {exit_code}
        duration: {duration_seconds:.3f}s

        stdout:
        {stdout.strip() or "<empty>"}

        stderr:
        {stderr.strip() or "<empty>"}
    """).strip()
    return transcript[:4000]


FONT_5X7 = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "\\": ("10000", "01000", "01000", "00100", "00010", "00010", "00001"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "<": ("00010", "00100", "01000", "10000", "01000", "00100", "00010"),
    ">": ("01000", "00100", "00010", "00001", "00010", "00100", "01000"),
    "'": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    '"': ("01010", "01010", "00000", "00000", "00000", "00000", "00000"),
    "=": ("00000", "11111", "00000", "11111", "00000", "00000", "00000"),
    "$": ("00100", "01111", "10100", "01110", "00101", "11110", "00100"),
    "#": ("01010", "11111", "01010", "01010", "11111", "01010", "00000"),
    "|": ("00100", "00100", "00100", "00100", "00100", "00100", "00100"),
    ";": ("00000", "00100", "00100", "00000", "00100", "00100", "01000"),
    ",": ("00000", "00000", "00000", "00000", "00100", "00100", "01000"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "*": ("00000", "10101", "01110", "11111", "01110", "10101", "00000"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "[": ("01110", "01000", "01000", "01000", "01000", "01000", "01110"),
    "]": ("01110", "00010", "00010", "00010", "00010", "00010", "01110"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _write_text_frame(
    text: str,
    path: Path,
    *,
    width: int,
    height: int,
    font_size: int,
    keyboard_key: str | None = None,
    show_keyboard: bool = False,
) -> None:
    scale = max(1, font_size // 8)
    char_width = 6 * scale
    line_height = 9 * scale
    keyboard_height = 240 if show_keyboard else 0
    terminal_height = height - keyboard_height
    max_cols = max(1, (width - 64) // char_width)
    max_lines = max(1, (terminal_height - 64) // line_height)
    lines = []
    for raw_line in text.splitlines():
        line = raw_line[:max_cols]
        lines.append(line)
        if len(lines) >= max_lines:
            break

    background = (11, 16, 32)
    foreground = (248, 248, 242)
    pixels = bytearray(background * width * height)

    for row, line in enumerate(lines):
        y = 32 + row * line_height
        _draw_text(pixels, width, height, 32, y, line, scale, foreground)

    if show_keyboard:
        _draw_keyboard(pixels, width, height, keyboard_key, scale)

    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + bytes(pixels))


def _draw_text(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    text: str,
    scale: int,
    color: tuple[int, int, int],
) -> None:
    for col, char in enumerate(text):
        _draw_char(pixels, width, height, x + col * 6 * scale, y, char, scale, color)


def _draw_keyboard(
    pixels: bytearray,
    width: int,
    height: int,
    active_key: str | None,
    scale: int,
) -> None:
    top = height - 220
    panel = (18, 24, 42)
    border = (70, 80, 110)
    normal = (36, 43, 66)
    active = (250, 204, 21)
    text = (248, 248, 242)
    active_text = (11, 16, 32)
    _fill_rect(pixels, width, height, 20, top - 16, width - 40, 216, panel)
    _draw_rect(pixels, width, height, 20, top - 16, width - 40, 216, border)
    _draw_text(pixels, width, height, 36, top - 2, "KEYBOARD", scale, text)
    if active_key is not None:
        _fill_rect(pixels, width, height, width - 290, top - 10, 250, 34, active)
        _draw_rect(pixels, width, height, width - 290, top - 10, 250, 34, border)
        _draw_text(pixels, width, height, width - 276, top, f"KEY {active_key}", max(1, scale - 1), active_text)

    rows = [
        ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
        ["A", "S", "D", "F", "G", "H", "J", "K", "L", "ENTER"],
        ["Z", "X", "C", "V", "B", "N", "M", "SPACE", "BACKSPACE"],
    ]
    key_h = 42
    key_gap = 12
    y = top + 52
    for row in rows:
        total_w = sum(_key_width(k) for k in row) + key_gap * (len(row) - 1)
        x = (width - total_w) // 2
        for key in row:
            key_w = _key_width(key)
            is_active = active_key == key or (
                active_key is not None and len(active_key) == 1 and key == active_key.upper()
            )
            _fill_rect(pixels, width, height, x, y, key_w, key_h, active if is_active else normal)
            _draw_rect(pixels, width, height, x, y, key_w, key_h, border)
            label_scale = max(1, scale - 1)
            label_w = len(key) * 6 * label_scale
            _draw_text(
                pixels,
                width,
                height,
                x + max(4, (key_w - label_w) // 2),
                y + 14,
                key,
                label_scale,
                active_text if is_active else text,
            )
            x += key_w + key_gap
        y += key_h + key_gap


def _key_width(key: str) -> int:
    if key == "BACKSPACE":
        return 180
    if key == "ENTER":
        return 124
    if key == "SPACE":
        return 260
    return 58


def _fill_rect(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int],
) -> None:
    for py in range(max(0, y), min(height, y + rect_height)):
        for px in range(max(0, x), min(width, x + rect_width)):
            offset = (py * width + px) * 3
            pixels[offset : offset + 3] = bytes(color)


def _draw_rect(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int],
) -> None:
    _fill_rect(pixels, width, height, x, y, rect_width, 1, color)
    _fill_rect(pixels, width, height, x, y + rect_height - 1, rect_width, 1, color)
    _fill_rect(pixels, width, height, x, y, 1, rect_height, color)
    _fill_rect(pixels, width, height, x + rect_width - 1, y, 1, rect_height, color)


def _draw_char(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    char: str,
    scale: int,
    color: tuple[int, int, int],
) -> None:
    glyph = FONT_5X7.get(char) or FONT_5X7.get(char.upper(), FONT_5X7.get(" "))
    if glyph is None:
        return
    for gy, row in enumerate(glyph):
        for gx, bit in enumerate(row):
            if bit != "1":
                continue
            for sy in range(scale):
                for sx in range(scale):
                    px = x + gx * scale + sx
                    py = y + gy * scale + sy
                    if 0 <= px < width and 0 <= py < height:
                        offset = (py * width + px) * 3
                        pixels[offset : offset + 3] = bytes(color)


def shell(command: str) -> list[str]:
    return shlex.split(command)
