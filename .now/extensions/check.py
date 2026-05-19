from now_sdk import Context, argument, command, mode


@command("check")
@mode("dev")
@argument("path", default=".")
def check(ctx: Context) -> None:
    """Format, lint, and type-check the project."""
    path = ctx.args["path"]
    ctx.run(f"uv run --with ruff ruff format {path} examples", exit=False)
    ctx.run(f"uv run --with ruff ruff check {path} examples --fix", exit=False)
    ctx.run("uv run --with ty ty check src examples", exit=False)
