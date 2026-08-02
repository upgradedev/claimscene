"""Sealed approval receipt + aggregate named-check verification (verify_all).

Covers the review block sealing into ``manifest_hash``, the self-invalidating
``decision_digest``, and ``verify_all`` — a pass on a good case, a targeted
tamper flipping the matching check, and the fail-closed branches (a raising /
``None`` fetcher, garbage manifest, unverified + review-less manifests).
"""
from __future__ import annotations

import json

from claimscene.adapters import FakeMediaProvider, InMemoryStorage
from claimscene.case import CasePhoto, CaseSpec, PhotoSource, ReviewClassification
from claimscene.evaluation import diff_scenes, review_counts
from claimscene.pipeline import CasePipeline
from claimscene.provenance import (
    DISCLOSURE,
    WATERMARK,
    build_manifest,
    build_review,
    canonical_json,
    decision_digest,
    sha256_bytes,
    verify_all,
    verify_manifest,
)
from claimscene.scene import (
    DamageZone,
    Impact,
    Movement,
    Road,
    SceneGraph,
    Vehicle,
    VehicleColor,
)
from claimscene.schematic import PillowSchematicRenderer


# ── scene + sealing helpers ──────────────────────────────────────────────────
def _scene() -> SceneGraph:
    return SceneGraph(
        road=Road(layout="x_intersection", lanes_per_direction=1, signal="traffic_light"),
        vehicles=[
            Vehicle(id="veh_a", kind="car", color="silver",
                    damage=[DamageZone(clock_position=2, severity="crush")]),
            Vehicle(id="veh_b", kind="van", color="green"),
        ],
        movements=[
            Movement(vehicle_id="veh_a", approach="N", maneuver="left_turn", speed_band="low"),
            Movement(vehicle_id="veh_b", approach="S", maneuver="straight", speed_band="moderate"),
        ],
        impacts=[Impact(vehicle_id="veh_a", clock_position=2)],
    )


class _Fixed:
    """A VisionExtractor that returns a fixed (confirmed) scene — no model."""

    name = "fixed"

    def __init__(self, scene: SceneGraph) -> None:
        self._scene = scene

    def extract(self, photos, *, context=None) -> SceneGraph:
        return self._scene.model_copy(deep=True)


def _sealed(*, proposed: SceneGraph | None, reviewer_id="reviewer-7",
            classification=ReviewClassification.interactive_demo):
    """Seal a case through the real pipeline; returns (storage, result)."""
    confirmed = _scene()
    if proposed is not None:
        confirmed = proposed.model_copy(deep=True)
        confirmed.vehicles[0].color = VehicleColor.red  # one human edit
    storage = InMemoryStorage(bucket="review-test")
    spec = CaseSpec(
        case_id="rev",
        photos=[CasePhoto(filename="p.png", data=b"scene-bytes",
                          source=PhotoSource.staged_demo)],
        proposed_scene=proposed, reviewer_id=reviewer_id,
        review_classification=classification,
        prior_confidence_notes=["ai note: low confidence on the colour"],
    )
    result = CasePipeline(_Fixed(confirmed), FakeMediaProvider(), storage,
                          renderer=PillowSchematicRenderer(animate=False)).run(spec)
    return storage, result


def _fetcher(storage, result):
    def fetch(name):
        ref = result.artifacts.get(name)
        return storage.get(ref.key) if ref is not None else None
    return fetch


def _check(receipt, check_id):
    return next(c for c in receipt.checks if c["id"] == check_id)


# ── review block is sealed + server-computed ─────────────────────────────────
def test_review_block_sealed_and_covered_by_manifest_hash():
    _storage, result = _sealed(proposed=_scene())
    review = result.manifest["review"]
    assert review["schema"] == "claimscene/review/v1"
    assert review["classification"] == "interactive_demo"
    assert review["reviewer_id"] == "reviewer-7"
    # Confirmed hash IS the sealed scene_graph hash (self-invalidating binding).
    assert review["scene_confirmed_sha256"] == result.manifest["scene_graph"]["sha256"]
    # The AI's real notes were carried forward, not discarded.
    assert review["prior_confidence_notes"] == ["ai note: low confidence on the colour"]
    # The review block is inside the seal: touching it breaks manifest_hash.
    tampered = json.loads(json.dumps(result.manifest))
    tampered["review"]["classification"] = "authenticated_human"
    assert verify_manifest(tampered) is False


