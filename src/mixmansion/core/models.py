"""Domain models. Imports nothing from the project."""

from pydantic import BaseModel


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


class SimilarityGraph(BaseModel):
    dimension: str  # categorizer name
    edges: dict[tuple[str, str], float]  # (song_id_a, song_id_b) -> weight in [0, 1], a < b
    covered: set[str]  # songs this dimension had data for
    labels: dict[str, list[str]] = {}  # optional human-readable tags per song


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


class Playlist(BaseModel):
    name: str
    description: str = ""
    spotify_id: str | None = None  # set by apply
    tracks: list[PlanTrack]


class Plan(BaseModel):
    version: int = 1
    approved: bool = False
    generated: dict = {}  # pool size, weights, adapters + params used
    playlists: list[Playlist]
    unassigned: list[PlanTrack] = []


class Choice(BaseModel):
    value: str
    label: str
