"""Human-editable plan file. Round-trips comments, ordering and formatting."""

import os
import tempfile
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError

from mixmansion.core.models import Plan, parse_track_id
from mixmansion.plan_stores.port import PlanStore
from mixmansion.shared.config import AdapterParams, load

HEADER = """MixMansion plan. Review and edit, then set `approved: true` and run \
`mixmansion apply <file>`.
- move a song: move its line under another playlist
- remove a song from a playlist: delete the line (nothing is deleted on Spotify)
- add a song: add `- {id: <spotify id or track URL>}`
- drop a playlist: delete its block
artist / title / score / tags are informational and ignored on load."""

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096


class PlanFileError(ValueError):
    """The plan file has a syntax error or fails validation. Message includes line numbers."""


def _is_scalar(v) -> bool:
    return not isinstance(v, (dict, list))


def _to_commented(value):
    """Convert plain dict/list data to CommentedMap/CommentedSeq, flow-styling any
    container whose values are all scalars or scalar lists (so track lines stay one-liners)."""
    if isinstance(value, dict):
        cm = CommentedMap((k, _to_commented(v)) for k, v in value.items())
        if all(
            _is_scalar(v) or (isinstance(v, list) and all(map(_is_scalar, v)))
            for v in value.values()
        ):
            cm.fa.set_flow_style()
        return cm
    if isinstance(value, list):
        seq = CommentedSeq(_to_commented(v) for v in value)
        if all(map(_is_scalar, value)):
            seq.fa.set_flow_style()
        return seq
    return value


def _clean(track: dict) -> dict:
    """Drop informational fields that are unset, so a fresh file stays short."""
    return {k: v for k, v in track.items() if v is not None}


def _track_ids(tracks) -> list[str]:
    return [parse_track_id(str(t["id"] if isinstance(t, dict) else t)) for t in tracks]


def _sync_playlist(old: CommentedMap, new: dict) -> None:
    old["name"] = new["name"]
    old["description"] = new["description"]
    old["spotify_id"] = new["spotify_id"]
    if _track_ids(old.get("tracks", [])) != [t["id"] for t in new["tracks"]]:
        old["tracks"] = _to_commented([_clean(t) for t in new["tracks"]])


def _update_in_place(data: CommentedMap, plan: Plan) -> CommentedMap:
    new = plan.model_dump(mode="json")
    data["version"] = new["version"]
    data["approved"] = new["approved"]
    data["generated"] = _to_commented(new["generated"])

    old_playlists = data.get("playlists") or CommentedSeq()
    if len(old_playlists) != len(new["playlists"]):
        data["playlists"] = _to_commented(
            [{**p, "tracks": [_clean(t) for t in p["tracks"]]} for p in new["playlists"]]
        )
    else:
        for old_p, new_p in zip(old_playlists, new["playlists"], strict=True):
            _sync_playlist(old_p, new_p)

    old_unassigned = data.get("unassigned") or CommentedSeq()
    if _track_ids(old_unassigned) != [t["id"] for t in new["unassigned"]]:
        data["unassigned"] = _to_commented([_clean(t) for t in new["unassigned"]])
    return data


def _build_new(plan: Plan) -> CommentedMap:
    data = plan.model_dump(mode="json")
    for p in data["playlists"]:
        p["tracks"] = [_clean(t) for t in p["tracks"]]
    data["unassigned"] = [_clean(t) for t in data["unassigned"]]
    cm = _to_commented(data)
    cm.yaml_set_start_comment(HEADER)
    return cm


def _write_atomic(path: Path, data: CommentedMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            _yaml.dump(data, f)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _line_for(data, loc: tuple) -> int | None:
    """Walk `loc` (a pydantic error location) through the parsed YAML to find the closest
    line number, falling back to the nearest parent that has one."""
    node = data
    line = None
    for key in loc:
        if isinstance(node, CommentedMap) and key in node:
            line = node.lc.key(key)[0]
            node = node[key]
        elif isinstance(node, CommentedSeq) and isinstance(key, int) and 0 <= key < len(node):
            line = node.lc.item(key)[0]
            node = node[key]
        else:
            break
    return line


class YamlFilePlanStore(PlanStore):
    name = "yaml_file"

    class Params(AdapterParams):
        model_config = SettingsConfigDict(env_prefix="MIXMANSION_PLAN_STORE_YAML_FILE_")
        default_path: Path = Path("plan.yaml")

    def __init__(self):
        self.params = load(self.Params)

    def save(self, plan: Plan, ref: str | None = None) -> str:
        path = Path(ref or self.params.default_path)
        if path.exists():
            with open(path) as f:
                data = _yaml.load(f)
            data = _update_in_place(data, plan)
        else:
            data = _build_new(plan)
        _write_atomic(path, data)
        return str(path)

    def load(self, ref: str) -> Plan:
        path = Path(ref)
        if not path.exists():
            raise FileNotFoundError(f"plan file not found: {path}")
        try:
            with open(path) as f:
                data = _yaml.load(f)
        except MarkedYAMLError as e:
            mark = e.problem_mark or e.context_mark
            line = mark.line + 1 if mark else "?"
            raise PlanFileError(f"{path}:{line}: {e.problem}") from None

        try:
            return Plan.model_validate(data)
        except ValidationError as e:
            problems = []
            for err in e.errors():
                line = _line_for(data, err["loc"])
                problems.append(f"{path}:{'?' if line is None else line + 1}: {err['msg']}")
            raise PlanFileError("\n".join(problems)) from None