def test_server_computes_the_diff_itself():
    """The sealed diff is the server's own computation over proposed vs
    confirmed — there is no client-supplied diff channel to lie through."""
    proposed = _scene()
    _storage, result = _sealed(proposed=proposed)
    confirmed_scene = result.scene
    expected = diff_scenes(proposed, confirmed_scene)
    assert result.manifest["review"]["diff"] == expected
    assert result.manifest["review"]["counts"] == review_counts(expected)
    colour = next(r for r in result.manifest["review"]["diff"]
                  if r["path"] == "vehicles.veh_a.color")
    assert colour["changed"] is True and colour["confirmed"] == "red"


def test_absent_proposed_scene_seals_unverified_no_baseline():
    _storage, result = _sealed(proposed=None)
    review = result.manifest["review"]
    assert review["classification"] == "unverified_no_baseline"
    assert review["scene_proposed_sha256"] is None
    assert review["scene_confirmed_sha256"] is None
    assert review["diff"] == []
    # Honest, not fabricated — and still self-consistent (digest recomputes).
    assert verify_manifest(result.manifest) is True


# ── decision_digest self-invalidation ────────────────────────────────────────
def _review_kwargs():
    return dict(classification="interactive_demo", diff=[{"path": "road.signal",
                "proposed": "none", "confirmed": "stop_sign", "changed": True,
                "changed_by": "human"}], counts={"proposed_fields": 1,
                "human_changed": 1, "unchanged": 0}, reviewer_id="r1",
                scene_proposed_sha256="a" * 64, scene_confirmed_sha256="b" * 64,
                prior_confidence_notes=["note"])


def test_build_review_seals_a_recomputable_digest():
    review = build_review(**_review_kwargs())
    assert review["decision_digest"] == decision_digest(
        classification=review["classification"], diff=review["diff"],
        reviewer_id=review["reviewer_id"],
        scene_confirmed_sha256=review["scene_confirmed_sha256"],
        scene_proposed_sha256=review["scene_proposed_sha256"])


def test_digest_binds_exactly_the_five_decision_fields():
    base = build_review(**_review_kwargs())["decision_digest"]
    # Each of the five sealed fields changes the digest.
    assert build_review(**{**_review_kwargs(), "reviewer_id": "r2"})["decision_digest"] != base
    assert build_review(**{**_review_kwargs(),
                           "scene_confirmed_sha256": "c" * 64})["decision_digest"] != base
    assert build_review(**{**_review_kwargs(),
                           "scene_proposed_sha256": "d" * 64})["decision_digest"] != base
    assert build_review(**{**_review_kwargs(),
                           "classification": "authenticated_human"})["decision_digest"] != base
    assert build_review(**{**_review_kwargs(), "diff": []})["decision_digest"] != base
    # Fields OUTSIDE the digest (reviewed_at, counts, prior notes) do not.
    assert build_review(**{**_review_kwargs(),
                           "reviewed_at": "2000-01-01T00:00:00+00:00"})["decision_digest"] == base
    assert build_review(**{**_review_kwargs(),
                           "prior_confidence_notes": []})["decision_digest"] == base


def test_build_manifest_omits_review_when_none():
    manifest = build_manifest(
        case_id="c", inputs=[], scene_graph_sha256=sha256_bytes(b"s"),
        timeline_sha256=sha256_bytes(b"t"),
        schematic={"static_svg_sha256": sha256_bytes(b"svg"),
                   "hero_png_sha256": sha256_bytes(b"h"), "frame_count": 2, "fps": 8,
                   "animation_sha256": None, "watermark": WATERMARK},
        illustration={"provider": "fake-media", "model": "m", "prompt": "p",
                      "sha256": sha256_bytes(b"c"), "degraded": True},
        report_sha256=sha256_bytes(b"r"), created_at="2026-07-23T00:00:00+00:00")
    assert "review" not in manifest              # omitted, not sealed as null
    assert verify_manifest(manifest) is True


