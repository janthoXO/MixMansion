from pathlib import Path

import pytest

from mixmansion.core.models import Plan, PlanTrack, Playlist
from mixmansion.plan_stores.yaml_file import HEADER, PlanFileError, YamlFilePlanStore


def tid(n: int) -> str:
    return f"{n:022d}"


@pytest.fixture(autouse=True)
def chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def store():
    return YamlFilePlanStore()


def _plan() -> Plan:
    return Plan(
        generated={"pool_id": "p1", "pool_size": 3, "weights": {"genre": 0.5, "mood": 0.5}},
        playlists=[
            Playlist(
                name="Late Night Drive",
                description="Moody synth tracks.",
                tracks=[
                    PlanTrack(
                        id=tid(1),
                        artist="The Midnight",
                        title="Sunset",
                        score=0.81,
                        tags=["a", "b"],
                    ),
                    PlanTrack(id=tid(2)),
                ],
            ),
            Playlist(name="Chill", tracks=[PlanTrack(id=tid(3))]),
        ],
        unassigned=[PlanTrack(id=tid(4))],
    )


def test_round_trip(tmp_path, store):
    plan = _plan()
    path = store.save(plan, str(tmp_path / "plan.yaml"))
    assert store.load(path) == plan


def test_new_file_has_header_and_one_line_tracks(tmp_path, store):
    path = store.save(_plan(), str(tmp_path / "plan.yaml"))
    text = Path(path).read_text()
    for line in HEADER.splitlines():
        assert f"# {line}" in text
    assert f"- {{id: '{tid(1)}'" in text
    assert f"- {{id: '{tid(2)}'" in text


def test_default_path_used_when_ref_is_none(tmp_path, store):
    path = store.save(_plan())
    assert path == "plan.yaml"
    assert (tmp_path / "plan.yaml").exists()
    assert store.load("plan.yaml").playlists[0].name == "Late Night Drive"


def test_user_comments_survive_apply_writing_spotify_id(tmp_path, store):
    path = store.save(_plan(), str(tmp_path / "plan.yaml"))
    text = Path(path).read_text()
    text = text.replace("  - name: Chill", "  # keep this one small\n  - name: Chill")
    text = text.replace(
        f"{{id: '{tid(3)}', tags: []}}", f"{{id: '{tid(3)}', tags: []}}  # my favorite"
    )
    Path(path).write_text(text)

    plan = store.load(path)
    for playlist in plan.playlists:
        playlist.spotify_id = f"sp_{playlist.name}"
    store.save(plan, path)

    text2 = Path(path).read_text()
    assert "# keep this one small" in text2
    assert "# my favorite" in text2
    reloaded = store.load(path)
    assert reloaded.playlists[0].spotify_id == "sp_Late Night Drive"
    assert reloaded.playlists[1].spotify_id == "sp_Chill"


@pytest.mark.parametrize(
    "written_id",
    [
        tid(5),
        f"spotify:track:{tid(5)}",
        f"https://open.spotify.com/track/{tid(5)}?si=abc123",
    ],
)
def test_all_id_formats_normalize_on_load(tmp_path, store, written_id):
    path = tmp_path / "plan.yaml"
    path.write_text(
        f"""version: 1
approved: false
playlists:
  - name: A
    tracks:
      - {{id: '{written_id}'}}
unassigned: []
"""
    )
    plan = store.load(str(path))
    assert plan.playlists[0].tracks[0].id == tid(5)


def test_unknown_track_keys_are_ignored(tmp_path, store):
    path = tmp_path / "plan.yaml"
    path.write_text(
        f"""version: 1
approved: false
playlists:
  - name: A
    tracks:
      - {{id: '{tid(1)}', mood: happy, extra: 42}}
unassigned: []
"""
    )
    plan = store.load(str(path))
    assert plan.playlists[0].tracks[0].id == tid(1)


def test_missing_file_raises_file_not_found(tmp_path, store):
    with pytest.raises(FileNotFoundError, match="plan file not found"):
        store.load(str(tmp_path / "nope.yaml"))


def test_bad_indentation_reports_line_number(tmp_path, store):
    path = tmp_path / "plan.yaml"
    path.write_text(
        "version: 1\n"
        "approved: false\n"
        "playlists:\n"
        "  - name: A\n"
        "   description: bad\n"
        "    tracks: []\n"
        "unassigned: []\n"
    )
    with pytest.raises(PlanFileError, match=r"plan\.yaml:5:"):
        store.load(str(path))


def test_missing_playlist_name_reports_line_number(tmp_path, store):
    path = tmp_path / "plan.yaml"
    path.write_text(
        "version: 1\n"
        "approved: false\n"
        "playlists:\n"
        "  - description: d\n"
        "    tracks: []\n"
        "unassigned: []\n"
    )
    with pytest.raises(PlanFileError, match=r"plan\.yaml:4:"):
        store.load(str(path))


def test_duplicate_ids_reports_playlist_line_number(tmp_path, store):
    path = tmp_path / "plan.yaml"
    path.write_text(
        "version: 1\n"
        "approved: false\n"
        "playlists:\n"
        f"  - name: A\n"
        "    tracks:\n"
        f"      - {{id: '{tid(1)}'}}\n"
        f"      - {{id: '{tid(1)}'}}\n"
        "unassigned: []\n"
    )
    with pytest.raises(PlanFileError, match=r"plan\.yaml:4:.*twice"):
        store.load(str(path))


def test_invalid_track_id_reports_line_number(tmp_path, store):
    path = tmp_path / "plan.yaml"
    path.write_text(
        "version: 1\n"
        "approved: false\n"
        "playlists:\n"
        "  - name: A\n"
        "    tracks:\n"
        "      - {id: 'not-a-valid-id'}\n"
        "unassigned: []\n"
    )
    with pytest.raises(PlanFileError, match=r"plan\.yaml:6:"):
        store.load(str(path))


def test_unquoted_numeric_id_is_accepted(tmp_path):
    path = tmp_path / "plan.yaml"
    path.write_text(
        "version: 1\napproved: false\nplaylists:\n  - name: A\n    tracks:\n"
        "      - {id: 1234567890123456789012}\n"
    )
    plan = YamlFilePlanStore().load(str(path))
    assert plan.playlists[0].tracks[0].id == "1234567890123456789012"
