from nowbox import LocalSandbox as Sandbox

sandbox = Sandbox()
result = sandbox.run(["python", "-c", "print('hello from nowbox')"])

print(result.stdout, end="")
raise SystemExit(result.exit_code)
