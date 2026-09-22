"""LLM completions and text embeddings for any provider (local or cloud), through LiteLLM.

`LLM_*` and `EMBEDDINGS_*` are only read when a connector actually uses them. Every answer
is cached on disk, so re-running the pipeline doesn't re-query anything.
"""

import hashlib
import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from mixmansion.shared.config import load

log = logging.getLogger(__name__)

EMBED_BATCH = 100


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    provider: str  # e.g. ollama, openai, anthropic, openrouter, openai_compatible
    model: str
    url: str | None = None
    api_key: SecretStr | None = None
    temperature: float | None = None
    timeout: float = 120
    max_concurrency: int = 4


class EmbeddingsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMBEDDINGS_", env_file=".env", extra="ignore")

    provider: str
    model: str
    url: str | None = None
    api_key: SecretStr | None = None


class LLMError(Exception):
    """The model didn't return valid output, even after one retry."""


def _model(provider: str, model: str) -> str:
    # LiteLLM talks to any OpenAI-compatible server (LM Studio, vLLM, ...) as "openai" + api_base
    return f"{'openai' if provider == 'openai_compatible' else provider}/{model}"


def _completion(**kwargs: Any) -> Any:
    import litellm  # slow to import; only pay for it when a model is actually called

    return litellm.completion(**kwargs)


def _embedding(**kwargs: Any) -> Any:
    import litellm

    return litellm.embedding(**kwargs)


def _supports_schema(provider: str, model: str) -> bool:
    import litellm

    try:
        return litellm.supports_response_schema(model=model, custom_llm_provider=provider)
    except Exception:  # noqa: BLE001 — unknown model: fall back to JSON mode
        return False


def _parse[T: BaseModel](text: str, schema: type[T]) -> T:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    return schema.model_validate(json.loads(text))


class LLMService:
    def __init__(self, workspace: Path):
        self.cache_path = workspace / "llm_cache.sqlite"

    @cached_property
    def llm(self) -> LLMSettings:
        return load(LLMSettings)

    @cached_property
    def embeddings(self) -> EmbeddingsSettings:
        return load(EmbeddingsSettings)

    # ── cache ───────────────────────────────────────────────────────────────

    def _db(self) -> sqlite3.Connection:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.cache_path, timeout=30)
        db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)")
        return db

    def _get(self, keys: list[str]) -> dict[str, str]:
        with closing(self._db()) as db:
            rows = db.execute(
                f"SELECT key, value FROM cache WHERE key IN ({','.join('?' * len(keys))})", keys
            ).fetchall()
        return dict(rows)

    def _put(self, items: dict[str, str]) -> None:
        with closing(self._db()) as db, db:  # closing() closes, `db` commits
            db.executemany("INSERT OR REPLACE INTO cache VALUES (?, ?)", items.items())

    @staticmethod
    def _key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    # ── completions ─────────────────────────────────────────────────────────

    def complete_json[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        """Ask for JSON matching `schema`; retry once with the validation error fed back."""
        s = self.llm
        schema_json = json.dumps(schema.model_json_schema(), sort_keys=True)
        key = self._key("completion", s.provider, s.model, system, user, schema_json)
        if cached := self._get([key]).get(key):
            log.debug("LLM cache hit (%s/%s)", s.provider, s.model)
            return schema.model_validate_json(cached)

        messages = [
            {
                "role": "system",
                "content": f"{system}\n\nReply with JSON only, matching this JSON Schema:\n"
                f"{schema_json}",
            },
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": _model(s.provider, s.model),
            "api_base": s.url,
            "api_key": s.api_key.get_secret_value() if s.api_key else None,
            "timeout": s.timeout,
            "drop_params": True,  # providers without a feature just ignore it
            "response_format": schema
            if _supports_schema(s.provider, s.model)
            else {"type": "json_object"},
        }
        if s.temperature is not None:
            kwargs["temperature"] = s.temperature

        error = None
        for attempt in range(2):
            log.debug("LLM request %s/%s (attempt %d)", s.provider, s.model, attempt + 1)
            text = _completion(messages=messages, **kwargs).choices[0].message.content
            try:
                result = _parse(text, schema)
            except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
                error = e
                messages += [
                    {"role": "assistant", "content": text or ""},
                    {"role": "user", "content": f"That was invalid: {e}. Reply with valid JSON."},
                ]
                continue
            self._put({key: result.model_dump_json()})
            return result
        raise LLMError(f"{s.provider}/{s.model} returned invalid JSON twice: {error}")

    def complete_json_many[T: BaseModel](
        self, requests: list[tuple[str, str]], schema: type[T]
    ) -> list[T | LLMError]:
        """Run requests in parallel (LLM_MAX_CONCURRENCY); failures come back as LLMError."""

        def one(request: tuple[str, str]) -> T | LLMError:
            try:
                return self.complete_json(*request, schema)
            except LLMError as e:
                return e
            except Exception as e:  # noqa: BLE001 — network or provider error for one request
                return LLMError(str(e))

        with ThreadPoolExecutor(max_workers=self.llm.max_concurrency) as pool:
            return list(pool.map(one, requests))

    # ── embeddings ──────────────────────────────────────────────────────────

    def embed(self, texts: list[str]) -> np.ndarray:
        """Shape (n, d), L2-normalized rows."""
        if not texts:
            return np.zeros((0, 0))
        s = self.embeddings
        keys = [self._key("embedding", s.provider, s.model, t) for t in texts]
        cached = self._get(list(set(keys)))
        missing = list(
            dict.fromkeys(t for t, k in zip(texts, keys, strict=True) if k not in cached)
        )
        log.debug("embeddings: %d cached, %d to fetch", len(texts) - len(missing), len(missing))
        for i in range(0, len(missing), EMBED_BATCH):
            batch = missing[i : i + EMBED_BATCH]
            response = _embedding(
                model=_model(s.provider, s.model),
                input=batch,
                api_base=s.url,
                api_key=s.api_key.get_secret_value() if s.api_key else None,
            )
            fresh = {
                self._key("embedding", s.provider, s.model, text): json.dumps(item["embedding"])
                for text, item in zip(batch, response.data, strict=True)
            }
            self._put(fresh)
            cached |= fresh
        vectors = np.array([json.loads(cached[k]) for k in keys], dtype=float)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)
