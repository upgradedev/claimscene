"""End-to-end: the sealed AI→human approval receipt + GET /cases/{id}/verify.

Drives the real FastAPI app in-process (offline, no creds): render seals a
server-computed review receipt, the classification honesty-gate downgrades a
demo click, and the verify endpoint re-checks the case from stored bytes and
flips the matching check when a stored artifact drifts.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

import claimscene.api as api  # noqa: E402
from claimscene.evaluation import diff_scenes  # noqa: E402
from claimscene.provenance import (  # noqa: E402
    canonical_json,
    sha256_bytes,
    verify_detached_receipt,
)
from claimscene.scene import scene_from_json  # noqa: E402

client = TestClient(api.app)


def _proposed() -> dict:
    return client.post("/cases/extract",
                       data={"scenario_id": "s02_left_cross"}).json()["scene"]


def _render(scene: dict, **extra) -> dict:
    data = {"scene": json.dumps(scene), **extra}
    res = client.post("/cases/render", data=data)
    assert res.status_code == 200, res.text
    return res.json()


def _manifest(body: dict) -> dict:
    return json.loads(client.get(body["manifest_url"]).content)


# ── the review receipt is sealed + server-computed ───────────────────────────
def test_render_with_proposed_scene_seals_a_server_computed_receipt():
    proposed = _proposed()
    confirmed = json.loads(json.dumps(proposed))
    confirmed["vehicles"][0]["color"] = "black"   # the human's correction
    body = _render(confirmed, proposed_scene=json.dumps(proposed),
                   reviewer_id="alex", review_classification="interactive_demo",
                   scenario_id="s02_left_cross")
    review = _manifest(body)["review"]

    assert review["schema"] == "claimscene/review/v1"
    assert review["reviewer_id"] == "alex"
    assert review["classification"] == "interactive_demo"
    # The sealed diff is the SERVER's own computation (no client diff channel).
    expected = diff_scenes(scene_from_json(json.dumps(proposed)),
                           scene_from_json(json.dumps(body["scene"])))
    assert review["diff"] == expected
    colour = next(r for r in review["diff"] if r["path"].endswith(".color")
                  and r["changed"])
    assert colour["confirmed"] == "black"
    assert review["counts"]["human_changed"] >= 1


def test_a_client_supplied_diff_form_field_is_ignored():
    """There is no diff input — a fabricated ``diff`` form field is inert; the
    sealed diff still reflects proposed vs confirmed only."""
    proposed = _proposed()
    confirmed = json.loads(json.dumps(proposed))
    confirmed["road"]["signal"] = "stop_sign"
    body = _render(confirmed, proposed_scene=json.dumps(proposed),
                   diff=json.dumps([{"path": "hacked", "changed": False}]),
                   scenario_id="s02_left_cross")
    review = _manifest(body)["review"]
    assert all(r["path"] != "hacked" for r in review["diff"])
    signal = next(r for r in review["diff"] if r["path"] == "road.signal")
    assert signal["changed"] is True and signal["confirmed"] == "stop_sign"


def test_render_without_proposed_scene_seals_unverified_no_baseline():
    body = _render(_proposed(), scenario_id="s02_left_cross")
    review = _manifest(body)["review"]
    assert review["classification"] == "unverified_no_baseline"
    assert review["scene_proposed_sha256"] is None
    assert review["scene_confirmed_sha256"] is None
    assert review["diff"] == []


def test_ai_notes_are_carried_into_the_receipt_not_discarded():
    """The confirmed scene's notes are reset to the human-in-the-loop line, but
    the AI's real notes are captured into the receipt beforehand."""
    scene = _proposed()
    scene["confidence_notes"] = ["vlm: unsure whether the van is grey or green"]
    body = _render(scene, scenario_id="s02_left_cross")
    # Confirmed scene's sealed notes are the server line only (unchanged behaviour).
    assert body["warnings"] == [
        "scene reviewed and confirmed by a human operator before rendering "
        "(human-in-the-loop)"]
    # ...but the AI note survives in the review receipt.
    review = _manifest(body)["review"]
    assert "vlm: unsure whether the van is grey or green" in review["prior_confidence_notes"]


# ── the classification honesty gate ──────────────────────────────────────────
def test_authenticated_human_is_downgraded_on_the_unauthenticated_demo():
    proposed = _proposed()
    body = _render(proposed, proposed_scene=json.dumps(proposed),
                   review_classification="authenticated_human",
                   scenario_id="s02_left_cross")
    # A demo click can NEVER be sealed as an authenticated approval.
    assert _manifest(body)["review"]["classification"] == "interactive_demo"


def test_out_of_taxonomy_classification_is_422():
    proposed = _proposed()
    res = client.post("/cases/render", data={
        "scene": json.dumps(proposed), "review_classification": "notary_certified"})
    assert res.status_code == 422
    assert "review_classification" in json.dumps(res.json())


