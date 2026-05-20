"""Run a local sandbox and publish the recording to a Discord channel.

Set DISCORD_WEBHOOK_URL in your environment before running:

    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    uv run python examples/publish_discord.py
"""
from nowbox import DiscordPublisher, LocalSandbox

sandbox = LocalSandbox(name="discord-demo")
terminal = sandbox.terminal
terminal.start_record(sandbox.root / "artifacts" / "terminal.mp4")

terminal.paste("python3 --version")
result = terminal.enter()
print(result.output)

terminal.paste("python3 -c \"import platform; print(platform.platform())\"")
result = terminal.enter()
print(result.output)

recording = terminal.stop_record()
print(f"Recording saved to: {recording}")

publisher = DiscordPublisher()  # reads DISCORD_WEBHOOK_URL from env
publisher.publish(recording, message="sandbox run complete")
print("Published to Discord!")
