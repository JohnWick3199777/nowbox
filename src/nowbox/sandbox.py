from __future__ import annotations

import os
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
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("MP4 recording requires ffmpeg on PATH")

    options.path.parent.mkdir(parents=True, exist_ok=True)
    text = _recording_text(
        sandbox=sandbox,
        command=command,
        cwd=cwd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration_seconds,
    )
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
                str(options.path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return options.path


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
    command_text = command if isinstance(command, str) else shlex.join(command)
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
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "[": ("01110", "01000", "01000", "01000", "01000", "01000", "01110"),
    "]": ("01110", "00010", "00010", "00010", "00010", "00010", "01110"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _write_text_frame(text: str, path: Path, *, width: int, height: int, font_size: int) -> None:
    scale = max(1, font_size // 8)
    char_width = 6 * scale
    line_height = 9 * scale
    max_cols = max(1, (width - 64) // char_width)
    max_lines = max(1, (height - 64) // line_height)
    lines = []
    for raw_line in text.upper().splitlines():
        line = raw_line[:max_cols]
        lines.append(line)
        if len(lines) >= max_lines:
            break

    background = (11, 16, 32)
    foreground = (248, 248, 242)
    pixels = bytearray(background * width * height)

    for row, line in enumerate(lines):
        y = 32 + row * line_height
        for col, char in enumerate(line):
            x = 32 + col * char_width
            _draw_char(pixels, width, height, x, y, char, scale, foreground)

    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + bytes(pixels))


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
    glyph = FONT_5X7.get(char, FONT_5X7.get(" "))
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
