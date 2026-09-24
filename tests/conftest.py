"""Shared fixtures: an isolated cwd (no .env leaks) and a MixMansion built from fakes."""

import pytest
from fakes import (
    FakeBucketCategorizer,
    FakeCategorizer,
    FakeGrouper,
    FakeNamer,
    FakePlanStore,
    FakePoolStore,
    FakeRetriever,
    FakeWriter,
)

from mixmansion.core.usecases import MixMansion
from mixmansion.shared.config import AppSettings


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def fake_app():
    instances = {
        FakeRetriever: FakeRetriever(),
        FakeCategorizer: FakeCategorizer(),
        FakeBucketCategorizer: FakeBucketCategorizer(),
        FakeGrouper: FakeGrouper(),
        FakeNamer: FakeNamer(),
        FakeWriter: FakeWriter(),
        FakePoolStore: FakePoolStore(),
        FakePlanStore: FakePlanStore(),
    }
    adapters = {
        "retriever": {"fake": FakeRetriever},
        "categorizer": {"fake": FakeCategorizer, "bucket": FakeBucketCategorizer},
        "grouper": {"fake": FakeGrouper},
        "namer": {"fake": FakeNamer},
        "writer": {"fake": FakeWriter},
        "pool_store": {"fake": FakePoolStore},
        "plan_store": {"fake": FakePlanStore},
    }
    settings = AppSettings(
        pool_store="fake",
        plan_store="fake",
        writer="fake",
        grouper="fake",
        namer="fake",
        weights={"fake": 1.0},
    )
    app = MixMansion(settings, adapters, lambda cls: instances[cls])
    return app, instances
