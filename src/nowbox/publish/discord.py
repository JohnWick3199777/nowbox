from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path

_DISCORD_MAX_BYTES = 25 * 1024 * 1024  # 25 MB hard limit
_DISCORD_COLOR = 0x5865F2  # blurple


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
        if message:
            payload["content"] = message
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url
        if meta:
            payload["embeds"] = [_build_embed(meta, path)]

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
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    if sidecar.exists():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _build_embed(meta: dict, path: Path) -> dict:
    fields = []

    if name := meta.get("sandbox_name"):
        fields.append({"name": "Sandbox", "value": f"`{name}`", "inline": True})
    if sid := meta.get("sandbox_id"):
        fields.append({"name": "ID", "value": f"`{sid}`", "inline": True})
    if backend := meta.get("sandbox_backend"):
        fields.append({"name": "Backend", "value": backend, "inline": True})
    if image := meta.get("image"):
        fields.append({"name": "Image", "value": f"`{image}`", "inline": False})
    if (dur := meta.get("duration_seconds")) is not None:
        fields.append({"name": "Duration", "value": f"{dur:.1f}s", "inline": True})
    if codes := meta.get("exit_codes"):
        indicators = " ".join("✅" if c == 0 else f"❌ ({c})" for c in codes)
        fields.append({"name": "Exit codes", "value": indicators, "inline": True})
    if platform := meta.get("platform"):
        fields.append({"name": "Platform", "value": platform, "inline": True})

    embed: dict = {
        "title": path.name,
        "color": _DISCORD_COLOR,
        "fields": fields,
    }
    if started := meta.get("started_at"):
        embed["timestamp"] = started[:26].rstrip("Z") + "+00:00" if not started.endswith("+00:00") else started
    return embed


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
