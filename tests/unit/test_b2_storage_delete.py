"""Unit tests for B2Storage.delete — added additively for multitenancy's
``DELETE /me/data``. Drives the adapter through a small in-memory
S3-compatible stub (no boto3/creds needed in CI). Ported from Cinemory's
identical test file for its own multitenancy PR.

This file defines its OWN local stub with ``delete_object`` rather than using
the shared ``fake_s3`` fixture in ``tests/conftest.py`` (that fixture predates
DELETE support) — mirrors ``tests/integration/test_b2_storage.py``'s own
locally-scoped ``b2_env`` fixture pattern; nothing shared is modified.
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
    """Minimal in-memory S3 stub (put/get/delete) shared across instances."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.store[(Bucket, Key)] = Body
        return {}

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.store:
            raise KeyError(Key)
        return {"Body": _Body(self.store[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        # Real S3/B2 DeleteObject does not error on a missing key — mirrored
        # here (pop with a default) so the stub matches production semantics.
        self.store.pop((Bucket, Key), None)
        return {}


@pytest.fixture
def b2_env(clean_b2_env, monkeypatch):
    # A non-empty prefix is deliberate — see test_b2_storage_merge_on_write.py's
    # own fixture for why (makes a logical-vs-actual key mixup observable).
    monkeypatch.setenv("B2_BUCKET_NAME", "claimscene-live")
    monkeypatch.setenv("B2_S3_ENDPOINT", "https://s3.eu-central-003.backblazeb2.com")
    monkeypatch.setenv("B2_KEY_PREFIX", "cs")


def _durable_index_keys(s3: _FakeS3) -> set[str]:
    raw = s3.store[("claimscene-live", "cs/index.jsonl")]
    return {json.loads(line)["key"] for line in raw.decode("utf-8").splitlines() if line}


def test_delete_removes_the_prefixed_object_and_the_logical_index_row(b2_env):
    s3 = _FakeS3()
    store = B2Storage(client=s3)
    key = "case1/illustration/aa/bb/illustration.mp4"
    store.put(key, b"video-bytes", content_type="video/mp4")
    assert ("claimscene-live", f"cs/{key}") in s3.store

    store.delete(key)

    assert ("claimscene-live", f"cs/{key}") not in s3.store
    assert store.index == []
    assert _durable_index_keys(s3) == set()


def test_delete_only_removes_the_matching_key_from_the_durable_index(b2_env):
    s3 = _FakeS3()
    store = B2Storage(client=s3)
    store.put("r/scene/aa/a1/scene.json", b"{}", content_type="application/json")
    store.put("r/illustration/bb/b1/illustration.mp4", b"22", content_type="video/mp4")

    store.delete("r/scene/aa/a1/scene.json")

    assert [r["key"] for r in store.index] == ["r/illustration/bb/b1/illustration.mp4"]
    assert _durable_index_keys(s3) == {"r/illustration/bb/b1/illustration.mp4"}


def test_delete_does_not_resurrect_the_row_via_persist_indexs_remote_reread(b2_env):
    """Regression test for the resurrection bug a naive delete would have:
    ``_persist_index``'s merge-on-write re-reads the bucket's ``index.jsonl``
    BEFORE merging — and that re-read is, by construction, the pre-delete
    snapshot (nothing has rewritten it yet). A plain ``dict.update()`` union
    can only ADD/overwrite keys, never remove one absent from the update
    source — so without explicitly telling ``_persist_index`` which key to
    drop from that stale remote snapshot (the ``removed=`` parameter), the
    very act of persisting a delete would silently write the deleted row
    right back into the durable index."""
    s3 = _FakeS3()
    store = B2Storage(client=s3)
    key = "r/illustration/dd/d1/illustration.mp4"
    store.put(key, b"x", content_type="video/mp4")
    assert _durable_index_keys(s3) == {key}

    store.delete(key)

    assert _durable_index_keys(s3) == set(), "delete resurrected its own row"
    assert store.index == []

    # A fresh instance (e.g. a new Cloud Run worker) must also see the
    # deletion, not a resurrected row — the bucket, not any one Python
    # object, is the source of truth.
    reader = B2Storage(client=s3)
    assert reader.index == []


def test_delete_of_a_missing_key_is_a_no_op_not_an_error(b2_env):
    s3 = _FakeS3()
    store = B2Storage(client=s3)
    store.delete("never-existed")  # must not raise
    assert store.index == []


def test_delete_is_idempotent(b2_env):
    s3 = _FakeS3()
    store = B2Storage(client=s3)
    store.put("k", b"v")
    store.delete("k")
    store.delete("k")  # second call: still a clean no-op
    assert store.index == []
