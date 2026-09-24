"""Keywords categorizer: distinctive words the lyrics share (TF-IDF), no LLM."""

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from math import log as ln

import numpy as np
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.port import Categorizer
from mixmansion.core.models import Song, SongVectors
from mixmansion.shared.config import AdapterParams, AppSettings
from mixmansion.shared.http import session

log = logging.getLogger(__name__)

# Sung filler with no lexical meaning
VOCABLES = {"yeah", "yeh", "ooh", "ohh", "ahh", "hey", "whoa", "woah", "mmm", "lala", "nana"}


def words(text: str) -> list[str]:
    """Lowercase words, at least 3 Unicode letters, section markers and vocables dropped."""
    stripped = re.sub(r"\[[^\]]*\]", " ", text)
    return [w for w in re.findall(r"[^\W\d_]{3,}", stripped.lower()) if w not in VOCABLES]


class KeywordsCategorizer(Categorizer):
    """Distinctive words the lyrics share (TF-IDF)."""

    name = "keywords"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_CATEGORIZER_KEYWORDS_")
        max_df: float = Field(
            0.5,
            gt=0,
            le=1,
            description=(
                "Ignore words in more than this share of songs with lyrics "
                "(drops stopwords in any language)"
            ),
        )
        min_df: int = Field(2, ge=1, description="Ignore words in fewer songs than this")
        labels_per_song: int = Field(5, ge=0, description="Top keywords shown per song")

    def __init__(self, settings: AppSettings):
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
        with_lyrics = [s.id for s in songs if texts[s.id]]
        log.info("lyrics found for %d of %d songs", len(with_lyrics), len(songs))

        counts = {i: Counter(words(texts[i])) for i in with_lyrics}
        n = len(with_lyrics)
        df = Counter(w for c in counts.values() for w in c)
        # smoothed idf over the words neither too rare to link songs nor too common to mean much
        idf = {
            w: ln((1 + n) / (1 + d)) + 1
            for w, d in df.items()
            if params.min_df <= d <= params.max_df * n
        }
        # sublinear tf, so a chorus repeating one word doesn't dominate
        weights = {
            i: {w: (1 + ln(c)) * idf[w] for w, c in counts[i].items() if w in idf}
            for i in with_lyrics
        }

        covered = [i for i in with_lyrics if weights[i]]
        if not covered:
            return SongVectors.empty(self.name)
        vocab = {w: i for i, w in enumerate(dict.fromkeys(w for i in covered for w in weights[i]))}
        # ponytail: dense songs × words matrix; switch to scipy.sparse if pools get huge
        vectors = np.zeros((len(covered), len(vocab)), dtype=np.float32)
        for row, song_id in enumerate(covered):
            for w, weight in weights[song_id].items():
                vectors[row, vocab[w]] = weight
        return SongVectors(
            dimension=self.name,
            ids=covered,
            vectors=vectors,
            labels={
                i: sorted(weights[i], key=lambda w: (-weights[i][w], w))[: params.labels_per_song]
                for i in covered
            },
        )
