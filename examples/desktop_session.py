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
    # Programmatic execution - uses container machine run, no VNC
    whoami = sb.run(["whoami"])
    echo = sb.run(["echo", "hello from machine"])
    print(f"whoami stdout: {whoami.stdout.strip()!r}")
    print(f"echo stdout: {echo.stdout.strip()!r}")
    assert whoami.ok
    assert echo.ok

    # Visual terminal recording via VNC
    t = sb.terminal
    t.start_recording(OUTPUT)

    t.type("whoami").key("enter")
    t.type("echo 'hello from xterm'").key("enter")

    path = t.stop_recording()
    print(f"Recording saved: {path}")
