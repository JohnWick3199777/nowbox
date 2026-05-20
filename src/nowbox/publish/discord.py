"""Publish terminal recordings to a Discord channel via webhook."""
from __future__ import annotations

import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_MAX_FILE_BYTES = 25 * 1024 * 1024  # Discord's 25 MB limit for webhooks


class DiscordPublisher:
    """Post a recording file (MP4, cast, …) to a Discord webhook.

    Args:
        webhook_url: Discord webhook URL. Falls back to the
            ``DISCORD_WEBHOOK_URL`` environment variable if omitted.
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        if not url:
            raise ValueError(
                "No webhook URL provided. Pass webhook_url= or set DISCORD_WEBHOOK_URL."
            )
        self._url = url

    def publish(
        self,
        path: str | os.PathLike[str],
        *,
        message: str = "",
        username: str = "nowbox",
        avatar_url: str | None = None,
    ) -> None:
        """Upload *path* to the Discord channel.

        Args:
            path: Path to the recording file.
            message: Optional text that appears above the video.
            username: Override display name for the webhook post.
            avatar_url: Optional avatar image URL for the webhook post.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file exceeds Discord's 25 MB limit.
            urllib.error.HTTPError: On a non-2xx response from Discord.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Recording not found: {file_path}")
        file_size = file_path.stat().st_size
        if file_size > _MAX_FILE_BYTES:
            mb = file_size / (1024 * 1024)
            raise ValueError(
                f"File is {mb:.1f} MB — exceeds Discord's 25 MB webhook limit."
            )

        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        boundary = uuid.uuid4().hex
        body = _build_multipart(
            boundary=boundary,
            filename=file_path.name,
            mime=mime,
            data=file_path.read_bytes(),
            message=message,
            username=username,
            avatar_url=avatar_url,
        )
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req) as resp:
            resp.read()


def _build_multipart(
    *,
    boundary: str,
    filename: str,
    mime: str,
    data: bytes,
    message: str,
    username: str,
    avatar_url: str | None,
) -> bytes:
    parts: list[bytes] = []
    sep = f"--{boundary}\r\n".encode()
    end = f"--{boundary}--\r\n".encode()

    # JSON payload (content + username + optional avatar_url)
    import json as _json

    payload: dict[str, object] = {"content": message, "username": username}
    if avatar_url:
        payload["avatar_url"] = avatar_url
    json_bytes = _json.dumps(payload).encode()
    parts.append(
        sep
        + b'Content-Disposition: form-data; name="payload_json"\r\n'
        + b"Content-Type: application/json\r\n\r\n"
        + json_bytes
        + b"\r\n"
    )

    # File part
    parts.append(
        sep
        + f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
        + f"Content-Type: {mime}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )

    parts.append(end)
    return b"".join(parts)
