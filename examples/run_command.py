from nowbox import LocalSandbox as Sandbox
from nowbox import RecordingOptions

sandbox = Sandbox(name="example")
result = sandbox.run(
    ["python", "-c", "print('hello from nowbox')"],
    recording=RecordingOptions(path=sandbox.root / "artifacts" / "example.mp4"),
)

print(f"sandbox id: {sandbox.id}")
print(f"backend: {sandbox.backend}")
print(f"root: {sandbox.root}")
print(f"recording: {result.recording_path}")
print(result.stdout, end="")
raise SystemExit(result.exit_code)
