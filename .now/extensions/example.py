import json
import subprocess
from pathlib import Path

from now_sdk import Context, command


@command("example")
def example(ctx: Context) -> None:
    launch_dir = ctx.project_root / ".vscode"
    launch_dir.mkdir(exist_ok=True)

    launch = {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Run Example",
                "type": "debugpy",
                "request": "launch",
                "program": str(ctx.project_root / "examples" / "example.py"),
                "console": "integratedTerminal",
                "stopOnEntry": True,
            }
        ],
    }
    launch_path = launch_dir / "launch.json"
    launch_path.write_text(json.dumps(launch, indent=4))

    subprocess.run(
        [
            "code",
            "--reuse-window",
            "--goto",
            str(ctx.project_root / "examples" / "example.py"),
            str(ctx.project_root),
        ]
    )
    # Requires Accessibility permission for the terminal in
    # System Settings → Privacy & Security → Accessibility
    script = """
        tell application "System Events"
            tell process "Code"
                set frontmost to true
                delay 2
                key code 96
            end tell
        end tell
    """
    subprocess.run(["osascript", "-e", script])
