"""Genre categorizer: songs whose Spotify artist genres and Last.fm tags overlap are neighbours."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import numpy as np
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mixmansion.categorizers.lastfm import Lastfm, LastfmSettings
from mixmansion.categorizers.port import Categorizer
from mixmansion.categorizers.spotify import artist_genres
from mixmansion.core.models import SimilarityGraph, Song
from mixmansion.shared.config import AdapterParams, AppSettings, load
from mixmansion.shared.graph import cosine_knn
from mixmansion.shared.http import session
from mixmansion.shared.spotify import SpotifyService

log = logging.getLogger(__name__)

# Popular Last.fm tags that say nothing about the genre
STOPLIST = {
    "seen live",
    "favorites",
    "favourites",
    "favorite",
    "favourite",
    "awesome",
    "love",
    "beautiful",
    "female vocalists",
    "male vocalists",
    "female vocalist",
    "male vocalist",
    "my music",
    "under 2000 listeners",
}


def normalize(tag: str) -> str:
    """Lowercase; `-`, `_` and spaces are the same (`hip-hop` = `hip hop`)."""
    return re.sub(r"[\s_-]+", " ", tag).strip().lower()


class GenreCategorizer(Categorizer):
    """Genres from Spotify's artist genres and Last.fm tags."""

    name = "genre"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_CATEGORIZER_GENRE_")
        k: int = Field(15, ge=1, description="Neighbours kept per song")
        spotify_weight: float = Field(1.0, ge=0, description="Weight of Spotify artist genres")
        lastfm_weight: float = Field(1.0, ge=0, description="Weight of Last.fm tags")
        lastfm_min_count: int = Field(
            10, ge=0, le=100, description="Ignore Last.fm tags whose count is below this"
        )
        labels_per_song: int = Field(5, ge=0, description="Top genres shown per song")

    def __init__(self, spotify: SpotifyService, settings: AppSettings):
        self.spotify = spotify
        self.http = session(settings.workspace)

    def similarity(self, songs: list[Song], params: Params) -> SimilarityGraph:
        weights: dict[str, dict[str, float]] = {s.id: {} for s in songs}

        def add(song: Song, tags: dict[str, float], factor: float) -> None:
            artists = {normalize(a) for a in song.artists}
            for raw, w in tags.items():
                tag = normalize(raw)
                if tag and tag not in STOPLIST and tag not in artists:
                    weights[song.id][tag] = weights[song.id].get(tag, 0.0) + factor * w

        if params.spotify_weight > 0:
            genres = artist_genres(self.spotify.client, [a for s in songs for a in s.artist_ids])
            for song in songs:
                union = dict.fromkeys((g for a in song.artist_ids for g in genres.get(a, [])), 1.0)
                add(song, union, params.spotify_weight)

        if params.lastfm_weight > 0:
            lastfm = Lastfm(self.http, load(LastfmSettings))
            artist_tags = cache(lastfm.artist_tags)

            def tags_of(song: Song) -> dict[str, int]:
                artist = song.artists[0] if song.artists else ""
                return lastfm.track_tags(artist, song.title) or artist_tags(artist)

            with ThreadPoolExecutor(max_workers=8) as pool:
                for song, tags in zip(songs, pool.map(tags_of, songs), strict=True):
                    kept = {t: c / 100 for t, c in tags.items() if c >= params.lastfm_min_count}
                    add(song, kept, params.lastfm_weight)

        covered = [s.id for s in songs if weights[s.id]]
        log.info("genre data for %d of %d songs", len(covered), len(songs))
        vocab = {t: i for i, t in enumerate(dict.fromkeys(t for i in covered for t in weights[i]))}
        # ponytail: dense songs × tags matrix; switch to scipy.sparse if pools get huge
        vectors = np.zeros((len(covered), len(vocab)), dtype=np.float32)
        for row, song_id in enumerate(covered):
            for tag, w in weights[song_id].items():
                vectors[row, vocab[tag]] = w
        return SimilarityGraph(
            dimension=self.name,
            edges=cosine_knn(covered, vectors, params.k) if covered else {},
            covered=set(covered),
            labels={
                i: sorted(weights[i], key=lambda t: -weights[i][t])[: params.labels_per_song]
                for i in covered
            },
        )
