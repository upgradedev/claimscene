"""Manifest sealing, per-input source attribution, and tamper evidence."""
from __future__ import annotations

import json

from claimscene.case import CasePhoto, PhotoSource
from claimscene.provenance import (
    DISCLOSURE,
    RECEIPT_SCHEMA,
    VerificationReceipt,
    build_detached_receipt,
    build_manifest,
    build_review,
    input_record,
    sha256_bytes,
    verify_artifact,
    verify_detached_receipt,
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


# ── detached, independently re-verifiable receipt ─────────────────────────────
def _receipt(passed: int = 3, total: int = 3, *, success: bool = True) -> VerificationReceipt:
    checks = [{"id": f"c{i}", "label": "L", "passed": i < passed, "evidence": "e"}
              for i in range(total)]
    return VerificationReceipt(checks=checks, success=success,
                               digest=sha256_bytes(b"receipt"))


def _manifest_with_review() -> dict:
    """A manifest that also seals an approval review (so the detached receipt
    cites a real, non-null decision digest)."""
    photo = CasePhoto(filename="p.png", data=b"photo-bytes",
                      source=PhotoSource.staged_demo)
    review = build_review(
        classification="interactive_demo",
        diff=[{"path": "vehicles.0.color", "proposed": "blue",
               "confirmed": "black", "changed": True}],
        counts={"human_changed": 1},
        reviewer_id="alex",
        scene_proposed_sha256=sha256_bytes(b"proposed"),
        scene_confirmed_sha256=sha256_bytes(b"scene"),
        prior_confidence_notes=["vlm unsure"],
    )
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
        review=review,
    )


def test_detached_receipt_carries_the_expected_cited_fields():
    manifest = _manifest_with_review()
    receipt = build_detached_receipt(manifest, _receipt(3, 3),
                                     created_at="2026-07-21T00:00:00+00:00")
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["case_id"] == "case-1"
    assert receipt["manifest_hash"] == manifest["manifest_hash"]
    assert receipt["disclosure"] == DISCLOSURE
    # It cites the Genblaze illustration output digest + the review decision digest.
    assert receipt["illustration"]["output_digest"] == manifest["illustration"]["sha256"]
    assert receipt["illustration"]["provider"] == "fake-media"
    assert receipt["illustration"]["degraded"] is True
    assert receipt["review"]["classification"] == "interactive_demo"
    assert receipt["review"]["decision_digest"] == manifest["review"]["decision_digest"]
    assert receipt["verify_summary"] == {"success": True, "check_count": 3, "passed": 3}
    assert len(receipt["receipt_digest"]) == 64


def test_detached_receipt_digest_recomputes():
    manifest = _manifest_with_review()
    receipt = build_detached_receipt(manifest, _receipt())
    assert verify_detached_receipt(receipt) is True
    # ...and binds to its source manifest when supplied.
    assert verify_detached_receipt(receipt, manifest) is True


def test_detached_receipt_verify_summary_reflects_a_failing_verification():
    manifest = _manifest_with_review()
    receipt = build_detached_receipt(manifest, _receipt(2, 3, success=False))
    assert receipt["verify_summary"] == {"success": False, "check_count": 3, "passed": 2}
    assert verify_detached_receipt(receipt) is True  # the receipt itself is still intact


def test_tampering_the_detached_receipt_flips_verification():
    manifest = _manifest_with_review()
    receipt = build_detached_receipt(manifest, _receipt())
    # Forge the cited generative output digest.
    forged = json.loads(json.dumps(receipt))
    forged["illustration"]["output_digest"] = "0" * 64
    assert verify_detached_receipt(forged) is False
    # Claim the whole case verified when it did not.
    lied = json.loads(json.dumps(receipt))
    lied["verify_summary"]["success"] = not lied["verify_summary"]["success"]
    assert verify_detached_receipt(lied) is False


def test_detached_receipt_without_a_digest_fails_closed():
    manifest = _manifest_with_review()
    receipt = build_detached_receipt(manifest, _receipt())
    stripped = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    assert verify_detached_receipt(stripped) is False
    assert verify_detached_receipt(None) is False  # not even a dict


def test_detached_receipt_rejected_when_bound_to_a_different_manifest():
    manifest = _manifest_with_review()
    receipt = build_detached_receipt(manifest, _receipt())
    # A pristine receipt still fails against a manifest it was not distilled from
    # (its cited manifest_hash / digests no longer match) — cannot be re-homed.
    other = _manifest()  # a different, review-less manifest
    assert verify_detached_receipt(receipt) is True
    assert verify_detached_receipt(receipt, other) is False


def test_detached_receipt_handles_a_manifest_missing_sections():
    """A sparse manifest degrades to null cited fields, never raises."""
    receipt = build_detached_receipt({}, _receipt())
    assert receipt["case_id"] is None
    assert receipt["illustration"]["output_digest"] is None
    assert receipt["review"]["decision_digest"] is None
    assert verify_detached_receipt(receipt) is True