# ── verify_all: happy path ───────────────────────────────────────────────────
def test_verify_all_passes_on_a_good_sealed_case():
    storage, result = _sealed(proposed=_scene())
    receipt = verify_all(result.manifest, _fetcher(storage, result))
    assert receipt.success is True
    assert all(c["passed"] for c in receipt.checks)
    ids = {c["id"] for c in receipt.checks}
    assert {"seal.manifest_hash", "artifact.scene_graph", "artifact.report",
            "structural.disclosure", "structural.input_attribution",
            "structural.schematic_watermark", "structural.illustration_degraded",
            "review.decision_digest"} <= ids
    # The receipt content-addresses itself.
    assert receipt.digest == sha256_bytes(canonical_json(
        {"checks": receipt.checks, "success": True}))


# ── verify_all: targeted tamper flips the matching check ──────────────────────
def test_mutating_artifact_bytes_flips_that_artifact_check():
    storage, result = _sealed(proposed=_scene())
    good = _fetcher(storage, result)

    def tampered(name):
        return b"swapped-report-bytes" if name == "report" else good(name)

    receipt = verify_all(result.manifest, tampered)
    assert _check(receipt, "artifact.report")["passed"] is False
    assert _check(receipt, "artifact.scene_graph")["passed"] is True
    assert receipt.success is False


def test_drifting_the_confirmed_scene_flips_the_review_check():
    storage, result = _sealed(proposed=_scene())
    drifted = json.loads(json.dumps(result.manifest))
    # Re-point the sealed scene_graph hash: the review's confirmed binding no
    # longer matches even though the review block itself is untouched.
    drifted["scene_graph"]["sha256"] = "0" * 64
    receipt = verify_all(drifted, _fetcher(storage, result))
    review = _check(receipt, "review.decision_digest")
    assert review["passed"] is False and "drift" in review["evidence"].lower()


def test_rewriting_a_sealed_review_field_flips_the_review_check():
    storage, result = _sealed(proposed=_scene())
    forged = json.loads(json.dumps(result.manifest))
    forged["review"]["reviewer_id"] = "somebody-else"  # a digest field
    receipt = verify_all(forged, _fetcher(storage, result))
    assert _check(receipt, "review.decision_digest")["passed"] is False


def test_stripping_the_watermark_flips_the_watermark_check():
    storage, result = _sealed(proposed=_scene())
    good = _fetcher(storage, result)

    def stripped(name):
        data = good(name)
        if name == "schematic_svg":
            return data.replace(WATERMARK.encode(), b"") if data else data
        return data

    receipt = verify_all(result.manifest, stripped)
    assert _check(receipt, "structural.schematic_watermark")["passed"] is False


def test_blanking_an_attribution_flips_the_input_check():
    storage, result = _sealed(proposed=_scene())
    blanked = json.loads(json.dumps(result.manifest))
    blanked["inputs"][0]["source"] = ""
    receipt = verify_all(blanked, _fetcher(storage, result))
    assert _check(receipt, "structural.input_attribution")["passed"] is False


