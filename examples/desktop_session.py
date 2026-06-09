"""Example: DesktopMachineSandbox - VNC-backed terminal recording.

Build the container image first:
    container build -f container/Dockerfile.desktop.machine -t nowbox-desktop-machine:latest container/

Useful checks:
    container system status
    container image list
    container machine list

If startup reports "The disk image format is not recognized", rebuild or delete
the stale nowbox-desktop image and build it again.

On Apple Silicon, install Rosetta if the Apple Container builder reports
"Rosetta is not installed":
    softwareupdate --install-rosetta --agree-to-license

Then run:
    uv run python examples/desktop_session.py
"""

from datetime import datetime
from pathlib import Path

from nowbox import DesktopMachineSandbox

OUTPUT = Path("artifacts") / f"desktop-{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"

with DesktopMachineSandbox("nowbox-desktop-machine:latest") as sb:
    # Visual terminal recording via VNC
    t = sb.terminal
    t.start_recording(OUTPUT)

    commands = [
        "whoami",
        "pwd",
        "uname -a",
        "head -5 /etc/os-release",
        "echo hello from xterm",
    ]
    for command in commands:
        result = t.type(command).key("enter")
        print(f"terminal {command!r} stdout: {result.stdout.strip()!r}")
        print(f"terminal {command!r} stderr: {result.stderr.strip()!r}")

    path = t.stop_recording()
    print(f"Recording saved: {path}")
