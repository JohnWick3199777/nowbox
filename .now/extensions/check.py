from now_sdk import Context, command, mode


@command("check")
@mode("dev")
def check(ctx: Context) -> None:
    """Lint and type-check the project."""
    ctx.run("uv run --with ruff ruff check .", exit=False)
    ctx.run("uv run --with ty ty check src", exit=False)
