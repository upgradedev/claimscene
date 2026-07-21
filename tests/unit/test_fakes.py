"""The fakes must be deterministic and honest about being fakes."""
from __future__ import annotations

from claimscene.adapters.fakes import (
    FakeMediaProvider,
    FakeVisionExtractor,
    InMemoryStorage,
)
from claimscene.ports import MediaProvider, StorageBackend, VisionExtractor
from claimscene.scene import SceneGraph


def test_fakes_satisfy_the_ports():
    assert isinstance(FakeVisionExtractor(), VisionExtractor)
    assert isinstance(FakeMediaProvider(), MediaProvider)
    assert isinstance(InMemoryStorage(), StorageBackend)


def test_extractor_deterministic_for_same_photos(photos):
    a = FakeVisionExtractor().extract(photos)
    b = FakeVisionExtractor().extract(photos)
    assert a.model_dump() == b.model_dump()
    assert isinstance(a, SceneGraph)
    assert any("no VLM was called" in n for n in a.confidence_notes)


def test_extractor_context_changes_selection_deterministically(photos):
    base = FakeVisionExtractor().extract(photos)
    ctx = FakeVisionExtractor().extract(photos, context="rear ended at the light")
    ctx2 = FakeVisionExtractor().extract(photos, context="rear ended at the light")
    assert ctx.model_dump() == ctx2.model_dump()
    assert isinstance(base, SceneGraph) and isinstance(ctx, SceneGraph)


def test_provider_deterministic_and_prompt_sensitive():
    provider = FakeMediaProvider()
    a = provider.generate(model="m", prompt="p1", inputs=[b"x"], params={"k": 1})
    b = provider.generate(model="m", prompt="p1", inputs=[b"x"], params={"k": 1})
    c = provider.generate(model="m", prompt="p2", inputs=[b"x"], params={"k": 1})
    assert a == b
    assert a != c
    assert a.startswith(b"CLAIMSCENE-FAKE-MEDIA/")


def test_inmemory_storage_index_surface():
    storage = InMemoryStorage(bucket="t")
    url = storage.put("case/kind/ab/abcd/x.bin", b"data", content_type="application/json")
    assert url == "b2://t/case/kind/ab/abcd/x.bin"
    assert storage.exists("case/kind/ab/abcd/x.bin")
    assert storage.get("case/kind/ab/abcd/x.bin") == b"data"
    assert storage.index == [{"key": "case/kind/ab/abcd/x.bin", "size": 4,
                              "content_type": "application/json"}]
    assert '"key": "case/kind/ab/abcd/x.bin"' in storage.index_jsonl()
    assert storage.reload_index() == storage.index


def test_inmemory_storage_missing_key_raises():
    storage = InMemoryStorage()
    try:
        storage.get("nope")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")
