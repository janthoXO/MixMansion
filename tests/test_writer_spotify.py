from unittest.mock import MagicMock

import pytest
from spotipy import SpotifyException

from mixmansion.writers.port import PlaylistNotFound
from mixmansion.writers.spotify import SpotifyWriter


class FakeService:
    def __init__(self):
        self.client = MagicMock()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MIXMANSION_WRITER_SPOTIFY_PUBLIC", raising=False)
    monkeypatch.delenv("MIXMANSION_WRITER_SPOTIFY_NAME_PREFIX", raising=False)


@pytest.fixture
def writer():
    return SpotifyWriter(FakeService())


def ids(n: int) -> list[str]:
    return [f"{i:022d}" for i in range(n)]


def test_create_uses_prefix_public_flag_and_sanitized_description(writer):
    writer.spotify.client.current_user_playlist_create.return_value = {"id": "new_id"}
    params = SpotifyWriter.Params(public=True, name_prefix="◐ ")
    description = "line one\r\nline two\n\n  extra   spaces" + "x" * 400

    result = writer.create("My Playlist", description, params)

    assert result == "new_id"
    call = writer.spotify.client.current_user_playlist_create.call_args
    assert call.args == ("◐ My Playlist",)
    assert call.kwargs["public"] is True
    assert len(call.kwargs["description"]) == 300
    assert "\n" not in call.kwargs["description"] and "\r" not in call.kwargs["description"]


@pytest.mark.parametrize("n", [0, 1, 100, 101, 250])
def test_replace_tracks_chunks_at_100(writer, n):
    params = SpotifyWriter.Params()
    track_ids = ids(n)

    writer.replace_tracks("pl1", track_ids, params)

    expected_uris = [f"spotify:track:{i}" for i in track_ids]
    writer.spotify.client.playlist_replace_items.assert_called_once_with("pl1", expected_uris[:100])
    add_calls = writer.spotify.client.playlist_add_items.call_args_list
    remaining = expected_uris[100:]
    expected_add_chunks = [remaining[i : i + 100] for i in range(0, len(remaining), 100)]
    assert [c.args[1] for c in add_calls] == expected_add_chunks


def test_replace_tracks_404_on_replace_raises_playlist_not_found(writer):
    writer.spotify.client.playlist_replace_items.side_effect = SpotifyException(404, -1, "gone")
    with pytest.raises(PlaylistNotFound):
        writer.replace_tracks("pl1", ids(1), SpotifyWriter.Params())


def test_replace_tracks_404_on_add_raises_playlist_not_found(writer):
    writer.spotify.client.playlist_add_items.side_effect = SpotifyException(404, -1, "gone")
    with pytest.raises(PlaylistNotFound):
        writer.replace_tracks("pl1", ids(150), SpotifyWriter.Params())


def test_replace_tracks_other_error_propagates(writer):
    writer.spotify.client.playlist_replace_items.side_effect = SpotifyException(500, -1, "boom")
    with pytest.raises(SpotifyException):
        writer.replace_tracks("pl1", ids(1), SpotifyWriter.Params())