def test_server_only_classification_cannot_be_claimed_by_a_client():
    proposed = _proposed()
    res = client.post("/cases/render", data={
        "scene": json.dumps(proposed), "review_classification": "unverified_no_baseline"})
    assert res.status_code == 422
    assert "server-set only" in json.dumps(res.json())


# ── GET /cases/{id}/verify — server-side re-verification ──────────────────────
def test_verify_endpoint_passes_on_a_freshly_sealed_case():
    proposed = _proposed()
    body = _render(proposed, proposed_scene=json.dumps(proposed),
                   scenario_id="s02_left_cross")
    receipt = client.get(f"/cases/{body['case_id']}/verify")
    assert receipt.status_code == 200
    data = receipt.json()
    assert data["success"] is True
    assert all(c["passed"] for c in data["checks"])
    assert {"seal.manifest_hash", "review.decision_digest",
            "structural.schematic_watermark"} <= {c["id"] for c in data["checks"]}


def test_verify_endpoint_flips_a_check_when_stored_bytes_drift():
    body = _render(_proposed(), scenario_id="s02_left_cross")
    case_id = body["case_id"]
    # Simulate a stored artifact drifting under the seal (a B2 object rewrite).
    key = next(r["key"] for r in api._storage.index
               if case_id in r["key"] and r["key"].endswith("/report.md"))
    api._storage._objects[key] = b"a rewritten report body"
    data = client.get(f"/cases/{case_id}/verify").json()
    report = next(c for c in data["checks"] if c["id"] == "artifact.report")
    assert report["passed"] is False
    assert data["success"] is False


def test_verify_unknown_case_is_404_not_500():
    assert client.get("/cases/does-not-exist/verify").status_code == 404


def test_verify_degrades_honestly_on_unreadable_manifest_bytes():
    """If the stored manifest bytes are unreadable, verify returns a fully
    shaped failing receipt — never a 500."""
    body = _render(_proposed(), scenario_id="s02_left_cross")
    case_id = body["case_id"]
    key = next(r["key"] for r in api._storage.index
               if case_id in r["key"] and r["key"].endswith("/manifest.json"))
    api._storage._objects[key] = b"{ not valid json at all"
    res = client.get(f"/cases/{case_id}/verify")
    assert res.status_code == 200
    assert res.json()["success"] is False


def test_render_still_verifiable_end_to_end_after_all_the_above():
    """The manifest seal itself stays intact across the new review sealing."""
    from claimscene.provenance import verify_manifest

    body = _render(_proposed(), scenario_id="s02_left_cross")
    assert verify_manifest(_manifest(body)) is True


# ── GET /cases/{id}/receipt — the detached, self-sealed receipt ───────────────
def test_render_persists_a_detached_receipt_object_in_the_index():
    proposed = _proposed()
    body = _render(proposed, proposed_scene=json.dumps(proposed),
                   scenario_id="s02_left_cross")
    case_id = body["case_id"]
    # Stored as its own content-addressed object under the receipt kind.
    keys = [r["key"] for r in api._storage.index
            if case_id in r["key"] and r["key"].endswith("/receipt.json")]
    assert len(keys) == 1
    assert keys[0].split("/")[1] == "receipt"


def test_receipt_endpoint_returns_raw_reverifiable_bytes():
    proposed = _proposed()
    body = _render(proposed, proposed_scene=json.dumps(proposed),
                   reviewer_id="sam", scenario_id="s02_left_cross")
    case_id = body["case_id"]
    res = client.get(f"/cases/{case_id}/receipt")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    receipt = res.json()
    # Raw canonical bytes: the digest recomputes over exactly what was served.
    served_body = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    assert sha256_bytes(canonical_json(served_body)) == receipt["receipt_digest"]
    # Independently re-verifiable, and bound to this case's sealed manifest.
    manifest = _manifest(body)
    assert verify_detached_receipt(receipt) is True
    assert verify_detached_receipt(receipt, manifest) is True
    # It cites the two derived digests it promises to.
    assert receipt["illustration"]["output_digest"] == manifest["illustration"]["sha256"]
    assert receipt["review"]["decision_digest"] == manifest["review"]["decision_digest"]
    assert receipt["verify_summary"]["success"] is True


def test_receipt_bytes_drift_is_independently_detected():
    """A rewritten stored receipt object no longer re-verifies — the detached
    attestation catches tampering on its own, without the manifest."""
    body = _render(_proposed(), scenario_id="s02_left_cross")
    case_id = body["case_id"]
    receipt = client.get(f"/cases/{case_id}/receipt").json()
    assert verify_detached_receipt(receipt) is True
    receipt["illustration"]["output_digest"] = "0" * 64  # forge the cited output
    assert verify_detached_receipt(receipt) is False


def test_receipt_unknown_case_is_404():
    assert client.get("/cases/does-not-exist/receipt").status_code == 404
