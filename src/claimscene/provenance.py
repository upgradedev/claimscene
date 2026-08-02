"""Sealed, self-disclosing provenance — ClaimScene's product thesis in code.

Every artifact is content-addressed by SHA-256. A case manifest records the
hash of every input photo *with its source attribution and license*, the
constrained scene graph, the deterministic timeline + schematic (the factual
layer), the generative illustration (provider/model/prompt, explicitly
flagged), and the report. The manifest is sealed with a canonical SHA-256
(``manifest_hash``) computed over its sorted-key JSON, so tampering with any
recorded field — including an input's claimed source — is detectable via
:func:`verify_manifest`.

Shared foundation with our other entry, Cinemory (MIT): canonical-JSON
sealing and hash chaining follow the same approach.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .case import CasePhoto
from .layout import TIMELINE_SCHEMA
from .scene import SCENE_SCHEMA

MANIFEST_SCHEMA = "claimscene/manifest/v1"
REVIEW_SCHEMA = "claimscene/review/v1"
RECEIPT_SCHEMA = "claimscene/receipt/v1"
DISCLOSURE = "AI-generated illustration — not evidence"
WATERMARK = "ILLUSTRATION — NOT EVIDENCE"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def input_record(photo: CasePhoto) -> dict:
    """Provenance row for one input photo: hash + role + source attribution."""
    return {
        "filename": photo.filename,
        "sha256": sha256_bytes(photo.data),
        "media_type": photo.media_type,
        "role": photo.role.value,
        "source": photo.source.value,
        "attribution": photo.attribution,
        "license": photo.license,
    }


def build_manifest(
    *,
    case_id: str,
    inputs: list[dict],
    scene_graph_sha256: str,
    timeline_sha256: str,
    schematic: dict,
    illustration: dict,
    report_sha256: str,
    created_at: str | None = None,
    review: dict | None = None,
) -> dict:
    """Assemble the case manifest and seal it with a canonical hash.

    ``review`` is the sealed AI→human approval/correction receipt (see
    :func:`build_review`). When present it is folded into the sealed body, so
    ``manifest_hash`` covers it too; when ``None`` the key is *omitted* (not
    sealed as ``null``) — keeping the manifest byte-shape back-compatible for
    callers that never supply a review.
    """
    body = {
        "schema": MANIFEST_SCHEMA,
        "case_id": case_id,
        "created_at": created_at or utc_now_iso(),
        "disclosure": DISCLOSURE,
        "inputs": inputs,
        "scene_graph": {"schema": SCENE_SCHEMA, "sha256": scene_graph_sha256},
        "timeline": {"schema": TIMELINE_SCHEMA, "sha256": timeline_sha256},
        "schematic": schematic,
        "illustration": illustration,
        "report": {"media_type": "text/markdown", "sha256": report_sha256},
    }
    if review is not None:
        body["review"] = review
    body["manifest_hash"] = sha256_bytes(canonical_json(body))
    return body


# ── sealed AI→human approval / correction receipt ────────────────────────────
def decision_digest(
    *,
    classification: str | None,
    diff: list[dict] | None,
    reviewer_id: str | None,
    scene_confirmed_sha256: str | None,
    scene_proposed_sha256: str | None,
) -> str:
    """Self-invalidating digest over exactly the five decision fields.

    Recomputable from the sealed proposed/confirmed hashes, the reviewer, the
    classification and the field diff — nothing else. If the confirmed scene
    later drifts (its sealed hash changes) or any field is rewritten, the digest
    no longer recomputes, voiding the approval. The diff's *list order* is part
    of the hashed bytes, so :func:`~claimscene.evaluation.diff_scenes` must be
    deterministic.
    """
    return sha256_bytes(canonical_json({
        "classification": classification,
        "diff": diff,
        "reviewer_id": reviewer_id,
        "scene_confirmed_sha256": scene_confirmed_sha256,
        "scene_proposed_sha256": scene_proposed_sha256,
    }))


def build_review(
    *,
    classification: str,
    diff: list[dict],
    counts: dict,
    reviewer_id: str | None,
    scene_proposed_sha256: str | None,
    scene_confirmed_sha256: str | None,
    prior_confidence_notes: list[str],
    reviewed_at: str | None = None,
) -> dict:
    """Assemble a sealed review receipt (the AI-proposed → human-confirmed delta).

    The server computes the ``diff`` and ``counts`` itself (never trusting a
    client-supplied delta). ``prior_confidence_notes`` carries the AI's real
    notes forward non-destructively (the confirmed scene's own notes are reset
    to the human-in-the-loop line elsewhere). ``classification`` is an honest,
    closed taxonomy — ``unverified_no_baseline`` with null hashes when no
    AI-proposed scene was supplied to diff against.
    """
    review = {
        "schema": REVIEW_SCHEMA,
        "classification": classification,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at or utc_now_iso(),
        "scene_proposed_sha256": scene_proposed_sha256,
        "scene_confirmed_sha256": scene_confirmed_sha256,
        "diff": diff,
        "counts": counts,
        "prior_confidence_notes": prior_confidence_notes,
    }
    review["decision_digest"] = decision_digest(
        classification=classification,
        diff=diff,
        reviewer_id=reviewer_id,
        scene_confirmed_sha256=scene_confirmed_sha256,
        scene_proposed_sha256=scene_proposed_sha256,
    )
    return review


def verify_manifest(manifest: dict) -> bool:
    """Recompute the canonical hash and confirm the manifest is intact."""
    claimed = manifest.get("manifest_hash")
    if not claimed:
        return False
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    return sha256_bytes(canonical_json(body)) == claimed


# Dotted paths into the manifest for per-artifact hash checks.
_ARTIFACT_PATHS = {
    "scene_graph": ("scene_graph", "sha256"),
    "timeline": ("timeline", "sha256"),
    "report": ("report", "sha256"),
    "illustration": ("illustration", "sha256"),
    "illustration_still": ("illustration", "still_sha256"),
    "schematic_svg": ("schematic", "static_svg_sha256"),
    "schematic_hero": ("schematic", "hero_png_sha256"),
    "schematic_animation": ("schematic", "animation_sha256"),
}


def recorded_sha256(manifest: dict, artifact: str) -> str | None:
    section, field = _ARTIFACT_PATHS[artifact]
    value = manifest.get(section, {}).get(field)
    return value if isinstance(value, str) else None


def verify_artifact(manifest: dict, artifact: str, data: bytes) -> bool:
    """Confirm ``data`` matches the hash the manifest recorded for ``artifact``."""
    recorded = recorded_sha256(manifest, artifact)
    return recorded is not None and recorded == sha256_bytes(data)


# ── aggregate named-check verification receipt ────────────────────────────────
#: Artifacts re-hashed from stored bytes, in a stable order. Each is checked
#: only when the manifest actually recorded a hash for it (so a manifest with a
#: PNG hero but no MP4 animation, offline, skips the animation check).
_VERIFY_ARTIFACTS = (
    ("scene_graph", "Scene-graph bytes match the sealed hash"),
    ("timeline", "Timeline bytes match the sealed hash"),
    ("schematic_svg", "Schematic SVG bytes match the sealed hash"),
    ("schematic_hero", "Schematic hero-frame bytes match the sealed hash"),
    ("schematic_animation", "Schematic animation bytes match the sealed hash"),
    ("illustration_still", "Illustration still bytes match the sealed hash"),
    ("illustration", "Illustration clip bytes match the sealed hash"),
    ("report", "Report bytes match the sealed hash"),
)


@dataclass
class VerificationReceipt:
    """The outcome of :func:`verify_all` — a named-check attestation.

    ``checks`` is a list of ``{id, label, passed, evidence}`` rows, ``success``
    is the AND of every check, and ``digest`` content-addresses the receipt
    itself. Always fully shaped — even a totally failing verification returns a
    well-formed receipt rather than raising.
    """

    checks: list[dict]
    success: bool
    digest: str

    def to_dict(self) -> dict:
        return {"checks": self.checks, "success": self.success, "digest": self.digest}


def verify_all(
    manifest: dict,
    fetch_bytes: Callable[[str], bytes | None],
) -> VerificationReceipt:
    """Re-verify a sealed case from stored bytes, as a named-check receipt.

    Every check is re-run from primary evidence (the stored artifact bytes and
    the sealed body), never a cached boolean:

      * ``seal.manifest_hash`` — the canonical seal recomputes;
      * ``artifact.<name>`` — each recorded artifact's stored bytes re-hash to
        its sealed hash;
      * ``structural.disclosure`` — the disclosure line is present and exact;
      * ``structural.input_attribution`` — every input row carries a source;
      * ``structural.schematic_watermark`` — the schematic SVG carries the
        NOT-EVIDENCE watermark;
      * ``structural.illustration_degraded`` — the ``degraded`` flag is
        internally consistent with the sealing provider;
      * ``structural.illustration_watermark_burned`` /
        ``structural.illustration_still_watermark_burned`` — the disclosure
        was burned into the clip's / still's pixels (not merely requested in
        the generation prompt); a live (non-degraded) illustration MUST have
        burned it in, or the check fails — closing the gap where a real
        render carried ``degraded: false`` but the prompt-only disclosure was
        silently ignored by the model;
      * ``structural.illustration_camera_move`` -- when the manifest records a
        camera-move outcome (``illustration.camera_move_applied``), it is an
        explicit boolean and carries an error only when it is ``False`` --
        unlike the watermark check above, ``True`` is never REQUIRED (the
        camera push is an honest best-effort layered over the always-required
        watermark, not a second hard requirement -- see ``camera.py``); a
        manifest sealed before this feature existed, or with no camera-move
        record at all, passes with nothing to re-verify;
      * ``review.decision_digest`` — the approval receipt recomputes from its
        sealed proposed/confirmed hashes and stays bound to the sealed scene.

    ``fetch_bytes(name)`` returns the stored bytes for an artifact (or ``None``
    if absent). It is only ever called inside a guarded check, so a raising or
    ``None``-returning fetcher degrades that one check to ``passed: false`` with
    the reason as evidence — the receipt is always returned intact.
    """
    if not isinstance(manifest, dict):
        manifest = {}
    checks: list[dict] = []

    def record(check_id: str, label: str,
               fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            passed, evidence = fn()
        except Exception as exc:  # a raising check is a failing check (fail-closed)
            passed, evidence = False, f"{type(exc).__name__}: {exc}"
        checks.append({"id": check_id, "label": label,
                       "passed": bool(passed), "evidence": evidence})

    def _seal() -> tuple[bool, str]:
        ok = verify_manifest(manifest)
        return ok, ("canonical SHA-256 over the sealed body matches manifest_hash"
                    if ok else "recomputed canonical SHA-256 does NOT match manifest_hash")

    record("seal.manifest_hash", "Manifest seal recomputes (SHA-256)", _seal)

    def _safe_recorded(name: str) -> str | None:
        try:
            return recorded_sha256(manifest, name)
        except Exception:
            return None

    for name, label in _VERIFY_ARTIFACTS:
        recorded = _safe_recorded(name)
        if recorded is None:
            continue  # not part of this manifest (e.g. no animation offline)

        def _artifact(name: str = name, recorded: str = recorded) -> tuple[bool, str]:
            data = fetch_bytes(name)
            if data is None:
                return False, f"stored bytes for {name!r} could not be fetched"
            actual = sha256_bytes(data)
            return actual == recorded, (
                f"re-hashed stored bytes {actual[:12]}… "
                + ("matches the sealed hash" if actual == recorded
                   else f"!= sealed {recorded[:12]}…"))

        record(f"artifact.{name}", label, _artifact)

    def _disclosure() -> tuple[bool, str]:
        ok = manifest.get("disclosure") == DISCLOSURE
        return ok, (f"disclosure == {DISCLOSURE!r}"
                    if ok else "disclosure line missing or altered")

    record("structural.disclosure", "Disclosure statement present + exact", _disclosure)

    def _inputs() -> tuple[bool, str]:
        rows = manifest.get("inputs") or []
        if not rows:
            return False, "manifest carries no input rows"
        missing = [i for i, r in enumerate(rows) if not (r or {}).get("source")]
        return (not missing), ("every input row carries a non-empty source"
                               if not missing
                               else f"input rows {missing} missing a source")

    record("structural.input_attribution", "Every input photo carries a source", _inputs)

    def _watermark() -> tuple[bool, str]:
        data = fetch_bytes("schematic_svg")
        if data is None:
            return False, "schematic SVG bytes could not be fetched"
        count = data.decode("utf-8", "replace").count(WATERMARK)
        return count >= 1, (f"schematic SVG carries {WATERMARK!r} ×{count}"
                            if count >= 1 else f"schematic SVG missing {WATERMARK!r}")

    record("structural.schematic_watermark",
           "Schematic carries the NOT-EVIDENCE watermark", _watermark)

    def _degraded() -> tuple[bool, str]:
        illustration = manifest.get("illustration") or {}
        provider = illustration.get("provider") or ""
        degraded = illustration.get("degraded")
        expected = provider.startswith("fake")
        return degraded == expected, (
            f"degraded={degraded} is consistent with provider {provider!r}"
            if degraded == expected
            else f"degraded={degraded} contradicts provider {provider!r}")

    record("structural.illustration_degraded",
           "Illustration degraded flag is internally consistent", _degraded)

    # The disclosure must be burned into the pixels, not merely requested in
    # the generation prompt (see watermark.py). Mirrors _watermark() above
    # for the schematic, but the illustration comes from a generative model
    # that can (and, on 2026-07-30, did) ignore the prompt instruction — so a
    # LIVE (non-degraded) illustration must have actually burned it in, or
    # this check fails. An offline/degraded illustration is exempt from
    # requiring True (burning synthetic fake bytes is not meaningful), but
    # must still carry an explicit, honestly-sealed boolean — never a silent
    # omission — and the disclosure text itself is always required exactly,
    # regardless of degraded state.
    for check_id, burned_field, error_field, label_word in (
        ("structural.illustration_watermark_burned",
         "watermark_burned", "watermark_error", "clip"),
        ("structural.illustration_still_watermark_burned",
         "still_watermark_burned", "still_watermark_error", "still"),
    ):
        def _illustration_watermark(burned_field: str = burned_field,
                                    error_field: str = error_field,
                                    label_word: str = label_word) -> tuple[bool, str]:
            illustration = manifest.get("illustration") or {}
            text = illustration.get("watermark_text")
            if text != DISCLOSURE:
                return False, (f"illustration.watermark_text missing or altered "
                               f"(expected {DISCLOSURE!r}, got {text!r})")
            burned = illustration.get(burned_field)
            if not isinstance(burned, bool):
                return False, f"illustration.{burned_field} missing from the sealed manifest"
            degraded = illustration.get("degraded")
            if degraded is False and burned is not True:
                error = illustration.get(error_field)
                return False, (f"live illustration {label_word} shipped without a "
                               f"burned-in disclosure (illustration.{error_field}={error!r})")
            return True, (f"illustration.{burned_field}={burned} (degraded={degraded}); "
                          "disclosure text sealed exactly")

        record(check_id, f"Illustration {label_word} disclosure burned into "
                         "pixels (or honestly flagged)", _illustration_watermark)

    def _camera_move() -> tuple[bool, str]:
        illustration = manifest.get("illustration") or {}
        applied = illustration.get("camera_move_applied")
        if applied is None:
            # Absence-tolerant by design: a manifest sealed before this
            # feature existed (or a hand-built manifest that never claims a
            # camera move at all) has nothing to re-verify here -- that is
            # not the same as a dishonest omission, unlike the watermark
            # fields above, which every manifest this project seals has
            # always carried.
            return True, "no camera-move record sealed (nothing to re-verify)"
        if not isinstance(applied, bool):
            return False, "illustration.camera_move_applied is not an explicit boolean"
        error = illustration.get("camera_move_error")
        if applied is False and not error:
            return False, ("camera_move_applied is False but no "
                           "camera_move_error was recorded")
        if applied is True and error is not None:
            return False, ("camera_move_applied is True but a "
                           f"camera_move_error is also recorded ({error!r})")
        return True, f"illustration.camera_move_applied={applied}, honestly recorded"

    record("structural.illustration_camera_move",
           "Camera-move outcome honestly recorded (success is never required)",
           _camera_move)

    def _review() -> tuple[bool, str]:
        review = manifest.get("review")
        if review is None:
            return True, "no review receipt sealed (nothing to re-verify)"
        recomputed = decision_digest(
            classification=review.get("classification"),
            diff=review.get("diff"),
            reviewer_id=review.get("reviewer_id"),
            scene_confirmed_sha256=review.get("scene_confirmed_sha256"),
            scene_proposed_sha256=review.get("scene_proposed_sha256"),
        )
        if recomputed != review.get("decision_digest"):
            return False, "decision_digest does NOT recompute from the sealed review fields"
        confirmed = review.get("scene_confirmed_sha256")
        if confirmed is not None and confirmed != _safe_recorded("scene_graph"):
            return False, ("review is bound to a different scene than the sealed "
                           "scene_graph (the confirmed scene drifted)")
        return True, "decision_digest recomputes; approval stays bound to the sealed scene"

    record("review.decision_digest",
           "Approval receipt recomputes from the sealed hashes", _review)

    success = all(c["passed"] for c in checks)
    digest = sha256_bytes(canonical_json({"checks": checks, "success": success}))
    return VerificationReceipt(checks=checks, success=success, digest=digest)


# ── detached, independently re-verifiable receipt (its own B2 object) ──────────
def build_detached_receipt(
    manifest: dict,
    receipt: VerificationReceipt,
    *,
    created_at: str | None = None,
) -> dict:
    """A compact, self-sealed receipt persisted as its OWN small object.

    Distilled from the sealed ``manifest`` + a :class:`VerificationReceipt`
    (the aggregate named-check outcome), it is a durable, portable attestation
    that lives *next to* the case artifacts rather than inside the manifest —
    so it can be indexed, fetched and re-verified on its own. It cites, by
    hash, exactly the two derived digests a downstream reader most wants to
    trust without re-fetching every byte:

      * the **Genblaze illustration output digest** (``illustration.sha256``);
      * the **review decision digest** (the sealed approval receipt's digest).

    Plus the ``manifest_hash`` it was distilled from and a one-line
    ``verify_summary`` (was the whole case green when sealed, and over how many
    checks). ``receipt_digest`` seals the body with the same canonical SHA-256
    as every other artifact, so the receipt is tamper-evident on its own and
    :func:`verify_detached_receipt` recomputes it. It never raises — a manifest
    missing a section degrades to ``None`` fields, not an exception.
    """
    illustration = manifest.get("illustration") or {}
    review = manifest.get("review") or {}
    body = {
        "schema": RECEIPT_SCHEMA,
        "case_id": manifest.get("case_id"),
        "manifest_hash": manifest.get("manifest_hash"),
        "disclosure": manifest.get("disclosure"),
        "illustration": {
            "provider": illustration.get("provider"),
            "model": illustration.get("model"),
            "degraded": illustration.get("degraded"),
            # The generative output the receipt binds to (the Genblaze clip).
            "output_digest": illustration.get("sha256"),
        },
        "review": {
            "classification": review.get("classification"),
            "decision_digest": review.get("decision_digest"),
        },
        "verify_summary": {
            "success": receipt.success,
            "check_count": len(receipt.checks),
            "passed": sum(1 for c in receipt.checks if c.get("passed")),
        },
        "created_at": created_at or utc_now_iso(),
    }
    body["receipt_digest"] = sha256_bytes(canonical_json(body))
    return body


def verify_detached_receipt(receipt: dict, manifest: dict | None = None) -> bool:
    """Re-verify a detached receipt from its own bytes (fail-closed).

    Always recomputes ``receipt_digest`` over the sealed body — any mutation of
    a cited digest, the verify summary, or any field flips it to ``False``. When
    the source ``manifest`` is supplied the citations are additionally bound to
    it: the ``manifest_hash``, the illustration ``output_digest`` and the review
    ``decision_digest`` must each still match the manifest, so a receipt lifted
    onto a different (or drifted) case is rejected too.
    """
    if not isinstance(receipt, dict):
        return False
    claimed = receipt.get("receipt_digest")
    if not claimed:
        return False
    body = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    if sha256_bytes(canonical_json(body)) != claimed:
        return False
    if manifest is None:
        return True
    illustration = manifest.get("illustration") or {}
    review = manifest.get("review") or {}
    receipt_ill = receipt.get("illustration") or {}
    receipt_rev = receipt.get("review") or {}
    return (
        receipt.get("manifest_hash") == manifest.get("manifest_hash")
        and receipt_ill.get("output_digest") == illustration.get("sha256")
        and receipt_rev.get("decision_digest") == review.get("decision_digest")
    )
