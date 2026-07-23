"""PEN-TEST — Provenance integrity (ClaimScene's core security claim).

ClaimScene's promise is a tamper-evident, SHA-256-sealed case manifest: the hash
of every input photo *with its claimed source attribution*, the constrained
scene graph, the deterministic factual layer (timeline + schematic), the
disclosed generative illustration, and the report — sealed by a canonical
SHA-256 over the sorted-key JSON body. This suite is the adversarial view of that
promise: every attempt to forge, mutate, strip, or shadow the seal MUST be
detected (fail closed). It extends the tamper-evidence unit tests into an
attacker frame.
"""
from __future__ import annotations

import json

from claimscene.adapters import FakeMediaProvider, FakeVisionExtractor, InMemoryStorage
from claimscene.case import CasePhoto, CaseSpec, PhotoSource
from claimscene.pipeline import CasePipeline
from claimscene.provenance import sha256_bytes, verify_artifact, verify_manifest
from claimscene.schematic import PillowSchematicRenderer


def _sealed():
    storage = InMemoryStorage(bucket="pentest")
    result = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                          renderer=PillowSchematicRenderer(animate=False)).run(
        CaseSpec(case_id="prov", photos=[
            CasePhoto(filename="scene.png", data=b"\x89PNG-scene-bytes",
                      source=PhotoSource.staged_demo),
            CasePhoto(filename="road.png", data=b"\x89PNG-road-bytes",
                      source=PhotoSource.public_domain,
                      attribution="Municipal survey archive", license="CC0-1.0")]))
    return storage, result


def test_fresh_manifest_verifies():
    _, result = _sealed()
    assert verify_manifest(result.manifest) is True


def test_mutating_any_recorded_hash_is_detected():
    _, result = _sealed()
    tampered = json.loads(json.dumps(result.manifest))
    tampered["report"]["sha256"] = "0" * 64
    assert verify_manifest(tampered) is False


def test_rebadging_an_input_source_is_detected():
    """The product's honesty claim under attack: re-badging a public-domain input
    as a user upload (or vice versa) must break the seal — a source claim cannot
    be silently rewritten."""
    _, result = _sealed()
    tampered = json.loads(json.dumps(result.manifest))
    assert tampered["inputs"][1]["source"] == "public_domain"
    tampered["inputs"][1]["source"] = "user_upload"
    assert verify_manifest(tampered) is False


def test_mutating_the_illustration_prompt_is_detected():
    _, result = _sealed()
    tampered = json.loads(json.dumps(result.manifest))
    tampered["illustration"]["prompt"] = "attacker-rewritten prompt"
    assert verify_manifest(tampered) is False


def test_forged_seal_over_tampered_body_is_still_rejected():
    """An attacker who mutates a field and forges a plausible 64-hex seal cannot
    win: the seal is a function of the full body, not attacker-assertable."""
    _, result = _sealed()
    forged = json.loads(json.dumps(result.manifest))
    forged["case_id"] = "hijacked"
    forged["manifest_hash"] = "f" * 64
    assert verify_manifest(forged) is False


def test_stripping_the_seal_fails_closed():
    _, result = _sealed()
    stripped = {k: v for k, v in result.manifest.items() if k != "manifest_hash"}
    assert verify_manifest(stripped) is False
    result.manifest["manifest_hash"] = ""
    assert verify_manifest(result.manifest) is False


def test_shadowing_the_manifest_with_an_extra_field_is_detected():
    """Appending an unsealed top-level field changes the canonical body, so the
    recorded seal no longer matches — a shadow field cannot slip in silently."""
    _, result = _sealed()
    shadowed = json.loads(json.dumps(result.manifest))
    shadowed["injected"] = "attacker-added"
    assert verify_manifest(shadowed) is False


def test_swapped_artifact_bytes_fail_artifact_verification():
    _, result = _sealed()
    assert verify_artifact(result.manifest, "report", b"totally-different") is False


def test_stored_artifact_bytes_match_the_sealed_hash():
    storage, result = _sealed()
    for name in ("scene_graph", "report", "illustration", "schematic_svg",
                 "schematic_hero"):
        data = storage.get(result.artifacts[name].key)
        assert verify_artifact(result.manifest, name, data) is True
        assert sha256_bytes(data) == result.artifacts[name].sha256
