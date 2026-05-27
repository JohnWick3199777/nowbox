"""Example: DesktopSandbox — VNC-backed terminal recording.

Build the container image first:
    container build -f container/Dockerfile.desktop -t nowbox-desktop:latest container/

Then run:
    uv run python examples/desktop_session.py
"""
from pathlib import Path

from nowbox import DesktopSandbox

OUTPUT = Path("artifacts/desktop.mp4")

with DesktopSandbox("nowbox-desktop:latest", name="desktop-demo") as sb:
    # Programmatic execution — uses container exec, no VNC
    result = sb.run(["zsh", "-c", "echo hello from container"])
    print(f"run() stdout: {result.stdout.strip()!r}")
    assert result.ok

    # Visual terminal recording via VNC
    t = sb.terminal
    t.start_recording(OUTPUT)

    t.type("echo 'hello from xterm'").key("enter")
    t.type("uname -a").key("enter")
    t.type("ls /").key("enter")

    path = t.stop_recording()
    print(f"Recording saved: {path}")
