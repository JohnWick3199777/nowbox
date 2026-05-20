import json
import subprocess
from pathlib import Path

from now_sdk import Context, argument, command, mode, option


def _example_files() -> list[str]:
    return sorted(p.stem for p in Path("examples").glob("*.py"))


@command("run")
@mode("dev")
@argument("file", default="example", choices=_example_files, help="Example to run")
@option("--debug", is_flag=True, help="Open in VS Code and start the debugger")
@option("--profile", is_flag=True, help="Run with cProfile and print the top 20 calls")
def run(ctx: Context) -> None:
    """Run an example script. Use --debug to launch in VS Code, --profile to profile."""
    debug = ctx.args["debug"]
    profile = ctx.args["profile"]
    script = ctx.project_root / "examples" / f"{ctx.args['file']}.py"

    if debug:
        ctx.log.info(f"opening {script} in VS Code debugger")
        _launch_vscode_debug(ctx, script)
    elif profile:
        ctx.log.info(f"profiling {script}")
        ctx.run(f"uv run -m cProfile -s cumulative {script} 2>&1 | head -30", exit=False)
    else:
        ctx.log.info(f"running {script}")
        ctx.run(f"uv run {script}")


def _launch_vscode_debug(ctx: Context, script: Path) -> None:
    launch_dir = ctx.project_root / ".vscode"
    launch_dir.mkdir(exist_ok=True)

    launch = {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Run Example",
                "type": "debugpy",
                "request": "launch",
                "program": str(script),
                "console": "integratedTerminal",
                "stopOnEntry": True,
            }
        ],
    }
    (launch_dir / "launch.json").write_text(json.dumps(launch, indent=4))

    subprocess.run(["code", "--reuse-window", "--goto", str(script), str(ctx.project_root)])

    # Requires Accessibility permission for the terminal in
    # System Settings → Privacy & Security → Accessibility
    subprocess.run(
        [
            "osascript",
            "-e",
            """
            tell application "System Events"
                tell process "Code"
                    set frontmost to true
                    delay 2
                    key code 96
                end tell
            end tell
            """,
        ]
    )
