"""CLI tests against the fakes, driving mixmansion.interfaces.cli with typer's CliRunner."""

import pytest
from fakes import (
    FakePlanStore,
    FakeWriter,
)
from typer.testing import CliRunner

from mixmansion import bootstrap
from mixmansion.core.models import Plan
from mixmansion.interfaces import cli as cli_mod
from mixmansion.shared.config import ServiceOverrides
from mixmansion.shared.spotify import SpotifySettings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _plain_help(monkeypatch):
    """CI (GITHUB_ACTIONS) makes Rich emit ANSI codes that split flag names in --help."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")


@pytest.fixture
def cli(monkeypatch, fake_app):
    """Build a fresh Typer app wired to the fakes; `bootstrap.build_app` returns it and
    captures the `overrides` it was called with."""
    app, instances = fake_app
    adapters = app._adapters

    monkeypatch.setattr(bootstrap, "RETRIEVERS", adapters["retriever"])
    monkeypatch.setattr(bootstrap, "CATEGORIZERS", adapters["categorizer"])
    monkeypatch.setattr(bootstrap, "GROUPERS", adapters["grouper"])
    monkeypatch.setattr(bootstrap, "NAMERS", adapters["namer"])
    monkeypatch.setattr(bootstrap, "WRITERS", adapters["writer"])
    monkeypatch.setattr(bootstrap, "POOL_STORES", adapters["pool_store"])
    monkeypatch.setattr(bootstrap, "PLAN_STORES", adapters["plan_store"])

    captured: dict = {}

    def fake_build_app(overrides=None):
        captured["overrides"] = overrides or ServiceOverrides()
        return app

    monkeypatch.setattr(bootstrap, "build_app", fake_build_app)

    fresh = cli_mod.build_cli()
    cli_mod.state._app = None
    cli_mod.state.overrides = ServiceOverrides()
    return fresh, app, instances, captured


def test_pool_add_and_show(cli):
    app_cli, _app, _instances, _ = cli
    result = runner.invoke(app_cli, ["pool", "add", "fake", "--source", "default"])
    assert result.exit_code == 0, result.output
    assert "Added 6 songs (0 already in the pool), 6 in total." in result.output

    result = runner.invoke(app_cli, ["pool", "show"])
    assert result.exit_code == 0, result.output
    assert "Song 1" in result.output or "A –" in result.output
    assert "6 songs" in result.output


def _plan_and_approve(app_cli, instances, output_path):
    runner.invoke(app_cli, ["pool", "add", "fake", "--source", "default"])
    result = runner.invoke(app_cli, ["plan", "-o", str(output_path)])
    assert result.exit_code == 0, result.output
    assert "Group 1" in result.output
    assert "Group 2" in result.output
    assert str(output_path) in result.output
    ref = str(output_path)
    instances[FakePlanStore].plans[ref].approved = True
    return ref


def test_plan_writes_ref_and_playlists(cli, tmp_path):
    app_cli, _app, instances, _ = cli
    ref = _plan_and_approve(app_cli, instances, tmp_path / "plan.yaml")
    assert ref in instances[FakePlanStore].plans


def test_apply_with_yes(cli, tmp_path):
    app_cli, _app, instances, _ = cli
    ref = _plan_and_approve(app_cli, instances, tmp_path / "plan.yaml")

    result = runner.invoke(app_cli, ["apply", ref, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Created 2" in result.output


def test_apply_without_yes_aborts_on_no(cli, tmp_path):
    app_cli, _app, instances, _ = cli
    ref = _plan_and_approve(app_cli, instances, tmp_path / "plan.yaml")

    result = runner.invoke(app_cli, ["apply", ref], input="n\n")
    assert result.exit_code != 0
    assert instances[FakeWriter].calls == []


def test_retriever_flags_from_params(cli):
    app_cli, *_ = cli
    result = runner.invoke(app_cli, ["pool", "add", "fake", "--help"])
    assert result.exit_code == 0, result.output
    assert "--source" in result.output


def test_spotify_override_beats_env(cli, monkeypatch):
    app_cli, _app, _instances, captured = cli
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from-env")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "from-env-secret")

    result = runner.invoke(app_cli, ["--spotify-client-id", "from-cli", "pool", "show"])
    assert result.exit_code == 0, result.output
    overrides = captured["overrides"]
    assert SpotifySettings(**overrides.spotify).client_id == "from-cli"


def test_spotify_override_absent_falls_back_to_env(cli, monkeypatch):
    app_cli, _app, _instances, captured = cli
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from-env")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "from-env-secret")

    result = runner.invoke(app_cli, ["pool", "show"])
    assert result.exit_code == 0, result.output
    assert captured["overrides"].spotify == {}
    assert SpotifySettings().client_id == "from-env"


def test_spotify_client_secret_prompt(cli, monkeypatch):
    app_cli, _app, _instances, captured = cli
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from-env")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "from-env-secret")

    result = runner.invoke(
        app_cli,
        ["--spotify-client-secret", "-", "pool", "show"],
        input="s3cret\n",
    )
    assert result.exit_code == 0, result.output
    assert captured["overrides"].spotify["client_secret"] == "s3cret"


def test_help_lists_commands_and_spotify_options(cli):
    app_cli, *_ = cli
    result = runner.invoke(app_cli, ["--help"])
    assert result.exit_code == 0, result.output
    for expected in (
        "adapters",
        "pool",
        "plan",
        "apply",
        "--spotify-client-id",
        "--spotify-client-secret",
        "--spotify-redirect-uri",
    ):
        assert expected in result.output


def test_plan_bad_opt_target_fails(cli):
    app_cli, *_ = cli
    result = runner.invoke(app_cli, ["plan", "--opt", "nope.x=1"])
    assert result.exit_code != 0


class FakePrompt:
    """Stands in for a questionary prompt: `.ask()` returns the scripted answer."""

    def __init__(self, answer):
        self.answer = answer

    def ask(self):
        return self.answer


class FakeQuestionary:
    """Records the prompts and answers them from a script."""

    def __init__(self, checkbox, texts=()):
        self.checkbox_answer = checkbox
        self.texts = list(texts)
        self.choices = []
        self.asked = []
        self.validate = None

    def Choice(self, title, value, checked=False):  # noqa: N802 — mirrors questionary's API
        self.choices.append((title, value, checked))
        return (title, value, checked)

    def checkbox(self, message, choices):
        self.asked.append(message)
        return FakePrompt(self.checkbox_answer)

    def text(self, message, instruction=None, validate=None):
        self.asked.append(message.strip())
        self.validate = validate
        return FakePrompt(self.texts.pop(0))


def _weights(cli, monkeypatch, checkbox, texts=(), tty=True):
    app_cli, app, _instances, _captured = cli
    fake = FakeQuestionary(checkbox, texts)
    monkeypatch.setattr(cli_mod, "questionary", fake)
    monkeypatch.setattr(cli_mod, "_interactive", lambda: tty)
    built = {}
    monkeypatch.setattr(
        app, "build_plan", lambda pool, spec, ref=None: (built.update(spec=spec), "p")[1]
    )
    monkeypatch.setattr(app, "get_plan", lambda ref: Plan(playlists=[]))
    result = runner.invoke(app_cli, ["plan"])
    return result, fake, built.get("spec")


def test_plan_asks_for_categories_and_equal_weights(cli, monkeypatch):
    result, fake, spec = _weights(cli, monkeypatch, checkbox=["fake"], texts=[])
    assert result.exit_code == 0, result.output
    assert fake.choices == [
        ("fake — A categorizer that splits songs by index parity.", "fake", True)
    ]
    assert {n: c.weight for n, c in spec.categorizers.items()} == {"fake": 1.0}


def test_plan_weights_are_asked_per_category_and_default_to_equal(cli, monkeypatch):
    app_cli, app, _i, _c = cli
    app._adapters["categorizer"]["other"] = app._adapters["categorizer"]["fake"]
    result, fake, spec = _weights(cli, monkeypatch, checkbox=["fake", "other"], texts=["3", ""])
    assert result.exit_code == 0, result.output
    assert {n: c.weight for n, c in spec.categorizers.items()} == {"fake": 3.0, "other": 1.0}
    assert "fake 75% · other 25%" in result.output
    assert fake.validate("2") is True and fake.validate("") is True
    assert fake.validate("0") != True and fake.validate("abc") != True  # noqa: E712


def test_plan_does_not_prompt_without_a_tty(cli, monkeypatch):
    result, fake, spec = _weights(cli, monkeypatch, checkbox=None, tty=False)
    assert result.exit_code == 0, result.output
    assert fake.asked == []
    assert set(spec.categorizers) == set(cli[1].settings.weights)


def test_plan_does_not_prompt_when_by_is_given(cli, monkeypatch):
    app_cli, app, _i, _c = cli
    fake = FakeQuestionary(checkbox=None)
    monkeypatch.setattr(cli_mod, "questionary", fake)
    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    spec = {}
    monkeypatch.setattr(app, "build_plan", lambda pool, s, ref=None: spec.update(s=s) or "p")
    monkeypatch.setattr(app, "get_plan", lambda ref: Plan(playlists=[]))
    result = runner.invoke(app_cli, ["plan", "--by", "fake=2"])
    assert result.exit_code == 0, result.output
    assert fake.asked == []
    assert spec["s"].categorizers["fake"].weight == 2.0


def test_plan_needs_at_least_one_category(cli, monkeypatch):
    result, _fake, _spec = _weights(cli, monkeypatch, checkbox=[])
    assert result.exit_code != 0
    assert "at least one category" in str(result.exception) + result.output
