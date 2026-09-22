"""The command-line interface: the interaction surface of the core."""

import inspect
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

import questionary
import typer

from mixmansion import __version__, bootstrap
from mixmansion.core.usecases import (
    AdapterSpec,
    CategorizerSpec,
    MixMansion,
    MixMansionError,
    PlanSpec,
)
from mixmansion.shared.config import ServiceOverrides

POOL = "default"  # the CLI always works on one pool


@dataclass
class State:
    overrides: ServiceOverrides = field(default_factory=ServiceOverrides)
    debug: bool = False
    _app: MixMansion | None = None

    @property
    def app(self) -> MixMansion:
        if self._app is None:
            self._app = bootstrap.build_app(self.overrides)
            logging.basicConfig(
                level=self._app.settings.log_level, format="%(levelname)s %(message)s"
            )
        return self._app


state = State()


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mixmansion {__version__}")
        raise typer.Exit()


def _is_list(annotation: Any) -> bool:
    return get_origin(annotation) is list or any(
        get_origin(a) is list for a in get_args(annotation)
    )


def _parse_pairs(values: list[str], flag: str) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        key, sep, val = value.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"expected KEY=VALUE, got {value!r}", param_hint=flag)
        pairs.append((key.strip(), val.strip()))
    return pairs


def build_cli() -> typer.Typer:
    """Build the Typer app. `pool add` gets one subcommand per registered retriever."""
    app = typer.Typer(
        no_args_is_help=True,
        help="Sort your Spotify songs into new playlists by vibe.",
        pretty_exceptions_enable=False,
    )
    pool = typer.Typer(no_args_is_help=True, help="Collect the songs to sort.")
    pool_add = typer.Typer(no_args_is_help=True, help="Add songs with a retriever.")
    app.add_typer(pool, name="pool")
    pool.add_typer(pool_add, name="add")
    pool_add.callback()(lambda: None)  # keeps `pool add <retriever>` a group with one retriever

    @app.callback()
    def main(
        spotify_client_id: Annotated[
            str | None, typer.Option(help="Overrides SPOTIFY_CLIENT_ID for this run.")
        ] = None,
        spotify_client_secret: Annotated[
            str | None,
            typer.Option(
                help="Overrides SPOTIFY_CLIENT_SECRET for this run. Pass '-' to be prompted: "
                "a value given here stays in your shell history and process list."
            ),
        ] = None,
        spotify_redirect_uri: Annotated[
            str | None, typer.Option(help="Overrides SPOTIFY_REDIRECT_URI for this run.")
        ] = None,
        debug: Annotated[bool, typer.Option("--debug", help="Show tracebacks on errors.")] = False,
        version: Annotated[
            bool,
            typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
        ] = False,
    ) -> None:
        """Sort your Spotify songs into new playlists by vibe."""
        if spotify_client_secret == "-":
            spotify_client_secret = typer.prompt("Spotify client secret", hide_input=True)
        spotify = {
            "client_id": spotify_client_id,
            "client_secret": spotify_client_secret,
            "redirect_uri": spotify_redirect_uri,
        }
        state.overrides = ServiceOverrides(spotify={k: v for k, v in spotify.items() if v})
        state.debug = debug
        state._app = None

    @app.command()
    def adapters(port: Annotated[str, typer.Argument(help="e.g. retriever, categorizer")]) -> None:
        """List the adapters of a port and their params."""
        for info in state.app.list_adapters(port):
            typer.secho(info.name, bold=True)
            props = info.params_schema.get("properties", {})
            required = set(info.params_schema.get("required", []))
            for name, prop in props.items():
                default = "required" if name in required else f"default {prop.get('default')!r}"
                typer.echo(f"  --{name.replace('_', '-')}  ({default})")

    for name, cls in bootstrap.RETRIEVERS.items():
        pool_add.command(name, help=(cls.__doc__ or f"Add songs with {name}.").strip())(
            _retriever_command(name, cls)
        )

    @pool.command("show")
    def pool_show() -> None:
        """List the songs in the pool."""
        songs = state.app.get_pool(POOL).songs
        for s in songs:
            typer.echo(f"{', '.join(s.artists)} – {s.title}  [{', '.join(s.sources)}]")
        typer.echo(f"{len(songs)} songs")

    @pool.command("clear")
    def pool_clear() -> None:
        """Empty the pool. Nothing on Spotify changes."""
        state.app.clear_pool(POOL)
        typer.echo("Pool cleared.")

    @app.command()
    def plan(
        by: Annotated[
            list[str] | None,
            typer.Option(help="CATEGORIZER=WEIGHT, repeatable. Default: MIXMANSION_WEIGHTS."),
        ] = None,
        grouper: Annotated[str | None, typer.Option(help="Default: MIXMANSION_GROUPER.")] = None,
        namer: Annotated[str | None, typer.Option(help="Default: MIXMANSION_NAMER.")] = None,
        opt: Annotated[
            list[str] | None,
            typer.Option(help="ADAPTER.FIELD=VALUE for a categorizer, grouper or namer."),
        ] = None,
        output: Annotated[Path | None, typer.Option("-o", "--output", help="Plan file.")] = None,
    ) -> None:
        """Group the pool into playlists and write the plan for you to review."""
        app = state.app
        weights = {k: float(v) for k, v in _parse_pairs(by or [], "--by")} or app.settings.weights
        opts: dict[str, dict[str, str]] = {}
        for key, value in _parse_pairs(opt or [], "--opt"):
            adapter, sep, fld = key.partition(".")
            if not sep:
                raise typer.BadParameter(
                    f"expected ADAPTER.FIELD=VALUE, got {key!r}", param_hint="--opt"
                )
            opts.setdefault(adapter, {})[fld] = value
        grouper = grouper or app.settings.grouper
        namer = namer or app.settings.namer
        spec = PlanSpec(
            categorizers={
                n: CategorizerSpec(weight=w, params=opts.pop(n, {})) for n, w in weights.items()
            },
            grouper=AdapterSpec(name=grouper, params=opts.pop(grouper, {})),
            namer=AdapterSpec(name=namer, params=opts.pop(namer, {})),
        )
        if opts:
            raise typer.BadParameter(
                f"not used in this plan: {', '.join(opts)}", param_hint="--opt"
            )
        ref = app.build_plan(POOL, spec, str(output) if output else None)
        result = app.get_plan(ref)
        for p in result.playlists:
            typer.echo(f"{len(p.tracks):4}  {p.name}")
        typer.echo(f"{len(result.unassigned):4}  (unassigned)")
        typer.echo(f"\nPlan written to {ref}. Edit it, set `approved: true`, then run `apply`.")

    @app.command()
    def apply(
        ref: Annotated[str, typer.Argument(help="The plan file.")],
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")
        ] = False,
    ) -> None:
        """Create or update the playlists of an approved plan."""
        app = state.app
        current = app.get_plan(ref)
        for p in current.playlists:
            action = "update" if p.spotify_id else "create"
            typer.echo(f"{action:6}  {len(p.tracks):4}  {p.name}")
        if not current.approved:
            raise MixMansionError(f"plan is not approved, set `approved: true` in {ref} first")
        if not yes and not typer.confirm("Apply this plan?", default=False):
            raise typer.Abort()
        result = app.apply_plan(ref)
        typer.echo(f"Created {len(result.created)}, updated {len(result.updated)} playlists.")

    return app


