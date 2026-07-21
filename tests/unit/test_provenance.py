"""Manifest sealing, per-input source attribution, and tamper evidence."""
from __future__ import annotations

import json

from claimscene.case import CasePhoto, PhotoSource
from claimscene.provenance import (
    DISCLOSURE,
    build_manifest,
    input_record,
    sha256_bytes,
    verify_artifact,
    verify_manifest,
)


def _manifest() -> dict:
    photo = CasePhoto(filename="p.png", data=b"photo-bytes",
                      source=PhotoSource.licensed,
                      attribution="Jane Doe / StagedShots Ltd",
                      license="Commercial license #123")
    return build_manifest(
        case_id="case-1",
        inputs=[input_record(photo)],
        scene_graph_sha256=sha256_bytes(b"scene"),
        timeline_sha256=sha256_bytes(b"timeline"),
        schematic={"static_svg_sha256": sha256_bytes(b"svg"),
                   "hero_png_sha256": sha256_bytes(b"hero"),
                   "frame_count": 25, "fps": 8, "animation_sha256": None,
                   "watermark": "ILLUSTRATION — NOT EVIDENCE"},
        illustration={"provider": "fake-media", "model": "Kling-v3-I2V",
                      "prompt": "p", "sha256": sha256_bytes(b"clip"),
                      "degraded": True},
        report_sha256=sha256_bytes(b"report"),
        created_at="2026-07-21T00:00:00+00:00",
    )


def test_fresh_manifest_verifies():
    assert verify_manifest(_manifest()) is True


def test_missing_hash_fails():
    manifest = _manifest()
    del manifest["manifest_hash"]
    assert verify_manifest(manifest) is False


def test_tampering_any_field_detected():
    manifest = _manifest()
    tampered = json.loads(json.dumps(manifest))
    tampered["illustration"]["degraded"] = False  # claim the fake clip was real
    assert verify_manifest(tampered) is False


def test_tampering_input_source_detected():
    manifest = _manifest()
    tampered = json.loads(json.dumps(manifest))
    tampered["inputs"][0]["source"] = "user_upload"  # re-badge a licensed photo
    assert verify_manifest(tampered) is False


def test_input_record_carries_source_attribution_license():
    record = _manifest()["inputs"][0]
    assert record["source"] == "licensed"
    assert record["attribution"] == "Jane Doe / StagedShots Ltd"
    assert record["license"] == "Commercial license #123"
    assert len(record["sha256"]) == 64
    assert record["role"] == "scene_photo"


def test_disclosure_string_sealed_exactly():
    manifest = _manifest()
    assert manifest["disclosure"] == DISCLOSURE
    assert manifest["disclosure"] == "AI-generated illustration — not evidence"


def test_verify_artifact_matches_and_rejects():
    manifest = _manifest()
    assert verify_artifact(manifest, "scene_graph", b"scene") is True
    assert verify_artifact(manifest, "scene_graph", b"different") is False
    assert verify_artifact(manifest, "schematic_svg", b"svg") is True
    assert verify_artifact(manifest, "schematic_animation", b"whatever") is False


def test_deterministic_seal_for_fixed_created_at():
    assert _manifest()["manifest_hash"] == _manifest()["manifest_hash"]
