"""PEN-TEST — AuthZ / abuse / resource exhaustion.

Threat: a caller sends oversized, over-count, or malformed uploads to crash the
worker (5xx), exhaust memory, or bypass the ingest guardrails. Both upload
surfaces (``/cases/extract`` and ``/cases/render``) must reject every one of
these at the boundary with a 4xx and never a 5xx / OOM.
"""
from __future__ import annotations

import json

from claimscene.ingest import MAX_PHOTO_BYTES, MAX_PHOTOS

from .conftest import PNG_1x1


def _files(n: int, data: bytes = PNG_1x1) -> list:
    return [("files", (f"p{i}.png", data, "image/png")) for i in range(n)]


def _scene(client) -> dict:
    return client.post("/cases/extract",
                       data={"scenario_id": "s01_rear_end"}).json()["scene"]


def test_over_max_photos_extract_is_rejected_4xx(client):
    """One over the file-count cap is a clean 413, not an OOM / 5xx."""
    r = client.post("/cases/extract", files=_files(MAX_PHOTOS + 1))
    assert 400 <= r.status_code < 500
    assert MAX_PHOTOS < 1000  # the guardrail is what bounds it


def test_over_max_photos_render_is_rejected_4xx(client):
    """Same count cap enforced on the render upload surface."""
    scene = _scene(client)
    r = client.post("/cases/render",
                    data={"scene": json.dumps(scene), "case_id": "abuse-max"},
                    files=_files(MAX_PHOTOS + 1))
    assert 400 <= r.status_code < 500


def test_oversized_single_file_extract_is_rejected_4xx(client):
    """A single file over the per-file byte cap is a clean 413 (PNG magic first,
    so it is the SIZE guard that rejects it, not the magic-byte guard)."""
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_PHOTO_BYTES + 1)
    r = client.post("/cases/extract",
                    files=[("files", ("big.png", big, "image/png"))])
    assert 400 <= r.status_code < 500


def test_oversized_single_file_render_is_rejected_4xx(client):
    scene = _scene(client)
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_PHOTO_BYTES + 1)
    r = client.post("/cases/render",
                    data={"scene": json.dumps(scene), "case_id": "abuse-big"},
                    files=[("files", ("big.png", big, "image/png"))])
    assert 400 <= r.status_code < 500


def test_malformed_extract_no_input_is_4xx_not_5xx(client):
    """No files and no scenario_id → a clean 400 (provide input), never a 500."""
    r = client.post("/cases/extract", data={})
    assert 400 <= r.status_code < 500


def test_empty_photo_bytes_is_rejected_4xx(client):
    """An empty file part is a clean 400 (not a crash downstream)."""
    r = client.post("/cases/extract",
                    files=[("files", ("p.png", b"", "image/png"))])
    assert 400 <= r.status_code < 500


def test_missing_scene_on_render_is_4xx_not_5xx(client):
    """Render without the required ``scene`` form field is a 422, never a 500."""
    r = client.post("/cases/render", data={"case_id": "no-scene"})
    assert 400 <= r.status_code < 500


def test_within_limits_upload_is_accepted(client):
    """Positive control: the guard is not over-broad — a normal small upload
    (two valid PNGs, well under both caps) still succeeds with a 200."""
    r = client.post("/cases/extract", files=_files(2))
    assert r.status_code == 200
    assert r.json()["scene"]["vehicles"]
