from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw
from PIL import ImageFont as PILFont

from nowbox.types import RecordingMetadata, RecordingOptions
from nowbox.utils import chunks, command_text, strip_ansi

Color = tuple[int, int, int]
StyledLine = list[tuple[str, Color]]
StyledScreen = list[StyledLine]

_DEFAULT_FG: Color = (212, 212, 212)

_ANSI_COLORS: dict[int, Color] = {
    30: (0, 0, 0),        31: (205, 49, 49),
    32: (13, 188, 121),   33: (229, 229, 16),
    34: (36, 114, 200),   35: (188, 63, 188),
    36: (17, 168, 205),   37: (229, 229, 229),
    90: (102, 102, 102),  91: (241, 76, 76),
    92: (35, 209, 139),   93: (245, 245, 67),
    94: (59, 142, 234),   95: (214, 112, 214),
    96: (41, 184, 219),   97: (229, 229, 229),
}

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
        frame_path = Path(tmp) / "frame.png"
        write_frame(text, frame_path, width=options.width, height=options.height, font_size=options.font_size)
        subprocess.run(
            [ffmpeg, "-y", "-loop", "1", "-i", str(frame_path), "-t", str(options.duration_seconds), "-pix_fmt", "yuv420p", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    return path


# ---------------------------------------------------------------------------
# Terminal MP4 (event-driven)
# ---------------------------------------------------------------------------


def record_terminal_mp4(
    *, events: list[tuple[float, str, str]], path: Path, options: RecordingOptions, metadata: RecordingMetadata | None = None
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("MP4 recording requires ffmpeg on PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = build_terminal_frames(events, options)
    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = Path(tmp)
        for index, (styled, key) in enumerate(frames):
            frame_path = frame_dir / f"frame-{index:04d}.png"
            write_frame(
                styled,
                frame_path,
                width=options.width,
                height=options.height,
                font_size=options.font_size,
                keyboard_key=key,
                show_keyboard=True,
                metadata=metadata,
            )
        subprocess.run(
            [ffmpeg, "-y", "-framerate", "8", "-i", str(frame_dir / "frame-%04d.png"), "-pix_fmt", "yuv420p", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    return path



def write_cast(path: Path, events: list[tuple[float, str, str]], metadata: RecordingMetadata | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header: dict[str, object] = {"version": 2, "width": 100, "height": 30, "timestamp": int(time.time())}
    if metadata is not None:
        header["sandbox_id"] = metadata.sandbox_id
        header["sandbox_name"] = metadata.sandbox_name
        header["sandbox_backend"] = metadata.sandbox_backend
        header["started_at"] = metadata.started_at
        header["ended_at"] = metadata.ended_at
        header["duration_seconds"] = metadata.duration_seconds
        header["exit_codes"] = metadata.exit_codes
    lines = [json.dumps(header)]
    for elapsed, stream, text in events:
        lines.append(json.dumps([elapsed, stream, text]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_TITLE_BAR_HEIGHT = 38


_KEYBOARD_HEIGHT = 130


def build_terminal_frames(events: list[tuple[float, str, str]], options: RecordingOptions) -> list[tuple[StyledScreen, str | None]]:
    font = _load_font(options.font_size)
    char_w = max(1, int(font.getlength("M")))
    ascent, descent = font.getmetrics()
    line_height = max(1, ascent + descent + 2)
    terminal_height = options.height - _KEYBOARD_HEIGHT - _TITLE_BAR_HEIGHT
    pad_x, pad_y = 20, 14
    cols = max(20, (options.width - pad_x * 2) // char_w)
    rows = max(5, (terminal_height - pad_y * 2) // line_height)
    state = TerminalScreen(cols=cols, rows=rows)
    frames: list[tuple[StyledScreen, str | None]] = [(state.render_styled(cursor=True), None)]
    for i, (_, stream, text) in enumerate(events):
        # strip ANSI only for keyboard input (keystrokes have no color intent)
        cleaned = strip_ansi(text) if stream == "k" else text
        if stream == "k":
            for char in cleaned:
                key = _key_label(char)
                state.write(char)
                frames.extend([(state.render_styled(cursor=True), key)] * _key_hold_frames(key))
        elif stream == "p":
            chunk_size = max(1, len(cleaned) // 4)
            for chunk in chunks(cleaned, chunk_size):
                state.write(chunk)
                frames.append((state.render_styled(cursor=True), None))
        else:
            for chunk in chunks(cleaned, 12):
                state.write(chunk)
                frames.append((state.render_styled(cursor=True), None))
            next_stream = events[i + 1][1] if i + 1 < len(events) else None
            if next_stream in ("p", "k"):
                frames.extend([(frames[-1][0], None)] * 6)
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
    return 2 if key in {"ENTER", "BACKSPACE", "TAB", "SPACE"} else 1


def _key_width(key: str) -> int:
    if key == "BACKSPACE":
        return 62
    if key == "ENTER":
        return 50
    if key == "SPACE":
        return 94
    return 27


# ---------------------------------------------------------------------------
# Terminal screen model
# ---------------------------------------------------------------------------


class TerminalScreen:
    def __init__(self, *, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.lines: list[StyledLine] = [[]]
        self._color: Color = _DEFAULT_FG

    def write(self, text: str) -> None:
        i = 0
        while i < len(text):
            ch = text[i]
            # ANSI escape: ESC [
            if ch == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
                j = i + 2
                while j < len(text) and text[j] not in "ABCDEFGHJKSTfmnsulh":
                    j += 1
                if j < len(text) and text[j] == "m":
                    for part in text[i + 2 : j].split(";"):
                        n = int(part) if part else 0
                        self._color = _ANSI_COLORS.get(n, _DEFAULT_FG) if n != 0 else _DEFAULT_FG
                i = j + 1
                continue
            if ch == "\r":
                i += 1
                continue
            if ch == "\n":
                self.lines.append([])
                i += 1
                continue
            if ch in {"\b", "\x7f"}:
                if self.lines[-1]:
                    self.lines[-1].pop()
                i += 1
                continue
            cells: list[tuple[str, Color]] = [(" ", self._color)] * 4 if ch == "\t" else ([(ch, self._color)] if ch.isprintable() else [])
            self.lines[-1].extend(cells)
            while len(self.lines[-1]) > self.cols:
                overflow = self.lines[-1][self.cols :]
                self.lines[-1] = self.lines[-1][: self.cols]
                self.lines.append(overflow)
            if len(self.lines) > self.rows:
                self.lines = self.lines[-self.rows :]
            i += 1

    def render_styled(self, *, cursor: bool = False) -> StyledScreen:
        visible: list[StyledLine] = [list(line) for line in self.lines[-self.rows :]]
        if cursor:
            if not visible:
                visible = [[]]
            visible[-1] = [*visible[-1], ("_", _DEFAULT_FG)]
        return visible

    def render(self, *, cursor: bool = False) -> str:
        return "\n".join("".join(ch for ch, _ in line) for line in self.render_styled(cursor=cursor))


# ---------------------------------------------------------------------------
# Image renderer (Pillow)
# ---------------------------------------------------------------------------

_MONO_FONT_PATHS = ["/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf"]


def _load_font(size: int) -> PILFont.FreeTypeFont:
    for path in _MONO_FONT_PATHS:
        try:
            return PILFont.truetype(path, size)
        except OSError:
            continue
    raise RuntimeError(f"No monospace font found; tried: {_MONO_FONT_PATHS}")


def write_frame(
    content: str | StyledScreen,
    path: Path,
    *,
    width: int,
    height: int,
    font_size: int,
    keyboard_key: str | None = None,
    show_keyboard: bool = False,
    metadata: RecordingMetadata | None = None,
) -> None:
    font = _load_font(font_size)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + 2
    char_w = int(font.getlength("M"))

    keyboard_height = _KEYBOARD_HEIGHT if show_keyboard else 0
    terminal_top = _TITLE_BAR_HEIGHT
    terminal_height = height - keyboard_height - terminal_top
    pad_x, pad_y = 20, 14
    max_cols = max(1, (width - pad_x * 2) // max(1, char_w))
    max_lines = max(1, (terminal_height - pad_y * 2) // max(1, line_height))

    # Normalise to StyledScreen
    styled_lines: StyledScreen
    if isinstance(content, str):
        styled_lines = [[(ch, _DEFAULT_FG) for ch in raw[:max_cols]] for raw in content.splitlines()[:max_lines]]
    else:
        styled_lines = [line[:max_cols] for line in content[:max_lines]]

    img = Image.new("RGB", (width, height), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    # Title bar
    draw.rectangle([0, 0, width - 1, _TITLE_BAR_HEIGHT - 1], fill=(50, 50, 50))
    draw.line([0, _TITLE_BAR_HEIGHT - 1, width - 1, _TITLE_BAR_HEIGHT - 1], fill=(28, 28, 28), width=1)

    # Traffic lights
    cy = _TITLE_BAR_HEIGHT // 2
    r = 7
    for cx, color in [(18, (255, 95, 86)), (38, (255, 189, 46)), (58, (39, 201, 63))]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    title_font = _load_font(max(10, font_size - 6))
    ta, td = title_font.getmetrics()
    title_y = (_TITLE_BAR_HEIGHT - ta - td) // 2

    # Window title (center)
    title = "Terminal"
    title_w = int(title_font.getlength(title))
    draw.text(((width - title_w) // 2, title_y), title, font=title_font, fill=(160, 160, 160))

    # Terminal text (styled)
    for row, line in enumerate(styled_lines):
        y = terminal_top + pad_y + row * line_height
        x = pad_x
        for ch, color in line:
            draw.text((x, y), ch, font=font, fill=color)
            x += char_w

    if show_keyboard:
        _draw_keyboard(draw, width, height, keyboard_key, font_size)

    if metadata is not None:
        _draw_metadata_panel(draw, width, metadata, font_size)

    img.save(str(path))


def _draw_metadata_panel(draw: ImageDraw.ImageDraw, width: int, metadata: RecordingMetadata, font_size: int) -> None:
    font = _load_font(max(10, font_size - 3))
    fa, fd = font.getmetrics()
    line_h = fa + fd + 4

    ts = metadata.started_at[:19].replace("T", "  ")
    rows = [
        ("sandbox", metadata.sandbox_name),
        ("backend", metadata.sandbox_backend),
        ("id", metadata.sandbox_id),
        ("started", ts),
    ]
    if metadata.image is not None:
        rows.insert(2, ("image", metadata.image))
    if metadata.platform is not None:
        rows.append(("platform", metadata.platform))

    pad = 12
    label_col_w = max(int(font.getlength(label)) for label, _ in rows)
    value_col_w = max(int(font.getlength(value)) for _, value in rows)
    gap = 10
    panel_w = pad * 2 + label_col_w + gap + value_col_w
    panel_h = pad * 2 + line_h * len(rows) - 4

    panel_x = width - panel_w - 16
    panel_y = _TITLE_BAR_HEIGHT + 16

    draw.rectangle([panel_x, panel_y, panel_x + panel_w, panel_y + panel_h], fill=(40, 40, 40), outline=(65, 65, 65))

    for i, (label, value) in enumerate(rows):
        y = panel_y + pad + i * line_h
        draw.text((panel_x + pad, y), label, font=font, fill=(120, 120, 120))
        draw.text((panel_x + pad + label_col_w + gap, y), value, font=font, fill=(210, 210, 210))


def _draw_keyboard(draw: ImageDraw.ImageDraw, width: int, height: int, active_key: str | None, font_size: int) -> None:
    rows = [
        ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
        ["A", "S", "D", "F", "G", "H", "J", "K", "L", "ENTER"],
        ["Z", "X", "C", "V", "B", "N", "M", "SPACE", "BACKSPACE"],
    ]
    key_h = 24
    key_gap = 4
    pad = 10

    panel_w = max(sum(_key_width(k) for k in row) + key_gap * (len(row) - 1) for row in rows) + pad * 2
    panel_h = len(rows) * (key_h + key_gap) - key_gap + pad * 2
    panel_x = width - panel_w - 16
    panel_y = height - panel_h - 16

    panel_bg = (40, 40, 40)
    panel_border = (65, 65, 65)
    normal = (58, 58, 58)
    key_border = (78, 78, 78)
    active_color = (250, 204, 21)
    text_color = (195, 195, 195)
    active_text = (20, 20, 20)

    label_font = _load_font(max(7, font_size - 7))

    draw.rectangle([panel_x, panel_y, panel_x + panel_w, panel_y + panel_h], fill=panel_bg, outline=panel_border)

    y = panel_y + pad
    for row in rows:
        row_w = sum(_key_width(k) for k in row) + key_gap * (len(row) - 1)
        x = panel_x + (panel_w - row_w) // 2
        for key in row:
            key_w = _key_width(key)
            is_active = active_key == key or (active_key is not None and len(active_key) == 1 and key == active_key.upper())
            bg = active_color if is_active else normal
            fg = active_text if is_active else text_color
            draw.rectangle([x, y, x + key_w - 1, y + key_h - 1], fill=bg, outline=key_border)
            label_w = int(label_font.getlength(key))
            lh = sum(label_font.getmetrics())
            draw.text((x + (key_w - label_w) // 2, y + (key_h - lh) // 2), key, font=label_font, fill=fg)
            x += key_w + key_gap
        y += key_h + key_gap
