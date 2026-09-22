import json

from fakes import _song, tid

from mixmansion.namers.llm import LLMNamer, NamedPlaylist, Names
from mixmansion.shared.llm import LLMError


def params(**overrides) -> LLMNamer.Params:
    return LLMNamer.Params(**overrides)


class FakeLLM:
    def __init__(self, *scripted):
        self.scripted = list(scripted)  # a Names instance or an Exception, per call
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        result = self.scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def groups(*group_songs: list[int]) -> list:
    return [[_song(n, chr(65 + n)) for n in ids] for ids in group_songs]


def labels_for(songs_and_tags: dict[int, list[str]]) -> dict:
    return {tid(n): tags for n, tags in songs_and_tags.items()}


def test_mapping_by_index_out_of_order():
    llm = FakeLLM(
        Names(
            playlists=[
                NamedPlaylist(index=1, name="Second", description="d2"),
                NamedPlaylist(index=0, name="First", description="d1"),
            ]
        )
    )
    result = LLMNamer(llm).name_groups(groups([1], [2]), {}, params())
    assert result == [("First", "d1"), ("Second", "d2")]


def test_name_truncated_to_max_length():
    llm = FakeLLM(Names(playlists=[NamedPlaylist(index=0, name="A" * 50, description="d")]))
    ((name, _),) = LLMNamer(llm).name_groups(groups([1]), {}, params(max_name_length=10))
    assert name == "A" * 10


def test_description_sanitized():
    long_desc = "line one\nline two   with   spaces" + "x" * 300
    llm = FakeLLM(Names(playlists=[NamedPlaylist(index=0, name="Name", description=long_desc)]))
    ((_, desc),) = LLMNamer(llm).name_groups(groups([1]), {}, params())
    assert "\n" not in desc
    assert "  " not in desc
    assert len(desc) <= 300


def test_duplicate_names_get_roman_suffix():
    llm = FakeLLM(
        Names(
            playlists=[
                NamedPlaylist(index=0, name="Vibes", description="a"),
                NamedPlaylist(index=1, name="vibes", description="b"),
                NamedPlaylist(index=2, name="VIBES", description="c"),
            ]
        )
    )
    result = LLMNamer(llm).name_groups(groups([1], [2], [3]), {}, params())
    assert [n for n, _ in result] == ["Vibes", "vibes II", "VIBES III"]


def test_fallback_when_llm_raises(caplog):
    llm = FakeLLM(LLMError("boom"))
    labels = labels_for({1: ["rock", "chill", "night", "extra"]})
    with caplog.at_level("WARNING"):
        result = LLMNamer(llm).name_groups(groups([1]), labels, params())
    name, desc = result[0]
    assert name.startswith("Mix 1")
    assert "rock" in name
    assert desc == "rock, chill, night"
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_missing_index_falls_back_others_keep_llm_name():
    llm = FakeLLM(Names(playlists=[NamedPlaylist(index=1, name="Second", description="d2")]))
    result = LLMNamer(llm).name_groups(groups([1], [2]), {}, params())
    assert result[0][0].startswith("Mix 1")
    assert result[1] == ("Second", "d2")


def test_more_than_30_groups_split_into_two_requests():
    llm = FakeLLM(
        Names(playlists=[NamedPlaylist(index=i, name=f"N{i}", description="d") for i in range(30)]),
        Names(
            playlists=[NamedPlaylist(index=i, name=f"N{i}", description="d") for i in range(30, 35)]
        ),
    )
    result = LLMNamer(llm).name_groups(groups(*[[1]] * 35), {}, params())
    assert len(llm.calls) == 2
    assert len(result) == 35
    first_chunk = json.loads(llm.calls[0][1])["groups"]
    second_chunk = json.loads(llm.calls[1][1])["groups"]
    assert [g["index"] for g in first_chunk] == list(range(30))
    assert [g["index"] for g in second_chunk] == list(range(30, 35))


def test_summary_limits_songs_and_tags():
    llm = FakeLLM(Names(playlists=[NamedPlaylist(index=0, name="N", description="d")]))
    song_ids = list(range(1, 21))
    tags = {n: ["common"] for n in song_ids}
    tags[1] = ["common", "rare"]
    LLMNamer(llm).name_groups(groups(song_ids), labels_for(tags), params(sample_size=5, top_tags=1))
    sent = json.loads(llm.calls[0][1])["groups"][0]
    assert len(sent["songs"]) == 5
    assert sent["tags"] == ["common"]
