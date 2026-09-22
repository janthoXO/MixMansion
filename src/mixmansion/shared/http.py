from functools import cache
from pathlib import Path

from requests_cache import CachedSession

from mixmansion import __version__


@cache
def session(workspace: Path) -> CachedSession:
    """Cached HTTP session every plain API client uses, so re-running the pipeline is cheap."""
    s = CachedSession(str(workspace / "http_cache.sqlite"), expire_after=30 * 24 * 3600)
    s.headers["User-Agent"] = f"MixMansion/{__version__} (+https://github.com/janthoXO/MixMansion)"
    return s
