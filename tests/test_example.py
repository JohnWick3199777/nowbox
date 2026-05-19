from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_example_script_runs() -> None:
    root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, str(root / "examples" / "run_command.py")],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "hello from nowbox" in result.stdout
