"""PEN-TEST — Sensitive-data exposure.

Threat: credentials/keys leak into an API response, an error body, a log line, or
the sealed manifest — including on the offline-degrade path (live mode, missing
creds). The invariant: a seeded sentinel credential value appears in NONE of
those surfaces.
"""
from __future__ import annotations

import json
import logging
import os

from claimscene.adapters import FakeMediaProvider, FakeVisionExtractor, InMemoryStorage
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.pipeline import CasePipeline
from claimscene.schematic import PillowSchematicRenderer

from .conftest import PNG_1x1, fresh_client

_SENTINEL = "SENTINEL-SECRET-DO-NOT-LEAK-abc123"
_SEEDED_VARS = ("B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY", "B2_KEY_ID",
                "B2_APP_KEY")


def test_health_does_not_expose_credentials(client):
    body = client.get("/health").json()
    blob = json.dumps(body).lower()
    assert _SENTINEL.lower() not in blob
    # /health surfaces only mode + backend identities, never secret material.
    assert set(body) >= {"status", "service", "mode", "provider", "extractor",
                         "storage"}
    assert "b2_" not in blob and "gmi" not in blob and "secret" not in blob


def test_manifest_contains_no_credentials():
    storage = InMemoryStorage(bucket="nocreds")
    result = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                          renderer=PillowSchematicRenderer(animate=False)).run(
        CaseSpec(case_id="nocreds", photos=[CasePhoto(
            filename="p.png", data=PNG_1x1, source=PhotoSource.staged_demo)]))
    blob = json.dumps(result.manifest).lower()
    for banned in ("secret", "password", "b2_application_key", "b2_app_key",
                   "aws_secret", "gmi_api_key", "api_key"):
        assert banned not in blob


def test_offline_degrade_path_leaks_no_seeded_secret(caplog, scrub_credentials):
    """Live mode with a *partial/misconfigured* credential set (key material
    present but bucket/endpoint absent → the B2 backend is not ready) must
    degrade to the offline fakes and leak the seeded secret NOWHERE — not in the
    response body, not in ``/health``, not in logs (even at DEBUG). Covers
    'offline-degrade path leaks nothing' + 'no creds in logs' at once.

    The provider/extractor are left unconfigured, so every backend falls back to
    fakes regardless of which optional SDKs are installed — and the seeded secret
    is nonetheless present in the process environment throughout.
    """
    for var in _SEEDED_VARS:
        os.environ[var] = _SENTINEL
    client, restore = fresh_client(mode="live")
    try:
        with caplog.at_level(logging.DEBUG):
            scene = client.post("/cases/extract",
                                data={"scenario_id": "s01_rear_end"}).json()["scene"]
            r = client.post("/cases/render",
                            data={"scene": json.dumps(scene),
                                  "scenario_id": "s01_rear_end"})
            health = client.get("/health").json()
        assert r.status_code == 200                       # never 500s
        assert _SENTINEL not in r.text
        assert _SENTINEL not in json.dumps(health)
        assert _SENTINEL not in caplog.text               # not logged, even DEBUG
        # It genuinely degraded (proves we exercised the no-usable-creds path).
        assert health["storage"] == "InMemoryStorage"
        assert health["provider"] == "fake-media"
        assert health["extractor"] == "fake-vision"
    finally:
        for var in _SEEDED_VARS:
            os.environ.pop(var, None)
        restore()


def test_error_response_does_not_echo_internal_paths_or_secrets(client):
    """A 422 body carries a user-facing validation error, not a stack trace or a
    secret."""
    r = client.post("/cases/preview-schematic",
                    json={"road": {"layout": "not-a-real-layout"}})
    assert r.status_code == 422
    assert _SENTINEL not in r.text
    assert "Traceback" not in r.text and "site-packages" not in r.text
