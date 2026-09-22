import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from mixmansion import bootstrap
from mixmansion.shared.config import AdapterParams, AppSettings, ConfigError, ServiceOverrides, load
from mixmansion.shared.spotify import SpotifySettings


class Params(AdapterParams):
    model_config = SettingsConfigDict(env_prefix="MIXMANSION_RETRIEVER_TEST_")
    query: str
    limit: int = 20


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("MIXMANSION_RETRIEVER_TEST_LIMIT", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def test_precedence_kwarg_env_dotenv_default(tmp_path, monkeypatch):
    assert Params(query="q").limit == 20
    (tmp_path / ".env").write_text("MIXMANSION_RETRIEVER_TEST_LIMIT=30\n")
    assert Params(query="q").limit == 30
    monkeypatch.setenv("MIXMANSION_RETRIEVER_TEST_LIMIT", "40")
    assert Params(query="q").limit == 40
    assert Params(query="q", limit=50).limit == 50


def test_app_settings_defaults_and_json_weights(monkeypatch):
    assert AppSettings().weights == {"genre": 0.5, "mood": 0.5}
    monkeypatch.setenv("MIXMANSION_WEIGHTS", '{"genre": 1}')
    assert AppSettings().weights == {"genre": 1.0}


def test_service_override_beats_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SPOTIFY_CLIENT_ID=from-dotenv\nSPOTIFY_CLIENT_SECRET=s\n")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from-env")
    assert SpotifySettings().client_id == "from-env"
    overrides = ServiceOverrides(spotify={"client_id": "from-cli"})
    assert SpotifySettings(**overrides.spotify).client_id == "from-cli"


def test_missing_setting_names_env_var():
    with pytest.raises(ConfigError, match="SPOTIFY_CLIENT_ID: missing"):
        load(SpotifySettings)
    with pytest.raises(ConfigError, match="MIXMANSION_RETRIEVER_TEST_QUERY: missing"):
        load(Params)


def _secret_fields(params: type[AdapterParams]) -> list[str]:
    return [
        field
        for field, info in params.model_fields.items()
        if SecretStr in (info.annotation, *getattr(info.annotation, "__args__", ()))
    ]


def test_no_secrets_in_params():
    class Leaky(AdapterParams):
        token: SecretStr | None = None

    assert _secret_fields(Leaky) == ["token"]
    registries = [
        bootstrap.RETRIEVERS,
        bootstrap.CATEGORIZERS,
        bootstrap.GROUPERS,
        bootstrap.NAMERS,
        bootstrap.WRITERS,
        bootstrap.POOL_STORES,
        bootstrap.PLAN_STORES,
    ]
    for registry in registries:
        for name, adapter in registry.items():
            assert not _secret_fields(adapter.Params), f"{name}.Params holds a secret"
