"""Language categorizer: detects each song's lyric language offline (py3langid), no LLM."""

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import numpy as np
import py3langid.langid as langid
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.categorizers import lrclib
from mixmansion.categorizers.port import Categorizer
from mixmansion.core.models import Song, SongVectors
from mixmansion.shared.config import AdapterParams, AppSettings
from mixmansion.shared.http import session

log = logging.getLogger(__name__)

# Common ISO 639-1 codes py3langid returns, mapped to lowercase English names.
LANGUAGE_NAMES = {
    "en": "english",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
    "sv": "swedish",
    "no": "norwegian",
    "da": "danish",
    "fi": "finnish",
    "pl": "polish",
    "ru": "russian",
    "uk": "ukrainian",
    "tr": "turkish",
    "ar": "arabic",
    "he": "hebrew",
    "hi": "hindi",
    "ja": "japanese",
    "ko": "korean",
    "zh": "chinese",
    "vi": "vietnamese",
    "th": "thai",
    "id": "indonesian",
    "el": "greek",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


@cache
def identifier() -> langid.LanguageIdentifier:
    return langid.LanguageIdentifier.from_model_file(langid.MODEL_FILE, norm_probs=True)


class LanguageCategorizer(Categorizer):
    """Language of the lyrics, detected offline."""

    name = "language"
    bucketable = True

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_CATEGORIZER_LANGUAGE_")
        min_probability: float = Field(
            0.5, ge=0, le=1, description="Minimum confidence to accept the detected language"
        )

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
        idf = identifier()
        languages: dict[str, str] = {}
        for s in songs:
            if not (text := texts[s.id]):
                continue
            code, probability = idf.classify(text)
            if probability >= params.min_probability:
                languages[s.id] = language_name(code)
        log.info("language detected for %d of %d songs", len(languages), len(songs))

        covered = [s.id for s in songs if s.id in languages]
        if not covered:
            return SongVectors.empty(self.name)
        vocab = {lang: i for i, lang in enumerate(dict.fromkeys(languages[i] for i in covered))}
        vectors = np.zeros((len(covered), len(vocab)), dtype=np.float32)
        for row, song_id in enumerate(covered):
            vectors[row, vocab[languages[song_id]]] = 1.0
        return SongVectors(
            dimension=self.name,
            ids=covered,
            vectors=vectors,
            labels={i: [languages[i]] for i in covered},
        )
