"""Demonstrate the unified terminal: tab completion and persistent shell state.

Run with:  uv run python examples/interactive_session.py
"""
import subprocess

from nowbox import LocalSandbox

sandbox = LocalSandbox(name="session-demo")
terminal = sandbox.terminal
terminal.start_record(sandbox.root / "artifacts" / "session.mp4")

terminal.paste("echo hello from a persistent shell").enter()
terminal.paste("uname -sr").enter()

# cd persists across commands — no manual cwd sync needed
terminal.paste("cd /tmp").enter()
terminal.paste("pwd").enter()

# Tab completion: type partial path, TAB expands it, Enter runs it
terminal.type("ls /usr/bin/pyth")
terminal.key("tab")
terminal.key("enter")

terminal.stop_record()
recording = sandbox.root / "artifacts" / "session.mp4"
print(f"Recording saved to: {recording}")
subprocess.run(["open", str(recording)])
