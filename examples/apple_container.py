from nowbox import AppleContainerSandbox

sandbox = AppleContainerSandbox("python:3.13", name="nowbox-example")
sandbox.start()

terminal = sandbox.terminal
terminal.start_record(sandbox.root / "artifacts" / "terminal.mp4")

terminal.type("python --version")
result = terminal.enter()
print(result.output)

terminal.type('python -c "import platform; print(platform.platform())"')
result = terminal.enter()
print(result.output)

terminal.paste("python -c \"import os; print(f'CPUs: {os.cpu_count()}')\"")
result = terminal.enter()
print(result.output)

terminal.paste("python -c \"import shutil; t,u,f = shutil.disk_usage('/'); print(f'Disk: {t//(1<<30)}GB total, {f//(1<<30)}GB free')\"")
result = terminal.enter()
print(result.output)

terminal.stop_record()
print(f"Recording saved to: {sandbox.root / 'artifacts' / 'terminal.mp4'}")

sandbox.stop()
