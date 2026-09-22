import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mixmansion.shared.config import ServiceOverrides
from mixmansion.shared.spotify import SpotifyService, SpotifySettings, paginate, parse_id, to_song

TRACK = json.loads((Path(__file__).parent / "fixtures/spotify/track.json").read_text())
ID = "0DiWol3AO6WpXZgp0goxAV"


def test_to_song_maps_a_track():
    song = to_song(TRACK, "playlist:Chill")
    assert song.model_dump() == {
        "id": ID,
        "isrc": "GBDUW0000053",
        "title": "One More Time",
        "artists": ["Daft Punk"],
        "artist_ids": ["4tZwfgrHOc3mvqYlEYSvVi"],
        "album": "Discovery",
        "release_year": 2001,
        "duration_ms": 320357,
        "sources": ["playlist:Chill"],
    }


def test_to_song_missing_isrc_and_year_precision():
    track = {**TRACK, "external_ids": {}, "album": {**TRACK["album"], "release_date": "1999"}}
    song = to_song(track, "s")
    assert song.isrc is None and song.release_year == 1999


@pytest.mark.parametrize(
    "track",
    [
        None,
        {**TRACK, "is_local": True, "id": None},
        {**TRACK, "type": "episode"},
        {**TRACK, "id": None},
    ],
)
def test_to_song_skips_local_files_episodes_and_null_tracks(track):
    assert to_song(track, "s") is None


@pytest.mark.parametrize(
    "value",
    [
        ID,
        f"spotify:track:{ID}",
        f"https://open.spotify.com/track/{ID}",
        f"https://open.spotify.com/track/{ID}?si=abc123",
        f"https://open.spotify.com/intl-de/track/{ID}",
    ],
)
def test_parse_id_formats(value):
    assert parse_id(value, "track") == ID


@pytest.mark.parametrize(
    "value", ["", "abc", f"spotify:playlist:{ID}", f"https://example.com/track/{ID}"]
)
def test_parse_id_rejects(value):
    with pytest.raises(ValueError):
        parse_id(value, "track")


def test_parse_id_playlist():
    assert parse_id(f"https://open.spotify.com/playlist/{ID}?si=x", "playlist") == ID


def test_paginate_follows_next():
    pages = [
        {"items": [1, 2], "next": "p2"},
        {"items": [3], "next": "p3"},
        {"items": [4, 5], "next": None},
    ]
    sp = MagicMock()
    sp.next.side_effect = pages[1:]
    assert list(paginate(sp, pages[0])) == [1, 2, 3, 4, 5]
    assert sp.next.call_count == 2


def test_settings_overrides_beat_env_and_token_cache_per_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SPOTIFY_CLIENT_ID=dotenv\nSPOTIFY_CLIENT_SECRET=s\n")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env")
    assert SpotifySettings().client_id == "env"
    overrides = ServiceOverrides(
        spotify={"client_id": "cli", "redirect_uri": "http://127.0.0.1:9/cb"}
    )
    settings = SpotifySettings(**overrides.spotify)
    assert (settings.client_id, settings.redirect_uri) == ("cli", "http://127.0.0.1:9/cb")
    a = SpotifyService(settings, tmp_path)
    b = SpotifyService(SpotifySettings(), tmp_path)
    assert a.token_cache == tmp_path / "spotify_token_cli.json"
    assert a.token_cache != b.token_cache


def test_client_is_lazy(tmp_path):
    service = SpotifyService(SpotifySettings(client_id="x", client_secret="y"), tmp_path)
    assert "client" not in service.__dict__
    assert service.client.auth_manager.cache_handler.cache_path == str(service.token_cache)
