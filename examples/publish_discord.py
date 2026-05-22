"""
Publish a terminal recording to Discord.

Set DISCORD_WEBHOOK_URL in your environment before running:

    export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
    uv run python examples/publish_discord.py
"""
from __future__ import annotations

from nowbox import AppleContainerSandbox, DiscordPublisher

IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"

with AppleContainerSandbox(IMAGE) as sandbox:
    terminal = sandbox.terminal
    terminal.start_recording()

    terminal.run("echo 'hello from nowbox'")
    terminal.run("uname -a")

    recording = terminal.stop_recording()

publisher = DiscordPublisher()  # reads DISCORD_WEBHOOK_URL from env
publisher.publish(recording, message="Latest sandbox run from nowbox")
print(f"Published {recording} to Discord.")
