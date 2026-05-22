from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path

_DISCORD_MAX_BYTES = 25 * 1024 * 1024  # 25 MB hard limit


class DiscordPublisher:
    """Publish MP4 recordings to a Discord channel via webhook."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL") or ""
        if not self.webhook_url:
            raise ValueError(
                "No webhook URL provided. Pass webhook_url= or set DISCORD_WEBHOOK_URL."
            )

    def publish(
        self,
        path: Path | str,
        *,
        message: str = "",
        username: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        size = path.stat().st_size
        if size > _DISCORD_MAX_BYTES:
            raise ValueError(
                f"{path.name} is {size / 1024 / 1024:.1f} MB — exceeds Discord's 25 MB limit."
            )

        meta = _load_meta(path)
        payload: dict = {}
        content_parts = []
        if message:
            content_parts.append(message)
        if meta:
            content_parts.append(_format_meta_text(meta))
        if content_parts:
            payload["content"] = "\n".join(content_parts)
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        boundary = "----NowboxBoundary"
        body = _build_multipart(path, payload, mime, boundary)

        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "nowbox (https://github.com/JohnWick3199777/nowbox)",
            },
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Discord webhook failed ({exc.code}): {detail}") from exc


def _load_meta(path: Path) -> dict | None:
    sidecar = path.with_suffix(".meta.json")
    if sidecar.exists():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _format_meta_text(meta: dict) -> str:
    lines = []
    if name := meta.get("sandbox_name"):
        lines.append(f"**sandbox** `{name}`")
    if sid := meta.get("sandbox_id"):
        lines.append(f"**id** `{sid}`")
    if backend := meta.get("sandbox_backend"):
        lines.append(f"**backend** {backend}")
    if image := meta.get("image"):
        lines.append(f"**image** `{image}`")
    if (dur := meta.get("duration_seconds")) is not None:
        lines.append(f"**duration** {dur:.1f}s")
    if codes := meta.get("exit_codes"):
        indicators = " ".join("✅" if c == 0 else f"❌ `{c}`" for c in codes)
        lines.append(f"**exit codes** {indicators}")
    if started := meta.get("started_at"):
        lines.append(f"**started** {started[:19].replace('T', ' ')} UTC")
    return "\n".join(lines)


def _build_multipart(path: Path, payload: dict, mime: str, boundary: str) -> bytes:
    parts: list[bytes] = []
    sep = f"--{boundary}\r\n".encode()

    if payload:
        parts.append(sep)
        parts.append(b'Content-Disposition: form-data; name="payload_json"\r\n')
        parts.append(b"Content-Type: application/json\r\n\r\n")
        parts.append(json.dumps(payload).encode())
        parts.append(b"\r\n")

    parts.append(sep)
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode())
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())

    return b"".join(parts)
