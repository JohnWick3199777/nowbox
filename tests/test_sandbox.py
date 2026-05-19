from __future__ import annotations

import inspect
import subprocess
import sys

import pytest

from nowbox import LocalSandbox, Sandbox, SandboxResult


def test_sandbox_is_abstract_base_class() -> None:
    assert inspect.isabstract(Sandbox)
    assert issubclass(LocalSandbox, Sandbox)

    with pytest.raises(TypeError):
        Sandbox()


def test_local_sandbox_run_executes_command_and_returns_result() -> None:
    sandbox = LocalSandbox()

    result = sandbox.run([sys.executable, "-c", "print('hello from sandbox')"])

    assert isinstance(result, SandboxResult)
    assert result.command == [sys.executable, "-c", "print('hello from sandbox')"]
    assert result.exit_code == 0
    assert result.ok is True
    assert result.stdout.strip() == "hello from sandbox"
    assert result.stderr == ""
    assert result.duration_seconds >= 0


def test_local_sandbox_run_accepts_string_commands() -> None:
    sandbox = LocalSandbox()

    result = sandbox.run(f"{sys.executable} -c \"print('string command')\"")

    assert result.ok
    assert result.stdout.strip() == "string command"


def test_local_sandbox_run_can_raise_on_failure() -> None:
    sandbox = LocalSandbox()

    with pytest.raises(subprocess.CalledProcessError) as exc:
        sandbox.run([sys.executable, "-c", "import sys; sys.exit(7)"], check=True)

    assert exc.value.returncode == 7
