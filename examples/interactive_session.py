"""Demonstrate a persistent interactive PTY session with tab completion.

Run with:  uv run python examples/interactive_session.py
"""
import subprocess
from nowbox import LocalSandbox

sandbox = LocalSandbox(name="session-demo")
terminal = sandbox.terminal
terminal.start_record(sandbox.root / "artifacts" / "session.mp4")

with terminal.session() as sess:
    out = sess.run("echo hello from a persistent shell")
    print("echo:", out)

    out = sess.run("uname -sr")
    print("uname:", out)

    # Tab completion: type partial path, press TAB to expand
    sess.type("ls /usr/bin/pyth")
    sess.key("tab")   # bash expands the path
    out_after_tab = sess.run("")  # press Enter on the expanded line
    print("tab-completed ls:", out_after_tab)

    out = sess.run("python3 -c \"import sys; print(sys.version.split()[0])\"")
    print("python version:", out)

terminal.stop_record()
recording = sandbox.root / "artifacts" / "session.mp4"
print(f"Recording saved to: {recording}")
subprocess.run(["open", str(recording)])
