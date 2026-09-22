import pytest

from mixmansion.core.models import Song, SongPool
from mixmansion.pool_stores.file_kv import FileKVPoolStore
from mixmansion.shared.config import AppSettings


def _song() -> Song:
    return Song(
        id="0" * 22,
        isrc="ISRC1",
        title="Title",
        artists=["Artist"],
        artist_ids=["a1"],
        album="Album",
        release_year=1999,
        duration_ms=180000,
        sources=["playlist:Chill", "search:rainy jazz"],
    )


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MIXMANSION_POOL_STORE_FILE_KV_DIR", raising=False)


def _store(tmp_path) -> FileKVPoolStore:
    return FileKVPoolStore(AppSettings(workspace=tmp_path))


def test_round_trip_preserves_all_fields(tmp_path):
    store = _store(tmp_path)
    pool = SongPool(songs=[_song()])
    store.save("mypool", pool)
    loaded = store.load("mypool")
    assert loaded == pool


def test_missing_pool_is_empty(tmp_path):
    store = _store(tmp_path)
    assert store.load("nope") == SongPool()


def test_delete_missing_is_noop(tmp_path):
    store = _store(tmp_path)
    store.delete("nope")  # no error


def test_delete_removes_pool(tmp_path):
    store = _store(tmp_path)
    store.save("mypool", SongPool(songs=[_song()]))
    store.delete("mypool")
    assert store.load("mypool") == SongPool()


@pytest.mark.parametrize("pool_id", ["../x", "a/b", "", "a" * 65])
def test_invalid_pool_ids_rejected(tmp_path, pool_id):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.load(pool_id)
    with pytest.raises(ValueError):
        store.save(pool_id, SongPool())
    with pytest.raises(ValueError):
        store.delete(pool_id)


def test_save_is_atomic(tmp_path, monkeypatch):
    store = _store(tmp_path)
    original = SongPool(songs=[_song()])
    store.save("mypool", original)
    path = store._path("mypool")
    original_content = path.read_text()

    def boom(self, *args, **kwargs):
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(SongPool, "model_dump_json", boom)
    with pytest.raises(RuntimeError):
        store.save("mypool", SongPool(songs=[]))

    assert path.read_text() == original_content
    assert list(path.parent.iterdir()) == [path]


def test_default_dir_is_workspace_pools(tmp_path):
    store = _store(tmp_path)
    assert store.dir == tmp_path / "pools"


def test_dir_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom_pools"
    monkeypatch.setenv("MIXMANSION_POOL_STORE_FILE_KV_DIR", str(custom))
    store = FileKVPoolStore(AppSettings(workspace=tmp_path))
    assert store.dir == custom
