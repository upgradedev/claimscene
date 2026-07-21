"""Real B2 adapter code driven against an in-memory S3 seam (no network)."""
from __future__ import annotations

import json

import pytest

from claimscene.adapters.b2_storage import B2Storage


@pytest.fixture
def b2_env(clean_b2_env, monkeypatch):
    monkeypatch.setenv("B2_BUCKET_NAME", "claimscene-test")
    monkeypatch.setenv("B2_S3_ENDPOINT", "https://s3.eu-central-003.backblazeb2.com")
    monkeypatch.setenv("B2_KEY_PREFIX", "cs")


def test_put_writes_object_and_durable_index(b2_env, fake_s3):
    storage = B2Storage(client=fake_s3)
    key = "case-1/manifests/ab/abcd/manifest.json"
    url = storage.put(key, b'{"manifest_hash": "cafe"}',
                      content_type="application/json")
    # Object written under the key prefix; logical key kept in the index.
    assert ("claimscene-test", f"cs/{key}") in fake_s3.store
    assert storage.index[0]["key"] == key
    # index.jsonl persisted durably in the bucket itself.
    assert ("claimscene-test", "cs/index.jsonl") in fake_s3.store
    assert url.endswith(f"cs/{key}")


def test_fresh_instance_inherits_durable_catalogue(b2_env, fake_s3):
    writer = B2Storage(client=fake_s3)
    writer.put("case-2/reports/cd/cdef/report.md", b"# report",
               content_type="text/markdown")
    reader = B2Storage(client=fake_s3)  # a brand-new worker
    match = next((row for row in reader.index
                  if row["key"].startswith("case-2/reports/")), None)
    assert match is not None
    assert reader.get(match["key"]) == b"# report"


def test_get_round_trips_bytes(b2_env, fake_s3):
    storage = B2Storage(client=fake_s3)
    storage.put("k/a/b/c/x.bin", b"\x00\x01\x02")
    assert storage.get("k/a/b/c/x.bin") == b"\x00\x01\x02"


def test_reload_index_survives_missing_index(b2_env, fake_s3):
    storage = B2Storage(client=fake_s3)
    assert storage.index == []  # first run: no index yet, no crash
    assert storage.reload_index() == []


def test_index_jsonl_serialisation(b2_env, fake_s3):
    storage = B2Storage(client=fake_s3)
    storage.put("a/b/c/d/e.json", b"{}", content_type="application/json")
    rows = [json.loads(line) for line in storage.index_jsonl().splitlines()]
    assert rows == [{"content_type": "application/json", "key": "a/b/c/d/e.json",
                     "size": 2}]


def test_missing_bucket_configuration_raises(clean_b2_env, fake_s3):
    with pytest.raises(RuntimeError, match="B2 bucket not configured"):
        B2Storage(client=fake_s3)


def test_missing_endpoint_configuration_raises(clean_b2_env, monkeypatch, fake_s3):
    monkeypatch.setenv("B2_BUCKET_NAME", "b")
    with pytest.raises(RuntimeError, match="endpoint not configured"):
        B2Storage(client=fake_s3)


def test_no_prefix_writes_at_bucket_root(clean_b2_env, monkeypatch, fake_s3):
    monkeypatch.setenv("B2_BUCKET_NAME", "claimscene-test")
    monkeypatch.setenv("B2_S3_ENDPOINT", "https://s3.eu-central-003.backblazeb2.com")
    storage = B2Storage(client=fake_s3)
    storage.put("x/y/z/w/f.bin", b"1")
    assert ("claimscene-test", "x/y/z/w/f.bin") in fake_s3.store
    assert ("claimscene-test", "index.jsonl") in fake_s3.store
