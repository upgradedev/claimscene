"""Unit tests for B2Storage's merge-on-write index persistence
(``_persist_index``) — a prerequisite for multitenancy's ``DELETE
/me/data``: background render-job threads and request threads can write to
the same bucket concurrently (see ``claimscene.api._job_storage``), and a
just-deleted row must actually stay deleted rather than being silently
resurrected by another writer's next ``put``. Drives the adapter through a
small in-memory S3-compatible stub (no boto3/creds needed in CI). Ported from
Cinemory's equivalent coverage (that repo's ``_persist_index`` merge-on-write
fix predates its own multitenancy PR).

This file defines its own local stub (mirrors
``tests/unit/test_b2_storage_delete.py``'s pattern) rather than the shared
``fake_s3`` fixture in ``tests/conftest.py``, and only covers the NEW
merge-on-write behaviour — basic put/get/region coverage already lives in
``tests/integration/test_b2_storage.py``.
"""
from __future__ import annotations

import json

import pytest

from claimscene.adapters.b2_storage import B2Storage


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    """Minimal in-memory S3 stub shared across adapter instances (one bucket)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.store[(Bucket, Key)] = Body
        return {}

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.store:
            raise KeyError(Key)
        return {"Body": _Body(self.store[(Bucket, Key)])}


@pytest.fixture
def b2_env(clean_b2_env, monkeypatch):
    # A NON-EMPTY prefix is deliberate: it makes a logical-vs-actual key mixup
    # observable (an empty prefix would hide it).
    monkeypatch.setenv("B2_BUCKET_NAME", "claimscene-live")
    monkeypatch.setenv("B2_S3_ENDPOINT", "https://s3.eu-central-003.backblazeb2.com")
    monkeypatch.setenv("B2_KEY_PREFIX", "cs")


def _durable_index_keys(s3: _FakeS3) -> set[str]:
    raw = s3.store[("claimscene-live", "cs/index.jsonl")]
    return {json.loads(line)["key"] for line in raw.decode("utf-8").splitlines() if line}


def test_concurrent_writers_union_by_key_not_last_writer_wins(b2_env):
    """Two writers with independent in-memory snapshots (e.g. a request
    thread and a background render-job thread — see
    ``claimscene.api._job_storage``) must UNION their rows at persist time,
    not clobber each other. Without merge-on-write, the second writer's put
    would overwrite the durable index with only ITS OWN rows, erasing the
    first writer's — exactly the failure mode Cinemory hit live before
    porting this fix."""
    s3 = _FakeS3()
    a = B2Storage(client=s3)  # both constructed BEFORE any write —
    b = B2Storage(client=s3)  # each starts from an empty index snapshot

    a.put("r/scene/aa/a1/scene.json", b"{}", content_type="application/json")
    # b never saw a's row in memory; without merge-on-write this put would
    # persist [b-row] alone, erasing a's row from the durable index.
    b.put("r/illustration/bb/b1/illustration.mp4", b"b1", content_type="video/mp4")
    # a is now the stale one (no b-row in memory); its next put must fold
    # b's row back in rather than clobbering it.
    a.put("r/manifest/cc/c1/manifest.json", b"{}", content_type="application/json")

    expected = {
        "r/scene/aa/a1/scene.json",
        "r/illustration/bb/b1/illustration.mp4",
        "r/manifest/cc/c1/manifest.json",
    }
    assert _durable_index_keys(s3) == expected
    # A fresh worker resolves EVERY writer's case artifacts (this is what
    # 404'd live in Cinemory before the fix).
    reader = B2Storage(client=s3)
    assert {r["key"] for r in reader.index} == expected


def test_reput_same_key_is_idempotent_in_index(b2_env):
    """Keys are content-addressed, so re-putting the same key (same bytes)
    must collapse to ONE index row — merge-by-key makes the write idempotent."""
    s3 = _FakeS3()
    store = B2Storage(client=s3)
    store.put("r/illustration/aa/bb/illustration.mp4", b"x", content_type="video/mp4")
    store.put("r/illustration/aa/bb/illustration.mp4", b"x", content_type="video/mp4")

    assert [r["key"] for r in store.index] == ["r/illustration/aa/bb/illustration.mp4"]
    assert len(store.index_jsonl().splitlines()) == 1


def test_corrupt_remote_index_line_never_fails_put_and_self_heals(b2_env):
    """Merge-on-write reads the remote index on EVERY put, so a corrupt or
    non-row line must be dropped (best-effort), never raise into ``put`` —
    and the healthy rows around it must survive the union. The subsequent
    persist rewrites a clean index (self-healing)."""
    s3 = _FakeS3()
    good = json.dumps({"key": "r/scene/aa/a1/scene.json", "size": 2,
                       "content_type": "application/json"})
    s3.store[("claimscene-live", "cs/index.jsonl")] = (
        f"{good}\nnot-json!!\n[1, 2, 3]\n".encode()
    )

    store = B2Storage(client=s3)  # constructor reload tolerates the bad lines
    store.put("r/illustration/bb/b1/illustration.mp4", b"b1", content_type="video/mp4")

    assert _durable_index_keys(s3) == {
        "r/scene/aa/a1/scene.json", "r/illustration/bb/b1/illustration.mp4",
    }
    # The durable index is clean NDJSON again (every line parses as a row).
    raw = s3.store[("claimscene-live", "cs/index.jsonl")].decode("utf-8")
    assert all(isinstance(json.loads(line), dict) for line in raw.splitlines() if line)
