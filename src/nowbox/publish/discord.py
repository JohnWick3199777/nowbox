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

        payload: dict[str, str] = {}
        if message:
            payload["content"] = message
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
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Discord webhook failed ({exc.code}): {detail}") from exc


def _build_multipart(path: Path, payload: dict[str, str], mime: str, boundary: str) -> bytes:
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