def _retriever_command(name: str, cls: type):
    """A command whose options are generated from the retriever's Params fields.

    Every option is optional: a left-out flag falls back to env, .env, then the default.
    """
    fields = cls.Params.model_fields
    params = [
        inspect.Parameter(
            fld,
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                None,
                f"--{fld.replace('_', '-')}",
                help=(info.description or "")
                + (" (comma-separated)" if _is_list(info.annotation) else ""),
                show_default=False,
            ),
            annotation=str | None,
        )
        for fld, info in fields.items()
    ]

    def command(**given: str | None) -> None:
        app = state.app
        values: dict[str, Any] = {}
        for fld, raw in given.items():
            if raw is not None:
                values[fld] = (
                    [v.strip() for v in raw.split(",") if v.strip()]
                    if _is_list(fields[fld].annotation)
                    else raw
                )
        for fld, info in fields.items():
            if fld in values or not (info.is_required() or info.default is None):
                continue
            if not sys.stdin.isatty():
                continue
            choices = app.adapter_choices("retriever", name, fld, values)
            if not choices:
                continue
            options = [questionary.Choice(c.label, value=c.value) for c in choices]
            if _is_list(info.annotation):
                picked = questionary.checkbox(f"Select {fld}:", choices=options).ask()
            else:
                picked = questionary.select(f"Select {fld}:", choices=options).ask()
            if not picked:
                raise typer.Abort()
            values[fld] = picked
        summary = app.add_to_pool(POOL, name, values)
        typer.echo(
            f"Added {summary.added} songs ({summary.duplicates} already in the pool), "
            f"{summary.total} in total."
        )

    command.__signature__ = inspect.Signature(params)
    command.__name__ = name
    return command


app = build_cli()


def main() -> None:
    """Console entry point: adapter errors become one line unless --debug."""
    try:
        app(prog_name="mixmansion", standalone_mode=False)
    except typer.Abort:
        typer.echo("Aborted.", err=True)
        sys.exit(1)
    except typer.TyperException as e:  # usage errors, e.g. a bad --opt
        e.show()
        sys.exit(e.exit_code)
    except Exception as e:  # noqa: BLE001 — network, auth and validation errors from adapters
        if state.debug:
            raise
        typer.secho(f"Error: {e}", err=True, fg="red")
        sys.exit(1)
