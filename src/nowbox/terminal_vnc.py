"""VNCTerminal — visual terminal backed by a VNC-connected xterm container."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from nowbox.recording import write_meta
from nowbox.rfb import RFBClient
from nowbox.types import RecordingMetadata, SandboxResult
from nowbox.utils import normalize_command, strip_ansi

if TYPE_CHECKING:
    from nowbox.sandbox.desktop import DesktopSandbox

_SENTINEL_RE = re.compile(r"(\d+)\s+(\d+)\s+(\S+)")


class VNCTerminal:
    """Interactive terminal that drives an xterm via VNC.

    Sends keystrokes via RFB KeyEvent messages; captures screenshots in a
    background thread to assemble MP4 recordings.  Command completion is
    detected via a sentinel file written by ZSH's ``precmd`` hook inside
    the container.

    Public API mirrors ``SandboxTerminal`` for compatibility.
    """

    def __init__(self, sandbox: DesktopSandbox, host: str = "127.0.0.1", port: int = 5900, *, type_delay: float = 0.05) -> None:
        self._sandbox = sandbox
        self._host = host
        self._port = port
        self._rfb: RFBClient | None = None
        # Seconds between keystrokes when typing — creates visible animation in recordings.
        self._type_delay = type_delay

        # Sentinel tracking
        self._last_seq: int = 0
        self._current_cwd: Path | None = None

        # Pending input buffer
        self._pending_text: str = ""
        self._pending_command: list[str] | str | None = None
        self._line_started: bool = False

        # Recording state
        self._recording_path: Path | None = None
        self._recording_started_at: float | None = None
        self._recording_started_at_iso: str | None = None
        self._frames: list[tuple[float, bytes]] = []  # (elapsed, PNG bytes)
        self._exit_codes: list[int] = []

        # Background capture thread
        self._capture_thread: threading.Thread | None = None
        self._stop_capture = threading.Event()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._rfb is not None:
            return
        deadline = time.monotonic() + 15
        last_err: Exception = RuntimeError("VNC not ready")
        while time.monotonic() < deadline:
            try:
                rfb = RFBClient(self._host, self._port)
                rfb.connect()
                self._rfb = rfb
                # Wait for ZSH precmd to fire at least once (initial prompt ready)
                self._wait_sentinel(timeout=15)
                return
            except (ConnectionError, OSError) as exc:
                last_err = exc
                time.sleep(0.5)
        raise TimeoutError(f"Could not connect to VNC terminal after 15s: {last_err}")

    def close(self) -> None:
        """Stop recording (if active) and close the VNC connection."""
        if self.is_recording:
            self.stop_recording()
        if self._rfb is not None:
            self._rfb.close()
            self._rfb = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording_path is not None

    def start_recording(self, path: str | Path | None = None) -> Path:
        self._ensure_connected()
        self._recording_path = Path(path) if path is not None else self._sandbox.root / "artifacts" / "terminal.mp4"
        self._recording_started_at = time.monotonic()
        self._recording_started_at_iso = datetime.now(UTC).isoformat()
        self._frames.clear()
        self._exit_codes.clear()
        self._stop_capture.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        return self._recording_path

    # alias
    def start_record(self, path: str | Path | None = None) -> Path:
        return self.start_recording(path)

    def stop_recording(self) -> Path | None:
        path = self._recording_path
        if path is None:
            return None

        # Stop the capture thread and wait for it to flush the last frame
        self._stop_capture.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None

        ended_at = datetime.now(UTC).isoformat()
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

        if self._frames:
            self._assemble_mp4(path, self._frames, duration)

        write_meta(path, metadata)

        self._recording_path = None
        self._recording_started_at = None
        self._recording_started_at_iso = None
        return path

    # alias
    def stop_record(self) -> Path | None:
        return self.stop_recording()

    # ------------------------------------------------------------------
    # Input API
    # ------------------------------------------------------------------

    def run(self, command, *, cwd=None, env=None, check: bool = False) -> SandboxResult:
        normalized = normalize_command(command)
        return self._type_command(normalized).enter(cwd=cwd, env=env, check=check)

    def type(self, text: str) -> VNCTerminal:
        self._ensure_connected()
        self._ensure_prompt()
        self._pending_text += text
        self._pending_command = self._pending_text
        return self

    def paste(self, text: str) -> VNCTerminal:
        return self.type(text)

    def key(self, key: str, *, cwd=None, env=None, check: bool = False) -> VNCTerminal | SandboxResult:
        lower = key.lower()
        if lower in {"enter", "return"}:
            return self.enter(cwd=cwd, env=env, check=check)
        if lower in {"backspace", "delete"}:
            if self._pending_text:
                self._pending_text = self._pending_text[:-1]
                self._pending_command = self._pending_text
                self._ensure_connected()
                self._sandbox._desktop_tmux_send_keys(["BSpace"])
            return self
        if lower == "tab":
            self._ensure_connected()
            if self._pending_text:
                self._tmux_send(self._pending_text)
                self._pending_text = ""
            self._sandbox._desktop_tmux_send_keys(["Tab"])
            time.sleep(0.3)
            return self
        # Map common names to tmux key names
        _TMUX_KEYS = {
            "escape": "Escape",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "ctrl+c": "C-c",
            "ctrl+d": "C-d",
            "ctrl+z": "C-z",
            "f1": "F1",
            "f2": "F2",
            "f3": "F3",
            "f4": "F4",
            "f5": "F5",
            "f6": "F6",
            "f7": "F7",
            "f8": "F8",
            "f9": "F9",
            "f10": "F10",
            "q": "q",
        }
        if lower in _TMUX_KEYS:
            self._ensure_connected()
            if self._pending_text:
                self._tmux_send(self._pending_text)
                self._pending_text = ""
            self._sandbox._desktop_tmux_send_keys([_TMUX_KEYS[lower]])
            return self
        if len(key) == 1:
            return self.type(key)
        raise ValueError(f"unknown key: {key!r}")

    def enter(self, *, cwd=None, env=None, check: bool = False) -> SandboxResult:
        command = self._pending_command if self._pending_command is not None else self._pending_text
        self._line_started = False
        self._pending_text = ""
        self._pending_command = None

        self._ensure_connected()
        assert self._rfb is not None
        started = time.monotonic()

        from nowbox.utils import command_text as _cmd_text

        cmd_str = (_cmd_text(command) if not isinstance(command, str) else command).strip()

        if cwd is not None and self._current_cwd != Path(cwd):
            import shlex as _shlex

            self._tmux_send(f"cd {_shlex.quote(str(cwd))}", enter=True)
            self._wait_sentinel(timeout=8)

        if cmd_str:
            if env:
                import shlex

                prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
                cmd_str = f"{prefix} {cmd_str}"
            # Capture pane output while typing the user's original command into
            # the visible terminal. This avoids leaking capture wrappers into
            # recordings.
            self._sandbox._desktop_tmux_command(["pipe-pane", "-o", "-t", self._TMUX_SESSION, "cat > /tmp/.nowbox_stdout"])
            self._tmux_send(cmd_str, enter=True)
        else:
            self._tmux_send("", enter=True)

        seq, exit_code, new_cwd = self._wait_sentinel(timeout=30)
        self._sandbox._desktop_tmux_command(["pipe-pane", "-t", self._TMUX_SESSION])
        # Let the capture thread grab the settled output before the caller sends the next command.
        if self.is_recording:
            time.sleep(0.3)
        duration = time.monotonic() - started

        if new_cwd:
            self._current_cwd = Path(new_cwd)

        # Read captured stdout from the tee file — no second execution needed.
        stdout = self._sandbox._container_read_file("/tmp/.nowbox_stdout") if cmd_str else ""
        stderr = ""

        if self._recording_path is not None:
            self._exit_codes.append(exit_code)

        result = SandboxResult(
            sandbox_id=self._sandbox.id,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            cwd=self._current_cwd or self._sandbox.root,
            recording_path=self._recording_path,
            output=strip_ansi(stdout).strip(),
        )
        if check and not result.ok:
            raise subprocess.CalledProcessError(result.exit_code, result.command, output=result.stdout)
        return result

    # ------------------------------------------------------------------
    # tmux input injection (PTY-direct, no X11 focus required)
    # ------------------------------------------------------------------

    _TMUX_SESSION = "nowbox"

    def _tmux_send(self, text: str, *, enter: bool = False) -> None:
        """Inject text (and optionally Enter) into the tmux session's PTY."""
        if not text and not enter:
            return
        # tmux send-keys with a type delay simulates natural typing in the recording.
        # When recording, send one character at a time with a sleep between each.
        if self.is_recording and self._type_delay > 0 and text:
            for ch in text:
                self._sandbox._desktop_tmux_send_keys([ch])
                time.sleep(self._type_delay)
        elif text:
            self._sandbox._desktop_tmux_send_keys([text])
        if enter:
            self._sandbox._desktop_tmux_send_keys(["Enter"])

    # ------------------------------------------------------------------
    # Sentinel polling
    # ------------------------------------------------------------------

    def _wait_sentinel(self, *, timeout: float) -> tuple[int, int, str]:
        """Poll /tmp/.nowbox_sentinel until seq increments past _last_seq.

        Returns (seq, exit_code, cwd).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self._sandbox._container_read_file("/tmp/.nowbox_sentinel")
            m = _SENTINEL_RE.search(raw.strip())
            if m:
                seq = int(m.group(1))
                if seq > self._last_seq:
                    self._last_seq = seq
                    return seq, int(m.group(2)), m.group(3)
            time.sleep(0.05)
        return self._last_seq, 0, str(self._current_cwd or self._sandbox.root)

    # ------------------------------------------------------------------
    # Background screenshot capture
    # ------------------------------------------------------------------

    def _screenshot_bytes(self) -> bytes | None:
        """Capture the current Xvfb display via scrot inside the container."""
        return self._sandbox._desktop_screenshot_bytes()

    def _capture_loop(self) -> None:
        prev_hash: int | None = None
        while not self._stop_capture.is_set():
            png_bytes = self._screenshot_bytes()
            if png_bytes is None:
                time.sleep(0.05)
                continue
            h = hash(png_bytes)
            if h != prev_hash:
                assert self._recording_started_at is not None
                elapsed = time.monotonic() - self._recording_started_at
                self._frames.append((elapsed, png_bytes))
                prev_hash = h
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # MP4 assembly
    # ------------------------------------------------------------------

    def _assemble_mp4(self, path: Path, frames: list[tuple[float, bytes]], total_duration: float) -> None:
        """Write frames to a temp dir and assemble into MP4 via ffmpeg."""
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found on PATH — required for MP4 assembly")

        path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            concat_lines: list[str] = []

            for i, (ts, png_bytes) in enumerate(frames):
                frame_path = tmp / f"frame_{i:06d}.png"
                frame_path.write_bytes(png_bytes)

                if i + 1 < len(frames):
                    duration = frames[i + 1][0] - ts
                else:
                    duration = max(total_duration - ts, 0.05)
                duration = max(duration, 0.05)

                concat_lines.append(f"file '{frame_path}'\n")
                concat_lines.append(f"duration {duration:.4f}\n")

            concat_path = tmp / "concat.txt"
            concat_path.write_text("".join(concat_lines))

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_path),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(path),
                ],
                check=True,
                capture_output=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _type_command(self, command: list[str] | str) -> VNCTerminal:
        from nowbox.utils import command_text

        self._pending_command = command
        self._ensure_prompt()
        self._pending_text = command_text(command)
        return self

    def _ensure_prompt(self) -> None:
        if not self._line_started:
            self._line_started = True
