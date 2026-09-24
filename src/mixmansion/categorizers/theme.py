"""Theme categorizer: an LLM describes what each song's lyrics are about, the descriptions
are embedded, and songs with similar themes become neighbours."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

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
(prompt v{version}) You read song lyrics and describe what they are about. For every song \
you get, return a short plain-English description (one or two sentences, at most about 30 \
words) of what the song is about: its subject, story, perspective and emotional stance \
toward it. Don't mention genre, sound, instrumentation, artist or title. Answer in English \
even when the lyrics are in another language. Return an entry for every song id you were \
given."""


class SongTheme(BaseModel):
    id: str
    theme: str


class ThemeResponse(BaseModel):
    songs: list[SongTheme]


class ThemeCategorizer(Categorizer):
    """What the lyrics are about, described by an LLM and embedded."""

    name = "theme"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_CATEGORIZER_THEME_")
        batch_size: int = Field(10, ge=1, description="Songs per LLM request")
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
        lyrical = [s for s in songs if texts[s.id]]
        log.info("lyrics found for %d of %d songs", len(lyrical), len(songs))

        system = SYSTEM_PROMPT.format(version=PROMPT_VERSION)

        def request(batch: list[Song]) -> tuple[str, str]:
            payload = [
                {
                    "id": s.id,
                    "title": s.title,
                    "artists": s.artists,
                    "lyrics": texts[s.id][: params.lyrics_max_chars],
                }
                for s in batch
            ]
            return system, json.dumps({"songs": payload}, ensure_ascii=False)

        themes: dict[str, str] = {}

        def describe(batches: list[list[Song]]) -> None:
            results = self.llm.complete_json_many([request(b) for b in batches], ThemeResponse)
            for batch, result in zip(batches, results, strict=True):
                if isinstance(result, LLMError):
                    log.warning("theme description failed for %d songs: %s", len(batch), result)
                    continue
                wanted = {s.id for s in batch}
                for entry in result.songs:
                    if entry.id in wanted and (clean := re.sub(r"\s+", " ", entry.theme).strip()):
                        themes[entry.id] = clean

        n = params.batch_size
        describe([lyrical[i : i + n] for i in range(0, len(lyrical), n)])
        if missing := [s for s in lyrical if s.id not in themes]:
            describe([[s] for s in missing])  # songs a batch answer left out, one by one
        if missing := [s for s in lyrical if s.id not in themes]:
            log.warning("%d songs with lyrics got no theme and stay uncovered", len(missing))

        covered = [s.id for s in lyrical if s.id in themes]
        if not covered:
            return SongVectors.empty(self.name)
        vectors = self.llm.embed([themes[i] for i in covered])
        return SongVectors(
            dimension=self.name,
            ids=covered,
            vectors=vectors,
            labels={i: [themes[i]] for i in covered},
        )
