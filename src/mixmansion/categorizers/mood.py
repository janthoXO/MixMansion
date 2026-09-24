"""Mood categorizer: an LLM tags each song's mood and themes from its metadata and lyrics,
the tags are embedded, and songs with similar tags become neighbours."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.port import Categorizer
from mixmansion.core.models import Song, SongVectors
from mixmansion.shared.config import AdapterParams, AppSettings
from mixmansion.shared.http import session
from mixmansion.shared.llm import LLMError, LLMService

log = logging.getLogger(__name__)

PROMPT_VERSION = 1  # bump when the prompt changes, so cached answers aren't reused
SYSTEM_PROMPT = """\
(prompt v{version}) You describe how songs feel. For every song you get, return about \
{n} short, lowercase English tags (between 5 and 10) that describe its mood, energy, \
atmosphere and lyrical themes, e.g. "melancholic", "euphoric", "late night", "heartbreak", \
"road trip". Don't use genre names. When "lyrics" is null the lyrics are unavailable: tag \
from the title, artists, album and year only, and never invent lyrics. Return an entry for \
every song id you were given."""


class SongTags(BaseModel):
    id: str
    tags: list[str]


class TagResponse(BaseModel):
    songs: list[SongTags]


def normalize(tags: list[str]) -> list[str]:
    cleaned = (re.sub(r"\s+", " ", t).strip().lower() for t in tags)
    return list(dict.fromkeys(t for t in cleaned if t))


class MoodCategorizer(Categorizer):
    """Mood, energy and themes, from lyrics and metadata tagged by an LLM."""

    name = "mood"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_CATEGORIZER_MOOD_")
        batch_size: int = Field(10, ge=1, description="Songs per LLM request")
        tags_per_song: int = Field(8, ge=1, description="Target number of tags per song")
        lyrics_max_chars: int = Field(1500, ge=0, description="Lyrics excerpt sent to the LLM")

    def __init__(self, llm: LLMService, settings: AppSettings):
        self.llm = llm
        self.http = session(settings.workspace)

    def vectors(self, songs: list[Song], params: Params) -> SongVectors:
        with ThreadPoolExecutor(max_workers=8) as pool:
            texts = dict(
                zip(
                    (s.id for s in songs),
                    pool.map(lambda s: lrclib.lyrics(self.http, s), songs),
                    strict=True,
                )
            )
        log.info("lyrics found for %d of %d songs", sum(map(bool, texts.values())), len(songs))

        system = SYSTEM_PROMPT.format(version=PROMPT_VERSION, n=params.tags_per_song)

        def request(batch: list[Song]) -> tuple[str, str]:
            payload = [
                {
                    "id": s.id,
                    "title": s.title,
                    "artists": s.artists,
                    "album": s.album,
                    "release_year": s.release_year,
                    "lyrics": (texts[s.id] or "")[: params.lyrics_max_chars] or None,
                }
                for s in batch
            ]
            return system, json.dumps({"songs": payload}, ensure_ascii=False)

        tags: dict[str, list[str]] = {}

        def tag(batches: list[list[Song]]) -> None:
            results = self.llm.complete_json_many([request(b) for b in batches], TagResponse)
            for batch, result in zip(batches, results, strict=True):
                if isinstance(result, LLMError):
                    log.warning("mood tagging failed for %d songs: %s", len(batch), result)
                    continue
                wanted = {s.id for s in batch}
                for entry in result.songs:
                    if entry.id in wanted and (clean := normalize(entry.tags)):
                        tags[entry.id] = clean

        n = params.batch_size
        tag([songs[i : i + n] for i in range(0, len(songs), n)])
        if missing := [s for s in songs if s.id not in tags]:
            tag([[s] for s in missing])  # songs a batch answer left out, one by one
        if missing := [s for s in songs if s.id not in tags]:
            log.warning("%d songs got no mood tags and stay uncovered", len(missing))

        covered = [s.id for s in songs if s.id in tags]
        if not covered:
            return SongVectors.empty(self.name)
        vocab = list(dict.fromkeys(t for i in covered for t in tags[i]))
        tag_vectors = dict(zip(vocab, self.llm.embed(vocab), strict=True))
        vectors = np.array([np.mean([tag_vectors[t] for t in tags[i]], axis=0) for i in covered])
        return SongVectors(
            dimension=self.name,
            ids=covered,
            vectors=vectors,
            labels={i: tags[i] for i in covered},
        )
