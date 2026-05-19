from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

from nowbox.types import RecordingOptions
from nowbox.utils import chunks, command_text, strip_ansi

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_recording(recording: RecordingOptions | bool, cwd: Path) -> RecordingOptions | None:
    if recording is False:
        return None
    if recording is True:
        return RecordingOptions(path=cwd / "sandbox-run.mp4")
    return recording


# ---------------------------------------------------------------------------
# Static-frame MP4 (sandbox.run)
# ---------------------------------------------------------------------------


def record_run_mp4(
    *,
    sandbox_id: str,
    sandbox_name: str,
    sandbox_backend: str,
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
    text = _run_transcript(
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        sandbox_backend=sandbox_backend,
        command=command,
        cwd=cwd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration_seconds,
    )
    return _write_text_mp4(text=text, path=options.path, options=options)


def _run_transcript(
    *,
    sandbox_id: str,
    sandbox_name: str,
    sandbox_backend: str,
    command: list[str] | str,
    cwd: Path,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_seconds: float,
) -> str:
    cmd = command_text(command)
    transcript = textwrap.dedent(f"""
        nowbox sandbox execution
        sandbox: {sandbox_name} ({sandbox_id})
        backend: {sandbox_backend}
        cwd: {cwd}
        command: {cmd}
        exit: {exit_code}
        duration: {duration_seconds:.3f}s

        stdout:
        {stdout.strip() or "<empty>"}

        stderr:
        {stderr.strip() or "<empty>"}
    """).strip()
    return transcript[:4000]


def _write_text_mp4(*, text: str, path: Path, options: RecordingOptions) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("MP4 recording requires ffmpeg on PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "frame.ppm"
        write_frame(text, frame_path, width=options.width, height=options.height, font_size=options.font_size)
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


# ---------------------------------------------------------------------------
# Terminal MP4 (event-driven)
# ---------------------------------------------------------------------------


def record_terminal_mp4(
    *,
    events: list[tuple[float, str, str]],
    path: Path,
    options: RecordingOptions,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("MP4 recording requires ffmpeg on PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = build_terminal_frames(events, options)
    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = Path(tmp)
        for index, (text, key) in enumerate(frames):
            frame_path = frame_dir / f"frame-{index:04d}.ppm"
            write_frame(
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


def write_cast(path: Path, events: list[tuple[float, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"version": 2, "width": 100, "height": 30, "timestamp": int(time.time())})]
    for elapsed, stream, text in events:
        lines.append(json.dumps([elapsed, stream, text]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_terminal_frames(
    events: list[tuple[float, str, str]], options: RecordingOptions
) -> list[tuple[str, str | None]]:
    keyboard_height = 240
    terminal_height = options.height - keyboard_height
    cols = max(20, (options.width - 64) // max(1, 6 * max(1, options.font_size // 8)))
    rows = max(5, (terminal_height - 64) // max(1, 9 * max(1, options.font_size // 8)))
    state = TerminalScreen(cols=cols, rows=rows)
    frames: list[tuple[str, str | None]] = [(state.render(cursor=True), None)]
    for _, stream, text in events:
        cleaned = strip_ansi(text)
        if stream == "k":
            for char in cleaned:
                key = _key_label(char)
                state.write(char)
                frames.extend([(state.render(cursor=True), key)] * _key_hold_frames(key))
        else:
            for chunk in chunks(cleaned, 12):
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
    return 8 if key in {"ENTER", "BACKSPACE", "TAB", "SPACE"} else 4


def _key_width(key: str) -> int:
    if key == "BACKSPACE":
        return 180
    if key == "ENTER":
        return 124
    if key == "SPACE":
        return 260
    return 58


# ---------------------------------------------------------------------------
# Terminal screen model
# ---------------------------------------------------------------------------


class TerminalScreen:
    def __init__(self, *, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.lines: list[str] = [""]

    def write(self, text: str) -> None:
        for char in text:
            if char == "\r":
                continue
            if char == "\n":
                self.lines.append("")
                continue
            if char in {"\b", "\x7f"}:
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


# ---------------------------------------------------------------------------
# Pixel renderer
# ---------------------------------------------------------------------------


FONT_5X7: dict[str, tuple[str, ...]] = {
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


def write_frame(
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

    lines: list[str] = []
    for raw_line in text.splitlines():
        lines.append(raw_line[:max_cols])
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
    text_color = (248, 248, 242)
    active_text = (11, 16, 32)

    _fill_rect(pixels, width, height, 20, top - 16, width - 40, 216, panel)
    _draw_rect(pixels, width, height, 20, top - 16, width - 40, 216, border)
    _draw_text(pixels, width, height, 36, top - 2, "KEYBOARD", scale, text_color)

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
            bg = active if is_active else normal
            fg = active_text if is_active else text_color
            _fill_rect(pixels, width, height, x, y, key_w, key_h, bg)
            _draw_rect(pixels, width, height, x, y, key_w, key_h, border)
            label_scale = max(1, scale - 1)
            label_w = len(key) * 6 * label_scale
            _draw_text(pixels, width, height, x + max(4, (key_w - label_w) // 2), y + 14, key, label_scale, fg)
            x += key_w + key_gap
        y += key_h + key_gap


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
