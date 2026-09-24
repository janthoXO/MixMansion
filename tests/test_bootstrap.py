from mixmansion import bootstrap


def test_registries_hold_the_adapters_under_their_names():
    for port, registry in bootstrap.registries().items():
        for name, adapter in registry.items():
            assert adapter.name == name, f"{port} {name!r} is registered as {adapter.name!r}"
    assert set(bootstrap.RETRIEVERS) == {"playlist", "search"}
    assert set(bootstrap.WRITERS) == {"spotify"}
    assert set(bootstrap.POOL_STORES) == {"file_kv"}
    assert set(bootstrap.PLAN_STORES) == {"yaml_file"}
    assert "theme" in bootstrap.CATEGORIZERS and "llm" in bootstrap.NAMERS
