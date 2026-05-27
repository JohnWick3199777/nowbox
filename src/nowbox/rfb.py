"""Minimal RFB 3.3 client — keyboard input and framebuffer screenshots only."""
from __future__ import annotations

import socket
import struct

from PIL import Image

# X11 keysyms for special keys
_KEYSYM: dict[str, int] = {
    "return": 0xFF0D,
    "enter": 0xFF0D,
    "backspace": 0xFF08,
    "tab": 0xFF09,
    "escape": 0xFF1B,
    "up": 0xFF52,
    "down": 0xFF54,
    "left": 0xFF51,
    "right": 0xFF53,
    "ctrl": 0xFFE3,
    "alt": 0xFFE9,
    "shift": 0xFFE1,
    "delete": 0xFFFF,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "f1": 0xFFBE,
    "f2": 0xFFBF,
    "f3": 0xFFC0,
    "f4": 0xFFC1,
    "f5": 0xFFC2,
    "f6": 0xFFC3,
    "f7": 0xFFC4,
    "f8": 0xFFC5,
    "f9": 0xFFC6,
    "f10": 0xFFC7,
    "f11": 0xFFC8,
    "f12": 0xFFC9,
    "q": ord("q"),
}


class RFBClient:
    """Minimal RFB 3.3 client that supports keyboard events and framebuffer screenshots."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5900) -> None:
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._width = 0
        self._height = 0
        self._pixel_format: bytes = b""

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._sock = socket.create_connection((self._host, self._port), timeout=5)
        self._sock.settimeout(10)
        self._handshake()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> RFBClient:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def key_down(self, keysym: int) -> None:
        self._send(struct.pack(">BBxxI", 4, 1, keysym))

    def key_up(self, keysym: int) -> None:
        self._send(struct.pack(">BBxxI", 4, 0, keysym))

    def key_press(self, keysym: int) -> None:
        self.key_down(keysym)
        self.key_up(keysym)

    def type_text(self, text: str) -> None:
        """Send each character as a key press. Handles printable ASCII and special keys."""
        for ch in text:
            if ch == "\n":
                self.key_press(_KEYSYM["return"])
            elif ch == "\t":
                self.key_press(_KEYSYM["tab"])
            elif ch == "\b":
                self.key_press(_KEYSYM["backspace"])
            elif ch == "\x1b":
                self.key_press(_KEYSYM["escape"])
            else:
                cp = ord(ch)
                # Latin-1 range maps directly to X11 keysym
                if 0x20 <= cp <= 0xFF:
                    self.key_press(cp)

    def key_by_name(self, name: str) -> None:
        """Press a named key (case-insensitive). See _KEYSYM for supported names."""
        lower = name.lower()
        if lower in _KEYSYM:
            self.key_press(_KEYSYM[lower])
        elif len(name) == 1:
            self.type_text(name)
        else:
            raise ValueError(f"unknown key name: {name!r}")

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def screenshot(self) -> Image.Image:
        """Request a full framebuffer update and return it as a PIL Image."""
        # Request full non-incremental update
        self._send(struct.pack(">BbHHHH", 3, 0, 0, 0, self._width, self._height))
        return self._read_framebuffer_update()

    # ------------------------------------------------------------------
    # Handshake (RFB 3.3)
    # ------------------------------------------------------------------

    def _handshake(self) -> None:
        # Server version
        self._recv(12)  # server version string
        self._send(b"RFB 003.003\n")

        # Security type (4 bytes): 1 = None, 2 = VNC auth
        (sec_type,) = struct.unpack(">I", self._recv(4))
        if sec_type == 2:
            # VNC auth challenge — we don't support password auth
            raise RuntimeError("VNC server requires password authentication; start x11vnc with -nopw")
        if sec_type != 1:
            raise RuntimeError(f"unsupported RFB security type: {sec_type}")
        # sec_type == 1 (None): no further auth exchange needed

        # ClientInit: shared=1 (allow multiple clients)
        self._send(struct.pack(">B", 1))

        # ServerInit: width(2) height(2) pixel-format(16) name-length(4) name(...)
        header = self._recv(24)
        self._width, self._height = struct.unpack(">HH", header[:4])
        self._pixel_format = header[4:20]
        (name_len,) = struct.unpack(">I", header[20:24])
        self._recv(name_len)  # server name (ignored)

        # Request raw encoding only
        # SetEncodings: type(1) pad(1) count(2) encodings(4*n)
        self._send(struct.pack(">BBH", 2, 0, 1) + struct.pack(">i", 0))  # encoding 0 = Raw

    # ------------------------------------------------------------------
    # Framebuffer update parsing
    # ------------------------------------------------------------------

    def _read_framebuffer_update(self) -> Image.Image:
        # Discard any server messages until we hit a FramebufferUpdate (type 0)
        while True:
            (msg_type,) = struct.unpack(">B", self._recv(1))
            if msg_type == 0:
                break
            # Skip other message types (Bell=2, ServerCutText=3, SetColourMapEntries=1)
            if msg_type == 2:
                continue
            if msg_type == 3:
                self._recv(7)  # pad + x + y + w + h
                continue
            if msg_type == 1:
                pad = self._recv(5)
                (n_colors,) = struct.unpack(">H", pad[3:5])
                self._recv(n_colors * 6)
                continue
            raise RuntimeError(f"unexpected RFB server message type: {msg_type}")

        # FramebufferUpdate: pad(1) num_rects(2)
        pad_and_count = self._recv(3)
        (num_rects,) = struct.unpack(">H", pad_and_count[1:3])

        # Parse pixel format from ServerInit
        (bits_per_pixel,) = struct.unpack(">B", self._pixel_format[0:1])
        bytes_per_pixel = bits_per_pixel // 8

        # Build a blank image; paint rectangles into it
        img = Image.new("RGB", (self._width, self._height))

        for _ in range(num_rects):
            rect_header = self._recv(12)
            x, y, w, h, encoding = struct.unpack(">HHHHi", rect_header)
            if encoding != 0:
                raise RuntimeError(f"unexpected RFB encoding: {encoding}")
            data = self._recv(w * h * bytes_per_pixel)
            rect_img = self._decode_raw(data, w, h, bytes_per_pixel)
            img.paste(rect_img, (x, y))

        return img

    def _decode_raw(self, data: bytes, w: int, h: int, bpp: int) -> Image.Image:
        """Convert raw RFB pixel data to a PIL Image using the server's pixel format."""
        # Pixel format bytes: bits_per_pixel(1) depth(1) big_endian(1) true_colour(1)
        #   red_max(2) green_max(2) blue_max(2) red_shift(1) green_shift(1) blue_shift(1)
        pf = self._pixel_format
        big_endian = pf[2]
        red_max, green_max, blue_max = struct.unpack(">HHH", pf[4:10])
        red_shift, green_shift, blue_shift = pf[10], pf[11], pf[12]

        endian = ">" if big_endian else "<"
        if bpp == 4:
            fmt = f"{endian}{w * h}I"
        elif bpp == 2:
            fmt = f"{endian}{w * h}H"
        elif bpp == 1:
            fmt = f"{endian}{w * h}B"
        else:
            raise RuntimeError(f"unsupported bytes per pixel: {bpp}")

        pixels = struct.unpack(fmt, data)
        rgb_bytes = bytearray(w * h * 3)
        for i, px in enumerate(pixels):
            r = ((px >> red_shift) & red_max) * 255 // max(red_max, 1)
            g = ((px >> green_shift) & green_max) * 255 // max(green_max, 1)
            b = ((px >> blue_shift) & blue_max) * 255 // max(blue_max, 1)
            rgb_bytes[i * 3] = r
            rgb_bytes[i * 3 + 1] = g
            rgb_bytes[i * 3 + 2] = b

        return Image.frombytes("RGB", (w, h), bytes(rgb_bytes))

    # ------------------------------------------------------------------
    # Socket helpers
    # ------------------------------------------------------------------

    def _send(self, data: bytes) -> None:
        assert self._sock is not None
        self._sock.sendall(data)

    def _recv(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("VNC server closed connection")
            buf.extend(chunk)
        return bytes(buf)
