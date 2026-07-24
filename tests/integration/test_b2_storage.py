"""Real B2 adapter code driven against an in-memory S3 seam (no network)."""
from __future__ import annotations

import json

import pytest

from claimscene.adapters.b2_storage import (
    B2Storage,
    build_b2_client,
    region_from_endpoint,
)
from claimscene.provenance import verify_detached_receipt


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


def test_detached_receipt_persisted_as_its_own_b2_object(b2_env, fake_s3):
    """A full case sealed onto the real B2 adapter writes the detached receipt
    as its own content-addressed object AND records it in the durable index, so
    a fresh worker resolves it and re-verifies it against the sealed manifest."""
    from claimscene.adapters.fakes import FakeMediaProvider, FakeVisionExtractor
    from claimscene.case import CasePhoto, CaseSpec, PhotoSource
    from claimscene.pipeline import CasePipeline
    from claimscene.schematic import PillowSchematicRenderer

    storage = B2Storage(client=fake_s3)
    result = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                          renderer=PillowSchematicRenderer(animate=False)).run(
        CaseSpec(case_id="b2case", photos=[
            CasePhoto(filename="p.png", data=b"\x89PNG-b2-scene",
                      source=PhotoSource.staged_demo)]))

    ref = result.artifacts["receipt"]
    assert ref.key.split("/")[1] == "receipt" and ref.key.endswith("/receipt.json")
    # A real object under the key prefix, mirrored in the durable index.jsonl.
    assert ("claimscene-test", f"cs/{ref.key}") in fake_s3.store
    assert ("claimscene-test", "cs/index.jsonl") in fake_s3.store

    reader = B2Storage(client=fake_s3)  # a fresh, scale-to-zero worker
    row = next((r for r in reader.index if r["key"] == ref.key), None)
    assert row is not None and row["content_type"] == "application/json"
    receipt = json.loads(reader.get(ref.key))
    assert verify_detached_receipt(receipt, result.manifest) is True


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


# ── presigned-URL correctness (Cinemory PR #17/#19 lesson) ───────────────────
@pytest.mark.parametrize("endpoint,region", [
    ("https://s3.eu-central-003.backblazeb2.com", "eu-central-003"),
    ("https://s3.us-west-004.backblazeb2.com", "us-west-004"),
    ("s3.eu-central-003.backblazeb2.com", "eu-central-003"),
    ("https://example.invalid/custom", None),
])
def test_region_derived_from_endpoint(endpoint, region):
    assert region_from_endpoint(endpoint) == region


def test_b2_client_signs_with_sigv4_and_region():
    """The real boto3 client MUST sign with SigV4 + an explicit region, or B2
    401s the presigned GET the playback routes redirect browsers to. No creds
    or network are needed to *construct* the client and read its config."""
    boto3 = pytest.importorskip("boto3")
    client = build_b2_client(
        endpoint_url="https://s3.eu-central-003.backblazeb2.com",
        region="eu-central-003", key_id="k", app_key="s")
    assert client.meta.region_name == "eu-central-003"
    assert client.meta.config.signature_version == "s3v4"
    # And a presigned URL is actually mintable (local signing, no network).
    url = client.generate_presigned_url(
        "get_object", Params={"Bucket": "b", "Key": "k"}, ExpiresIn=60)
    assert "X-Amz-Signature=" in url and "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert boto3  # import used


def test_b2_storage_region_resolution(clean_b2_env, monkeypatch, fake_s3):
    monkeypatch.setenv("B2_BUCKET_NAME", "b")
    monkeypatch.setenv("B2_S3_ENDPOINT", "https://s3.eu-central-003.backblazeb2.com")
    # Derived from the endpoint host when B2_REGION is unset.
    assert B2Storage(client=fake_s3).region == "eu-central-003"
    # Explicit override wins.
    monkeypatch.setenv("B2_REGION", "us-east-005")
    assert B2Storage(client=fake_s3).region == "us-east-005"
