import os
import re
import tempfile
from pathlib import Path

from pydantic_settings import SettingsConfigDict

from mixmansion.core.models import SongPool
from mixmansion.pool_stores.port import PoolStore
from mixmansion.shared.config import AdapterParams, AppSettings, load

_POOL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class FileKVPoolStore(PoolStore):
    """One JSON file per pool, at `<dir>/<pool_id>.json`. Writes are atomic."""

    name = "file_kv"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_POOL_STORE_FILE_KV_")
        dir: Path | None = None  # default: <MIXMANSION_WORKSPACE>/pools

    def __init__(self, settings: AppSettings):
        params = load(self.Params)
        self.dir = params.dir or settings.workspace / "pools"

    def _path(self, pool_id: str) -> Path:
        if not _POOL_ID.fullmatch(pool_id):
            raise ValueError(f"invalid pool id: {pool_id!r}")
        return self.dir / f"{pool_id}.json"

    def load(self, pool_id: str) -> SongPool:
        path = self._path(pool_id)
        if not path.exists():
            return SongPool()
        return SongPool.model_validate_json(path.read_text())

    def save(self, pool_id: str, pool: SongPool) -> None:
        path = self._path(pool_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = pool.model_dump_json(indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp_name, path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def delete(self, pool_id: str) -> None:
        self._path(pool_id).unlink(missing_ok=True)
