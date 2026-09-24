"""Domain models. Imports nothing from the project."""

import re
from typing import Annotated

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

_TRACK_ID = re.compile(
    r"^(?:spotify:track:|https?://open\.spotify\.com/(?:intl-[\w-]+/)?track/)?([0-9A-Za-z]{22})(?:\?.*)?$"
)


def parse_track_id(value: str) -> str:
    """Bare id, spotify:track: URI or open.spotify.com URL -> bare id."""
    m = _TRACK_ID.match(value.strip())
    if not m:
        raise ValueError(f"not a Spotify track id, URI or URL: {value!r}")
    return m.group(1)


class Song(BaseModel):
    id: str  # Spotify track id
    isrc: str | None = None
    title: str
    artists: list[str]
    artist_ids: list[str]
    album: str | None = None
    release_year: int | None = None
    duration_ms: int
    sources: list[str] = []  # provenance, e.g. "playlist:Chill", "search:rainy jazz"


class SongPool(BaseModel):
    songs: list[Song] = []

    def add(self, songs: list[Song]) -> int:
        """Dedupe by ISRC, then id; merge `sources`. Returns the number of songs added.

        ISRC first because a single and its album version have different Spotify ids.
        """
        by_isrc = {s.isrc: s for s in self.songs if s.isrc}
        by_id = {s.id: s for s in self.songs}
        added = 0
        for song in songs:
            existing = (song.isrc and by_isrc.get(song.isrc)) or by_id.get(song.id)
            if existing:
                existing.sources += [src for src in song.sources if src not in existing.sources]
                continue
            song = song.model_copy(deep=True)
            self.songs.append(song)
            by_id[song.id] = song
            if song.isrc:
                by_isrc[song.isrc] = song
            added += 1
        return added


class SongVectors(BaseModel):
    """One categorizer's output: a vector per covered song, compared by cosine similarity."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dimension: str  # categorizer name
    ids: list[str]  # songs this dimension had data for, one per row; the rest are uncovered
    vectors: np.ndarray  # shape (len(ids), d)
    labels: dict[str, list[str]] = {}  # optional human-readable tags per song

    @model_validator(mode="after")
    def _one_row_per_id(self):
        if self.vectors.ndim != 2 or len(self.vectors) != len(self.ids):
            raise ValueError(f"{self.dimension}: need one vector row per id")
        return self

    @classmethod
    def empty(cls, dimension: str) -> "SongVectors":
        return cls(dimension=dimension, ids=[], vectors=np.zeros((0, 0)))

    def subset(self, ids: set[str]) -> "SongVectors":
        """Only the rows of the given songs, e.g. those of one bucket."""
        rows = [i for i, song_id in enumerate(self.ids) if song_id in ids]
        return SongVectors(
            dimension=self.dimension,
            ids=[self.ids[i] for i in rows],
            vectors=self.vectors[rows],
            labels={k: v for k, v in self.labels.items() if k in ids},
        )


class ScoredSong(BaseModel):
    song_id: str
    score: float  # affinity to the group, higher = more typical


class Group(BaseModel):
    songs: list[ScoredSong]  # sorted by score, descending = playlist order


class Grouping(BaseModel):
    groups: list[Group]
    unassigned: list[str] = []  # song ids that could not be grouped


class PlanTrack(BaseModel):
    id: str
    artist: str | None = None  # informational
    title: str | None = None  # informational
    score: float | None = None  # informational
    tags: list[str] = []  # informational

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, v: str | int) -> str:
        # a hand-typed all-digit id is parsed as a number by YAML
        return parse_track_id(str(v))


class Playlist(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    description: str = Field("", max_length=300)
    spotify_id: str | None = None  # set by apply
    tracks: list[PlanTrack]

    @model_validator(mode="after")
    def _no_duplicates(self):
        seen: set[str] = set()
        dupes = {t.id for t in self.tracks if t.id in seen or seen.add(t.id)}
        if dupes:
            raise ValueError(f"playlist {self.name!r} lists these tracks twice: {sorted(dupes)}")
        return self


class Plan(BaseModel):
    version: int = 1
    approved: bool = False
    generated: dict = {}  # pool size, weights, adapters + params used
    playlists: list[Playlist]
    unassigned: list[PlanTrack] = []


class Choice(BaseModel):
    value: str
    label: str
