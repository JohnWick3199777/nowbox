from nowbox import LocalSandbox as Sandbox
from nowbox import RecordingOptions

sandbox = Sandbox(name="example")

result = sandbox.run(
    ["python", "-c", "print('hello from nowbox')"],
    recording=RecordingOptions(path=sandbox.root / "artifacts" / "example.mp4"),
)
print(result.stdout)

sandbox.terminal.start_record(sandbox.root / "artifacts" / "terminal.mp4")
sandbox.terminal.type("python -q -c ")
sandbox.terminal.type('"print(')
sandbox.terminal.type("'hello from terminal'")
sandbox.terminal.type(')"')
terminal_result = sandbox.terminal.enter()
sandbox.terminal.stop_record()
print(terminal_result.stdout)
