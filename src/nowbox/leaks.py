"""Leak detection and cleanup for Apple Container sandboxes."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

_NOWBOX_LABEL = "nowbox.managed"
_MACOS_EPOCH_OFFSET = 978307200  # seconds between Unix epoch and macOS absolute time (2001-01-01)


@dataclass(frozen=True)
class LeakedContainer:
    id: str
    image: str
    status: str
    started_at: datetime | None

    def __str__(self) -> str:
        started = self.started_at.strftime("%Y-%m-%d %H:%M:%S UTC") if self.started_at else "unknown"
        return f"{self.id}  [{self.status}]  image={self.image}  started={started}"


def list_leaked_containers() -> list[LeakedContainer]:
    """Return all containers tagged with the nowbox label (i.e. managed by nowbox)."""
    result = subprocess.run(["container", "list", "--all", "--format", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    leaked: list[LeakedContainer] = []
    for entry in data:
        cfg = entry.get("configuration", {})
        labels = cfg.get("labels", {})
        if labels.get(_NOWBOX_LABEL) != "true":
            continue
        container_id = cfg.get("id", "unknown")
        image = cfg.get("image", {}).get("reference", "unknown")
        status = entry.get("status", "unknown")
        started_raw = entry.get("startedDate")
        started_at: datetime | None = None
        if started_raw is not None:
            try:
                unix_ts = float(started_raw) + _MACOS_EPOCH_OFFSET
                started_at = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
            except (ValueError, OSError):
                pass
        leaked.append(LeakedContainer(id=container_id, image=image, status=status, started_at=started_at))
    return leaked


def cleanup_leaked_containers(*, dry_run: bool = False) -> list[str]:
    """Stop and delete all leaked nowbox containers. Returns list of cleaned-up container IDs."""
    leaked = list_leaked_containers()
    cleaned: list[str] = []
    for container in leaked:
        if dry_run:
            cleaned.append(container.id)
            continue
        subprocess.run(["container", "stop", container.id], capture_output=True)
        subprocess.run(["container", "delete", container.id], capture_output=True)
        cleaned.append(container.id)
    return cleaned
