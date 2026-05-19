# nowbox

Sandboxing infrastructure extracted from now.

## Quick start

```bash
uv sync
uv run pytest
```

## API sketch

`Sandbox` is the abstract base class. `LocalSandbox` is the first concrete implementation and runs commands on the host process environment.

```python
from nowbox import LocalSandbox as Sandbox

sandbox = Sandbox()
result = sandbox.run(["python", "-c", "print('hello from nowbox')"])

print(result.stdout)
assert result.ok
```

Run the example:

```bash
uv run python examples/run_command.py
```
