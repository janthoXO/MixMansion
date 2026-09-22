"""App-wide settings. Adapter params subclass `AdapterParams`."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterParams(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
