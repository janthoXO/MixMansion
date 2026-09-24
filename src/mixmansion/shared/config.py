"""App-wide settings, adapter params base and interaction-surface overrides.

Precedence everywhere: explicit value (CLI flag, REST body) > env var > .env > default.
Nothing here is loaded at import time.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """A setting is missing or invalid. The message names the env var."""


class AdapterParams(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIXMANSION_", env_file=".env", extra="ignore")

    workspace: Path = Path(".mixmansion")
    pool_store: str = "file_kv"
    plan_store: str = "yaml_file"
    writer: str = "spotify"
    grouper: str = "louvain"
    namer: str = "llm"
    weights: dict[str, float] = {"genre": 0.5, "theme": 0.5}
    log_level: str = "INFO"


class ServiceOverrides(BaseModel):
    """Service settings passed by the interaction surface; they beat env and .env."""

    spotify: dict[str, Any] = {}


def load[S: BaseSettings](cls: type[S], **values: Any) -> S:
    """Instantiate settings, turning validation errors into one line per env var."""
    try:
        return cls(**values)
    except ValidationError as e:
        prefix = cls.model_config.get("env_prefix", "")
        problems = []
        for err in e.errors():
            var = f"{prefix}{err['loc'][0]}".upper() if err["loc"] else prefix.rstrip("_")
            what = "missing" if err["type"] == "missing" else err["msg"]
            problems.append(f"{var}: {what}")
        raise ConfigError("; ".join(problems)) from None
