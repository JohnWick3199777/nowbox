import subprocess

from nowbox import LocalSandbox as Sandbox

sandbox = Sandbox(name="example")
terminal = sandbox.terminal
terminal.start_record(sandbox.root / "artifacts" / "terminal.mp4")

terminal.type("python3 --version")
result = terminal.enter()
print(result.output)

terminal.type('python3 -c "import platform; print(platform.platform())"')
result = terminal.enter()
print(result.output)

terminal.paste("python3 -c \"import os; print(f'CPUs: {os.cpu_count()}')\"")
result = terminal.enter()
print(result.output)

terminal.paste("python3 -c \"import shutil; t,u,f = shutil.disk_usage('/'); print(f'Disk: {t//(1<<30)}GB total, {f//(1<<30)}GB free')\"")
result = terminal.enter()
print(result.output)

terminal.type("python3 -c \"import sys; print(f'Python {sys.version}')\"")
result = terminal.enter()
print(result.output)

terminal.stop_record()
recording = sandbox.root / "artifacts" / "terminal.mp4"
print(f"Recording saved to: {recording}")
subprocess.run(["open", str(recording)])
