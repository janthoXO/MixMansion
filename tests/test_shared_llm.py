from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import BaseModel

from mixmansion.shared import llm as llm_mod
from mixmansion.shared.config import ConfigError
from mixmansion.shared.llm import LLMError, LLMService


class Answer(BaseModel):
    tags: list[str]


def reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("LLM_URL", "http://localhost:11434")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "nomic-embed-text")
    monkeypatch.setattr(llm_mod, "_supports_schema", lambda *a: False)
    return LLMService(tmp_path)


def fake_completion(monkeypatch, *texts):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return reply(texts[len(calls) - 1])

    monkeypatch.setattr(llm_mod, "_completion", completion)
    return calls


def test_valid_json(service, monkeypatch):
    calls = fake_completion(monkeypatch, '```json\n{"tags": ["calm"]}\n```')
    assert service.complete_json("sys", "user", Answer) == Answer(tags=["calm"])
    kwargs = calls[0]
    assert kwargs["model"] == "ollama/qwen2.5:14b"
    assert kwargs["api_base"] == "http://localhost:11434"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in kwargs["messages"][0]["content"]
    assert "temperature" not in kwargs


def test_invalid_then_valid_retries_with_the_error(service, monkeypatch):
    calls = fake_completion(monkeypatch, '{"tags": "nope"}', '{"tags": ["ok"]}')
    assert service.complete_json("sys", "user", Answer).tags == ["ok"]
    assert len(calls) == 2
    assert "invalid" in calls[1]["messages"][-1]["content"]


def test_invalid_twice_raises(service, monkeypatch):
    fake_completion(monkeypatch, "not json", "{}")
    with pytest.raises(LLMError):
        service.complete_json("sys", "user", Answer)


def test_cache_hit_skips_the_call(service, monkeypatch):
    calls = fake_completion(monkeypatch, '{"tags": ["a"]}')
    service.complete_json("sys", "user", Answer)
    again = LLMService(service.cache_path.parent)  # a new run
    assert again.complete_json("sys", "user", Answer).tags == ["a"]
    assert len(calls) == 1


def test_many_keeps_order_and_returns_errors(service, monkeypatch):
    def completion(messages, **kwargs):
        user = messages[1]["content"]
        return reply("garbage" if user == "bad" else f'{{"tags": ["{user}"]}}')

    monkeypatch.setattr(llm_mod, "_completion", completion)
    results = service.complete_json_many([("s", "a"), ("s", "bad"), ("s", "c")], Answer)
    assert results[0].tags == ["a"] and results[2].tags == ["c"]
    assert isinstance(results[1], LLMError)


def test_embeddings_normalized_and_cached(service, monkeypatch):
    calls = []

    def embedding(model, input, **kwargs):
        calls.append(list(input))
        return SimpleNamespace(data=[{"embedding": [3.0, 4.0 * len(t)]} for t in input])

    monkeypatch.setattr(llm_mod, "_embedding", embedding)
    vectors = service.embed(["a", "bb", "a"])
    assert vectors.shape == (3, 2)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)
    assert np.allclose(vectors[0], [0.6, 0.8]) and np.allclose(vectors[0], vectors[2])
    assert calls == [["a", "bb"]]  # each unique text once
    service.embed(["bb", "a"])
    assert len(calls) == 1


def test_missing_settings_only_fail_when_used(tmp_path, monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    service = LLMService(tmp_path)  # constructing needs no settings
    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        service.complete_json("s", "u", Answer)


def test_openai_compatible_maps_to_openai():
    assert llm_mod._model("openai_compatible", "local-model") == "openai/local-model"
