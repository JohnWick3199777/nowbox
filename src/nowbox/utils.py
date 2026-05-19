from __future__ import annotations

import re
import shlex

from nowbox.types import Command


def normalize_command(command: Command) -> list[str] | str:
    if isinstance(command, str):
        return command
    return [str(part) for part in command]


def command_text(command: list[str] | str) -> str:
    return command if isinstance(command, str) else shlex.join(command)


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def shell(command: str) -> list[str]:
    return shlex.split(command)
