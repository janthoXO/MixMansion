"""Names each group with an LLM: a short evocative name and a one-sentence description."""

import json
import logging
import re
from collections import Counter

from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import Song
from mixmansion.namers.port import PlaylistNamer
from mixmansion.shared.config import AdapterParams
from mixmansion.shared.llm import LLMService

log = logging.getLogger(__name__)

MAX_GROUPS_PER_REQUEST = 30
PROMPT_VERSION = 1  # bump to bust the LLM cache when the prompt changes
ROMAN = ["II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"]  # occurrences 2..11

SYSTEM_PROMPT = f"""You name playlists for a music app (prompt v{PROMPT_VERSION}).

For each group, give a short, evocative, distinct name (at most `max_name_length`
characters) and a one-sentence description (at most 300 characters, no newlines),
in the requested language. Follow the style hint if one is given. Names must differ
from each other. Return exactly one entry per group index."""


class NamedPlaylist(BaseModel):
    index: int
    name: str
    description: str


class Names(BaseModel):
    playlists: list[NamedPlaylist]


def _summarize(index: int, songs: list[Song], labels: dict[str, list[str]], params) -> dict:
    tags = Counter(tag for s in songs for tag in labels.get(s.id, []))
    return {
        "index": index,
        "songs": [
            f"{s.artists[0] if s.artists else '?'} – {s.title}" for s in songs[: params.sample_size]
        ],
        "tags": [tag for tag, _ in tags.most_common(params.top_tags)],
    }


def _fallback(index: int, tags: list[str], params) -> tuple[str, str]:
    suffix = " · " + ", ".join(tags[:3]) if tags else ""
    name = f"Mix {index + 1}{suffix}"[: params.max_name_length]
    return name, ", ".join(tags[:3])


def _sanitize(entry: NamedPlaylist | None, tags: list[str], index: int, params) -> tuple[str, str]:
    if entry is None or not entry.name.strip():
        return _fallback(index, tags, params)
    name = entry.name.strip()[: params.max_name_length]
    description = re.sub(r"\s+", " ", entry.description).strip()[:300]
    return name, description


def _dedupe(names: list[tuple[str, str]], max_name_length: int) -> list[tuple[str, str]]:
    seen: dict[str, int] = {}
    result = []
    for name, description in names:
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        n = seen[key]
        if n > 1:
            suffix = f" {ROMAN[n - 2]}" if n - 2 < len(ROMAN) else f" {n}"
            name = name[: max_name_length - len(suffix)] + suffix
        result.append((name, description))
    return result


class LLMNamer(PlaylistNamer):
    """Names and describes each group with an LLM, local or cloud, through `shared/llm.py`."""

    name = "llm"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_NAMER_LLM_")
        sample_size: int = Field(15, ge=1, description="Most typical songs shown per group")
        top_tags: int = Field(10, ge=1, description="Most common tags shown per group")
        max_name_length: int = Field(40, ge=1, description="Max playlist name length")
        language: str = Field("en", description="Language of names and descriptions")
        style: str = Field("", description='Optional style hint, e.g. "lowercase, no emojis"')

    def __init__(self, llm: LLMService):
        self.llm = llm

    def name_groups(
        self, groups: list[list[Song]], labels: dict[str, list[str]], params: Params
    ) -> list[tuple[str, str]]:
        summaries = [_summarize(i, g, labels, params) for i, g in enumerate(groups)]
        by_index: dict[int, NamedPlaylist] = {}
        for start in range(0, len(summaries), MAX_GROUPS_PER_REQUEST):
            chunk = summaries[start : start + MAX_GROUPS_PER_REQUEST]
            user = json.dumps(
                {
                    "language": params.language,
                    "style": params.style,
                    "max_name_length": params.max_name_length,
                    "groups": chunk,
                },
                ensure_ascii=False,
            )
            try:
                result = self.llm.complete_json(SYSTEM_PROMPT, user, Names)
                for entry in result.playlists:
                    by_index[entry.index] = entry
            except Exception:  # noqa: BLE001 — any failure falls back for this chunk
                log.warning(
                    "LLM naming failed for groups %d–%d; using fallback names",
                    chunk[0]["index"],
                    chunk[-1]["index"],
                )

        named = [
            _sanitize(by_index.get(s["index"]), s["tags"], s["index"], params) for s in summaries
        ]
        return _dedupe(named, params.max_name_length)
