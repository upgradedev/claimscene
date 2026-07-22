"""End-to-end API tests over the whole review-adjust chain (offline, no creds).

Every route is exercised in-process through FastAPI's TestClient: the
zero-photo scenario path, the extract → preview → render → get → verify chain,
honest-degrade on a live-provider failure, playback, and the security
invariants (path sanitisation, constrained-vocabulary 422s, no client text
sealed as system-derived).
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")  # python-multipart, needed for the form routes

from fastapi.testclient import TestClient  # noqa: E402

import claimscene.api as api  # noqa: E402
from claimscene.provenance import canonical_json, sha256_bytes, verify_manifest  # noqa: E402

client = TestClient(api.app)


# ── health + scenarios ───────────────────────────────────────────────────────
def test_health_reports_effective_backends():
    body = client.get("/health").json()
    assert body["service"] == "claimscene-api"
    assert body["mode"] == "offline"
    # Effective (resolved) backends, not just the configured mode.
    assert body["provider"] == "fake-media"
    assert body["extractor"] == "fake-vision"
    assert body["storage"] == "InMemoryStorage"


def test_scenarios_are_listed_for_zero_photo_demo():
    scenarios = client.get("/scenarios").json()["scenarios"]
    assert len(scenarios) >= 6
    first = scenarios[0]
    assert first["id"] and first["title"] and first["context"]
    assert first["thumbnail"].startswith(f"/scenarios/{first['id']}/images/")
    assert len(first["images"]) == 3


def test_scenario_image_serves_bytes():
    scenarios = client.get("/scenarios").json()["scenarios"]
    res = client.get(scenarios[0]["thumbnail"])
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/")
    assert len(res.content) > 100


@pytest.mark.parametrize("bad", [
    "/scenarios/s01_rear_end/images/..%2f..%2fmanifest.json",
    "/scenarios/..%2f..%2fetc/images/view_a.jpg",
    "/scenarios/s01_rear_end/images/nope.jpg",
    "/scenarios/ghost/images/view_a.jpg",
])
def test_scenario_image_rejects_traversal_and_unknowns(bad):
    assert client.get(bad).status_code == 404


# ── extract ──────────────────────────────────────────────────────────────────
def test_extract_from_scenario_is_honest_ground_truth_offline():
    body = client.post("/cases/extract", data={"scenario_id": "s01_rear_end"}).json()
    # Offline sample returns the shipped truth, labelled as such — NOT dressed
    # up as an extraction (the product is judged on this honesty).
    assert body["extraction"]["source"] == "committed_ground_truth"
    assert len(body["scene"]["vehicles"]) == 2
    note = " ".join(body["scene"]["confidence_notes"]).lower()
    assert "offline sample" in note and "vlm" in note
    # Per-input provenance sealed honestly as synthetic (never a user upload).
    assert all(r["source"] == "synthetic_generated" for r in body["inputs"])
    assert len(body["inputs"]) == 3


def test_extract_from_uploaded_photos_runs_configured_extractor():
    files = [("files", ("scene.png", api._PNG_1x1 + b"a", "image/png")),
             ("files", ("damage.png", api._PNG_1x1 + b"b", "image/png"))]
    body = client.post("/cases/extract", files=files,
                       data={"roles": "scene_photo,damage_photo"}).json()
    assert body["extraction"]["source"] == "fake_extraction"
    assert body["scene"]["vehicles"]
    sources = {r["source"] for r in body["inputs"]}
    assert sources == {"user_upload"}
    assert body["inputs"][1]["role"] == "damage_photo"


def test_extract_requires_input():
    assert client.post("/cases/extract", data={}).status_code == 400


# ── preview (the live review-adjust feedback loop) ───────────────────────────
def _scene() -> dict:
    return client.post("/cases/extract", data={"scenario_id": "s02_left_cross"}).json()["scene"]


def test_preview_renders_static_schematic():
    res = client.post("/cases/preview-schematic", json=_scene())
    assert res.status_code == 200
    body = res.json()
    assert body["svg"].startswith("<svg") and body["svg"].rstrip().endswith("</svg>")
    assert "ILLUSTRATION" in body["svg"]  # watermark on the factual layer
    assert body["vehicle_count"] == 2
    assert body["contact_point"] is not None


def test_preview_reflects_edits_live():
    """Editing the scene changes the schematic — the review-adjust wiring."""
    scene = _scene()
    a = client.post("/cases/preview-schematic", json=scene).json()["svg"]
    scene["road"]["signal"] = "stop_sign"
    scene["vehicles"][0]["color"] = "red"
    b = client.post("/cases/preview-schematic", json=scene).json()["svg"]
    assert a != b  # the edit propagated into the deterministic factual layer


def test_preview_rejects_hallucinated_vocabulary():
    scene = _scene()
    scene["vehicles"] = [{"id": "v", "kind": "hovercraft", "color": "red"}]
    assert client.post("/cases/preview-schematic", json=scene).status_code == 422


def test_preview_ignores_client_supplied_notes():
    scene = _scene()
    scene["confidence_notes"] = ["INJECTED CLIENT NOTE"]
    body = client.post("/cases/preview-schematic", json=scene).json()
    assert "INJECTED CLIENT NOTE" not in " ".join(body["warnings"])


# ── render → get → verify (the sealed case) ──────────────────────────────────
def _render(case_id: str = "case", **extra) -> dict:
    scene = _scene()
    data = {"scene": json.dumps(scene), "case_id": case_id, **extra}
    res = client.post("/cases/render", data=data)
    assert res.status_code == 200, res.text
    return res.json()


def test_render_seals_a_verifiable_case():
    body = _render(scenario_id="s02_left_cross")
    assert body["degraded"] is True  # offline illustration is deterministic bytes
    assert body["manifest_hash"]
    # The reviewed-scene notes are reset server-side — no client text sealed.
    assert body["warnings"] == [
        "scene reviewed and confirmed by a human operator before rendering "
        "(human-in-the-loop)"
    ]
    # Fetch the RAW sealed manifest and verify the seal exactly (python mirror
    # of the in-browser verify).
    raw = client.get(body["manifest_url"]).content
    manifest = json.loads(raw)
    assert verify_manifest(manifest)
    assert sha256_bytes(canonical_json({k: v for k, v in manifest.items()
                                        if k != "manifest_hash"})) == manifest["manifest_hash"]
    # The served bytes ARE the canonical bytes (what the browser re-hashes).
    assert raw == canonical_json(manifest)
    assert manifest["inputs"][0]["source"] == "synthetic_generated"


def test_render_case_id_is_sanitised_and_unique():
    a = _render(case_id="../../evil path", scenario_id="s01_rear_end")["case_id"]
    b = _render(case_id="../../evil path", scenario_id="s01_rear_end")["case_id"]
    assert a != b  # unique per render → unambiguous lookup
    assert ".." not in a and "/" not in a and " " not in a
    # Every stored key for the case is content-addressed + traversal-free.
    prefix = a.split("-")[0]
    keys = [r["key"] for r in api._storage.index if r["key"].startswith(prefix)]
    assert keys
    for key in keys:
        segs = key.split("/")
        assert ".." not in segs and not any(s.startswith(".") for s in segs)
        assert any(len(s) == 64 for s in segs)  # SHA-256 anchor survives


def test_render_with_no_inputs_still_seals():
    """A pure type-a-scene flow (no scenario, no files) still seals honestly."""
    body = _render()  # no scenario_id, no files
    manifest = json.loads(client.get(body["manifest_url"]).content)
    assert verify_manifest(manifest)
    assert manifest["inputs"][0]["source"] == "staged_demo"


def test_get_unknown_case_404():
    assert client.get("/cases/does-not-exist").status_code == 404


# ── playback ─────────────────────────────────────────────────────────────────
def test_schematic_playback_streams_offline():
    body = _render(scenario_id="s03_parking_reverse")
    res = client.get(body["schematic_url"])
    assert res.status_code == 200
    # mp4 when ffmpeg is present (live/container), else the real hero PNG —
    # the factual layer is genuine in every mode.
    assert res.headers["content-type"] in ("video/mp4", "image/png")
    assert len(res.content) > 100


def test_illustration_playback_streams_offline():
    body = _render(scenario_id="s03_parking_reverse")
    res = client.get(body["illustration_url"])
    assert res.status_code == 200
    assert len(res.content) > 0


def test_playback_unknown_case_404():
    assert client.get("/cases/ghost/schematic").status_code == 404
    assert client.get("/cases/ghost/illustration").status_code == 404


# ── honest degrade on a live-provider failure ────────────────────────────────
def test_render_degrades_honestly_when_live_provider_fails(monkeypatch):
    class _FailingProvider:
        name = "boom-live"  # not "fake…" → the degrade path engages

        def generate(self, **_kwargs):
            raise RuntimeError("simulated live media failure")

    monkeypatch.setattr(api.config, "build_provider", lambda: _FailingProvider())
    body = _render(scenario_id="s04_roundabout_sideswipe")
    # Never 500s because a remote backend misbehaved; the response is honest.
    assert body["provider_degraded"] is True
    assert body["degraded"] is True
    assert body["degrade_reason"] == "RuntimeError"
    # The fallback still sealed a verifiable manifest against the same storage.
    assert verify_manifest(json.loads(client.get(body["manifest_url"]).content))


def test_render_propagates_a_genuine_offline_bug(monkeypatch):
    """An OFFLINE provider failure is a real bug and must NOT be masked."""
    from claimscene.adapters import FakeMediaProvider

    class _BrokenFake(FakeMediaProvider):
        def generate(self, **_kwargs):
            raise RuntimeError("genuine offline bug")

    monkeypatch.setattr(api.config, "build_provider", lambda: _BrokenFake())
    # A separate client that surfaces server exceptions as 500 (rather than
    # re-raising them into the test) — the real deployment returns 500 here.
    strict = TestClient(api.app, raise_server_exceptions=False)
    res = strict.post("/cases/render",
                      data={"scene": json.dumps(_scene()), "case_id": "bug"})
    assert res.status_code == 500
