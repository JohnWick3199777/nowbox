# nowbox

Sandboxing infrastructure extracted from now.

## Quick start

```bash
uv sync
uv run python examples/run_command.py
```

## API sketch

`Sandbox` is the abstract base class. `LocalSandbox` is the first concrete implementation and runs commands on the host process environment.

```python
from nowbox import LocalSandbox as Sandbox
from nowbox import RecordingOptions

sandbox = Sandbox(name="example")
result = sandbox.run(
    ["python", "-c", "print('hello from nowbox')"],
    recording=RecordingOptions(path=sandbox.root / "artifacts" / "example.mp4"),
)

print(sandbox.id)
print(sandbox.name)
print(sandbox.backend)
print(sandbox.root)
print(sandbox.status)
print(result.stdout)
print(result.recording_path)
assert result.ok
```

Base sandbox properties:

- `id`: stable sandbox instance identifier
- `name`: human-readable name
- `backend`: backend kind, e.g. `local`, `apple-container`, `docker`
- `root`: default command working directory
- `status`: lifecycle status
- `created_at`: creation timestamp

Run results include:

- `sandbox_id`
- `command`
- `exit_code`
- `stdout`
- `stderr`
- `duration_seconds`
- `cwd`
- `recording_path`

## Recording

Pass `recording=RecordingOptions(path=...)` to create an MP4 artifact for a run. The current local backend records a text-mode execution summary into an MP4 using `ffmpeg`.

```python
result = sandbox.run(
    "python -c \"print('hello')\"",
    recording=RecordingOptions(path=sandbox.root / "artifacts" / "run.mp4"),
)
```

Run the example:

```bash
uv run python examples/run_command.py
```
