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


def ansi_chunks(text: str, size: int) -> list[str]:
    """Split text into chunks of ~size *visible* characters without cutting ANSI escape sequences."""
    result: list[str] = []
    current: list[str] = []
    count = 0
    i = 0
    while i < len(text):
        if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and text[j] not in "ABCDEFGHJKSTfmnsulh":
                j += 1
            end = j + 1 if j < len(text) else len(text)
            current.append(text[i:end])
            i = end
        else:
            current.append(text[i])
            count += 1
            i += 1
        if count >= size:
            result.append("".join(current))
            current = []
            count = 0
    if current:
        result.append("".join(current))
    return result or [""]


def shell(command: str) -> list[str]:
    return shlex.split(command)
