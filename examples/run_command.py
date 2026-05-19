from nowbox import LocalSandbox as Sandbox
from nowbox import RecordingOptions

sandbox = Sandbox(name="example")
result = sandbox.run(
    ["python", "-c", "print('hello from nowbox')"],
    recording=RecordingOptions(path=sandbox.root / "artifacts" / "example.mp4"),
)

terminal_recording = sandbox.terminal.start_record(sandbox.root / "artifacts" / "terminal.mp4")
sandbox.terminal.type("python -q -c ")
sandbox.terminal.type('"print(')
sandbox.terminal.type("'hello from terminal'")
sandbox.terminal.type(')"')
terminal_result = sandbox.terminal.key("enter")
sandbox.terminal.stop_record()

print(f"sandbox id: {sandbox.id}")
print(f"backend: {sandbox.backend}")
print(f"root: {sandbox.root}")
print(f"run recording: {result.recording_path}")
print(f"terminal recording: {terminal_recording}")
print(result.stdout, end="")
print(terminal_result.stdout, end="")
raise SystemExit(result.exit_code or terminal_result.exit_code)