def test_inconsistent_degraded_flag_flips_its_check():
    storage, result = _sealed(proposed=_scene())
    lied = json.loads(json.dumps(result.manifest))
    lied["illustration"]["degraded"] = False  # claim the fake clip was a real render
    receipt = verify_all(lied, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_degraded")["passed"] is False


# ── verify_all: fail-closed branches (never raises, always fully shaped) ──────
def test_verify_all_never_raises_on_garbage_manifest():
    for junk in (None, [], "nope", {}):
        receipt = verify_all(junk, lambda _name: None)
        assert receipt.success is False
        assert receipt.checks and all("passed" in c for c in receipt.checks)


def test_a_raising_fetcher_degrades_only_the_fetch_checks():
    _storage, result = _sealed(proposed=_scene())

    def boom(_name):
        raise RuntimeError("storage exploded")

    receipt = verify_all(result.manifest, boom)
    art = _check(receipt, "artifact.scene_graph")
    assert art["passed"] is False and "RuntimeError" in art["evidence"]
    # Seal + structural-manifest checks that need no bytes still pass.
    assert _check(receipt, "seal.manifest_hash")["passed"] is True
    assert _check(receipt, "structural.disclosure")["passed"] is True


def test_a_none_fetcher_fails_the_artifact_and_watermark_checks():
    _storage, result = _sealed(proposed=_scene())
    receipt = verify_all(result.manifest, lambda _name: None)
    assert _check(receipt, "artifact.report")["passed"] is False
    assert _check(receipt, "structural.schematic_watermark")["passed"] is False
    assert "could not be fetched" in _check(receipt, "artifact.report")["evidence"]


def test_unverified_review_block_verifies_and_binding_is_skipped():
    storage, result = _sealed(proposed=None)  # unverified_no_baseline, null hashes
    receipt = verify_all(result.manifest, _fetcher(storage, result))
    assert _check(receipt, "review.decision_digest")["passed"] is True
    assert receipt.success is True


def test_manifest_without_review_block_passes_the_review_check():
    manifest = build_manifest(
        case_id="c", inputs=[{"source": "staged_demo"}],
        scene_graph_sha256=sha256_bytes(b"s"), timeline_sha256=sha256_bytes(b"t"),
        schematic={"static_svg_sha256": sha256_bytes(b"svg"),
                   "hero_png_sha256": sha256_bytes(b"h"), "frame_count": 2, "fps": 8,
                   "animation_sha256": None, "watermark": WATERMARK},
        illustration={"provider": "fake-media", "model": "m", "prompt": "p",
                      "sha256": sha256_bytes(b"c"), "degraded": True},
        report_sha256=sha256_bytes(b"r"), created_at="2026-07-23T00:00:00+00:00")
    receipt = verify_all(manifest, lambda name: {
        "scene_graph": b"s", "timeline": b"t", "schematic_svg": b"svg",
        "schematic_hero": b"h", "illustration": b"c", "report": b"r"}.get(name))
    review = _check(receipt, "review.decision_digest")
    assert review["passed"] is True and "no review" in review["evidence"].lower()


def test_disclosure_is_the_exact_sealed_line():
    _storage, result = _sealed(proposed=_scene())
    assert result.manifest["disclosure"] == DISCLOSURE


def test_verify_all_tolerates_a_malformed_artifact_section():
    """A section that isn't a dict makes ``recorded_sha256`` raise; the guarded
    lookup swallows it so the artifact check is skipped, not a crash — the
    receipt is still fully shaped and honest (seal fails)."""
    manifest = {"scene_graph": "not-a-dict", "disclosure": DISCLOSURE}
    receipt = verify_all(manifest, lambda _name: None)
    assert receipt.success is False
    assert all("passed" in c for c in receipt.checks)
    # The unresolved scene_graph section produced no artifact.scene_graph check.
    assert "artifact.scene_graph" not in {c["id"] for c in receipt.checks}


# ── verify_all: illustration watermark burn-in (see watermark.py) ────────────
def test_illustration_watermark_checks_present_and_pass_when_degraded():
    """The offline/fake illustration is exempt from requiring burned=True
    (burning synthetic non-media bytes is not meaningful), but the checks
    still run and pass — this is what keeps every other fake-provider-backed
    test in this suite (and the readiness gate) green with no special-casing
    for the offline path."""
    storage, result = _sealed(proposed=_scene())
    receipt = verify_all(result.manifest, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_watermark_burned")["passed"] is True
    assert _check(receipt, "structural.illustration_still_watermark_burned")["passed"] is True


def test_live_illustration_without_a_burned_watermark_fails_the_check():
    """The exact production gap this closes: a live (non-degraded)
    illustration that shipped without a burned-in disclosure must fail
    verify_all — case ``live-forensic-retry-0bb32eeb``, 2026-07-30,
    ``degraded: false``, the disclosure absent from every sampled frame."""
    storage, result = _sealed(proposed=_scene())
    lied = json.loads(json.dumps(result.manifest))
    lied["illustration"]["degraded"] = False  # claim it was a real render
    # watermark_burned is already False here (the fake provider's synthetic
    # bytes cannot be burned) — now that combination is dishonest.
    assert lied["illustration"]["watermark_burned"] is False
    receipt = verify_all(lied, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_watermark_burned")["passed"] is False
    assert receipt.success is False


def test_illustration_watermark_text_absent_fails_both_checks_even_when_degraded():
    """The disclosure text itself must always be sealed exactly, regardless of
    degraded state — a manifest missing the marker entirely (an old shape, or
    a stripped field) cannot pass just by virtue of being the offline
    fallback."""
    storage, result = _sealed(proposed=_scene())
    stripped = json.loads(json.dumps(result.manifest))
    del stripped["illustration"]["watermark_text"]
    receipt = verify_all(stripped, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_watermark_burned")["passed"] is False
    assert _check(receipt, "structural.illustration_still_watermark_burned")["passed"] is False


def test_illustration_watermark_burned_flag_missing_fails_the_check():
    """A silently omitted flag (as opposed to an honest explicit False) must
    fail — the receipt can never treat "absent" the same as "sealed, and
    happens to be false"."""
    storage, result = _sealed(proposed=_scene())
    stripped = json.loads(json.dumps(result.manifest))
    del stripped["illustration"]["watermark_burned"]
    receipt = verify_all(stripped, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_watermark_burned")["passed"] is False
    # The sibling (still) check is independent and unaffected.
    assert _check(receipt, "structural.illustration_still_watermark_burned")["passed"] is True


# ── verify_all: illustration camera move (see camera.py) ────────────────────
def test_camera_move_check_present_and_honest_when_degraded():
    """The offline/fake illustration's clip is never real decodable video,
    so ``camera_move_applied`` is honestly False here -- the check does not
    require True (unlike the watermark check), only that the outcome is
    honestly recorded, so this still passes with no special-casing for the
    offline path."""
    storage, result = _sealed(proposed=_scene())
    assert result.manifest["illustration"]["camera_move_applied"] is False
    assert result.manifest["illustration"]["camera_move_error"]
    receipt = verify_all(result.manifest, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_camera_move")["passed"] is True


def test_camera_move_check_is_absence_tolerant():
    """A manifest sealed before this feature existed (no ``camera_move_*``
    fields at all -- the same minimal shape ``build_manifest`` accepts
    elsewhere in this file) has nothing to re-verify here: passes, not a
    failure."""
    manifest = build_manifest(
        case_id="c", inputs=[{"source": "staged_demo"}],
        scene_graph_sha256=sha256_bytes(b"s"), timeline_sha256=sha256_bytes(b"t"),
        schematic={"static_svg_sha256": sha256_bytes(b"svg"),
                   "hero_png_sha256": sha256_bytes(b"h"), "frame_count": 2, "fps": 8,
                   "animation_sha256": None, "watermark": WATERMARK},
        illustration={"provider": "fake-media", "model": "m", "prompt": "p",
                      "sha256": sha256_bytes(b"c"), "degraded": True},
        report_sha256=sha256_bytes(b"r"), created_at="2026-07-23T00:00:00+00:00")
    receipt = verify_all(manifest, lambda name: {
        "scene_graph": b"s", "timeline": b"t", "schematic_svg": b"svg",
        "schematic_hero": b"h", "illustration": b"c", "report": b"r"}.get(name))
    check = _check(receipt, "structural.illustration_camera_move")
    assert check["passed"] is True
    assert "no camera-move record" in check["evidence"].lower()


def test_camera_move_check_never_requires_success_even_when_live():
    """Unlike the watermark check, a live (non-degraded) render is allowed
    to have honestly failed its camera push -- the fail-safe is intentional
    design (see camera.py), not a gap this check needs to close."""
    storage, result = _sealed(proposed=_scene())
    live_but_honestly_failed = json.loads(json.dumps(result.manifest))
    live_but_honestly_failed["illustration"]["degraded"] = False
    live_but_honestly_failed["illustration"]["camera_move_applied"] = False
    live_but_honestly_failed["illustration"]["camera_move_error"] = "ffmpeg not on PATH"
    receipt = verify_all(live_but_honestly_failed, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_camera_move")["passed"] is True


def test_camera_move_check_flips_when_applied_false_has_no_error():
    storage, result = _sealed(proposed=_scene())
    lied = json.loads(json.dumps(result.manifest))
    lied["illustration"]["camera_move_applied"] = False
    lied["illustration"]["camera_move_error"] = None
    receipt = verify_all(lied, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_camera_move")["passed"] is False
    assert receipt.success is False


def test_camera_move_check_flips_when_applied_true_also_carries_an_error():
    storage, result = _sealed(proposed=_scene())
    contradictory = json.loads(json.dumps(result.manifest))
    contradictory["illustration"]["camera_move_applied"] = True
    contradictory["illustration"]["camera_move_error"] = "should not coexist with applied=True"
    receipt = verify_all(contradictory, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_camera_move")["passed"] is False


def test_camera_move_check_flips_when_applied_is_not_a_boolean():
    storage, result = _sealed(proposed=_scene())
    wrong_type = json.loads(json.dumps(result.manifest))
    wrong_type["illustration"]["camera_move_applied"] = "yes"
    receipt = verify_all(wrong_type, _fetcher(storage, result))
    assert _check(receipt, "structural.illustration_camera_move")["passed"] is False
