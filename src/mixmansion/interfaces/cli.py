import typer

from mixmansion import __version__

app = typer.Typer(no_args_is_help=True, help="Sort your Spotify songs into new playlists by vibe.")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mixmansion {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Sort your Spotify songs into new playlists by vibe."""
