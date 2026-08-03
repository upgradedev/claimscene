"""A sealed case outlives the tab that made it, and a live failure says why.

Three behaviours, driven through the real FastAPI app offline:

* ``GET /cases/{id}/result`` rebuilds a sealed case into the SAME body a fresh
  render returns, so a reloaded page shows the case it lost. Unknown ids,
  half-stored cases and another tenant's case all answer with the identical
  404 — a shared link must not confirm that someone else's case exists.
* A live-provider failure carries a plain, closed-vocabulary ``degrade_kind``
  into both the response and the sealed manifest, while the upstream error
  text stays server-side.
* Completed renders are timed, so ``GET /cases/render/estimate`` can answer
  "how long will this take" with a measurement instead of a guess.
"""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

import claimscene.api as api  # noqa: E402
from claimscene import jobs  # noqa: E402
from claimscene.adapters import InMemoryStorage  # noqa: E402

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch):
    """A fresh private store per test (same reasoning as test_render_jobs)."""
    monkeypatch.setattr(api, "_storage", InMemoryStorage())


def _scene(scenario_id: str = "s02_left_cross") -> dict:
    return client.post("/cases/extract", data={"scenario_id": scenario_id}).json()["scene"]


def _render(case_id: str, headers: dict | None = None) -> dict:
    r = client.post("/cases/render", headers=headers, data={
        "scene": json.dumps(_scene()), "case_id": case_id,
        "scenario_id": "s02_left_cross"})
    assert r.status_code == 200, r.text
    return r.json()


class _BrokenLiveProvider:
    """A provider that looks live (its name is not ``fake-*``) and fails the
    way the real one did the day the shared account ran out of credits."""

    name = "genblaze"

    def generate(self, **_kwargs) -> bytes:
        raise RuntimeError(
            "GMICloud submit failed (402): Insufficient credits for "
            "account acct_1234 at https://api.gmicloud.example/v1/video")


# ── resume a sealed case ─────────────────────────────────────────────────────
def test_result_route_rebuilds_the_render_body_from_stored_bytes():
    fresh = _render("resume-roundtrip")
    r = client.get(f"/cases/{fresh['case_id']}/result")
    assert r.status_code == 200
    resumed = r.json()

    # The fields the client renders are identical to the ones it was handed
    # when the case was sealed — one display path, whether the case is a
    # second old or a week old.
    for field in ("case_id", "manifest_hash", "manifest_url", "provider",
                  "degraded", "provider_degraded", "has_schematic_animation",
                  "schematic_kind", "schematic_url", "illustration_url",
                  "report_markdown", "scene", "warnings"):
        assert resumed[field] == fresh[field], field

    # Artifact hashes come back from the stored keys, matching the seal.
    assert resumed["artifacts"]["scene_graph"]["sha256"] == \
        fresh["artifacts"]["scene_graph"]["sha256"]
    assert resumed["artifacts"]["report"]["size_bytes"] > 0
    # The canonical storage URL is private; playback goes through the api route.
    assert all(a["url"] is None for a in resumed["artifacts"].values())


def test_result_route_survives_a_fresh_process_that_never_saw_the_render():
    """The point of resuming: the instance answering the reload need not be the
    one that sealed the case. Reading the index back is what makes that work."""
    fresh = _render("resume-cold-read")
    reload_index = getattr(api._storage, "reload_index", None)
    if callable(reload_index):
        reload_index()
    assert client.get(f"/cases/{fresh['case_id']}/result").status_code == 200


def test_unknown_case_id_is_404():
    assert client.get("/cases/no-such-case/result").status_code == 404


def test_a_traversal_shaped_id_cannot_reach_outside_its_own_prefix():
    r = client.get("/cases/..%2F..%2Fetc%2Fpasswd/result")
    assert r.status_code == 404


def test_a_stored_but_unreadable_case_degrades_to_404_not_500():
    """Half a case is not a case. A manifest whose bytes do not parse answers
    the same "nothing to show" 404 as an id that never existed."""
    api._storage.put("brokencase/manifest/ab/" + "a" * 64 + "/manifest.json",
                     b"not valid json")
    r = client.get("/cases/brokencase/result")
    assert r.status_code == 404
    assert "Traceback" not in r.text


def test_a_case_missing_its_scene_is_404_not_500():
    """A manifest that parses but whose scene bytes are gone cannot be shown."""
    fresh = _render("resume-missing-scene")
    scene_row = next(r for r in api._storage.index
                     if r["key"].startswith(f"{fresh['case_id']}/scene/"))
    api._storage.delete(scene_row["key"])
    assert client.get(f"/cases/{fresh['case_id']}/result").status_code == 404


def test_another_tenants_case_is_the_same_404_as_an_unknown_id(bearer_for):
    """A shared link to someone else's case must fail cleanly, without
    confirming that the id exists — the guest answer and the wrong-tenant
    answer are byte-identical."""
    owner = bearer_for("resume-tenant-owner")
    other = bearer_for("resume-tenant-other")
    sealed = _render("resume-private", headers=owner)

    mine = client.get(f"/cases/{sealed['case_id']}/result", headers=owner)
    assert mine.status_code == 200

    theirs = client.get(f"/cases/{sealed['case_id']}/result", headers=other)
    guest = client.get(f"/cases/{sealed['case_id']}/result")
    unknown = client.get("/cases/definitely-not-a-case/result")
    assert theirs.status_code == guest.status_code == unknown.status_code == 404
    assert theirs.json()["detail"].replace(sealed["case_id"], "X") == \
        unknown.json()["detail"].replace("definitely-not-a-case", "X")


# ── a live-provider failure is legible ───────────────────────────────────────
def test_live_failure_returns_a_plain_kind_and_hides_the_upstream_text(monkeypatch):
    monkeypatch.setattr(api.config, "build_provider", lambda: _BrokenLiveProvider())
    body = _render("degrade-credit")

    assert body["provider_degraded"] is True
    assert body["degraded"] is True
    assert body["degrade_kind"] == "credit"
    # Nothing from the upstream error reaches the caller: no account id, no
    # provider URL, no raw message.
    raw = json.dumps(body)
    for secret in ("Insufficient credits", "acct_1234", "gmicloud.example", "402"):
        assert secret not in raw


def test_live_failure_kind_is_sealed_into_the_manifest(monkeypatch):
    """The visitor's explanation and the sealed record are the same story, not
    two independent ones."""
    monkeypatch.setattr(api.config, "build_provider", lambda: _BrokenLiveProvider())
    body = _render("degrade-sealed")
    manifest = client.get(body["manifest_url"]).json()
    assert manifest["illustration"]["degraded"] is True
    assert manifest["illustration"]["degrade_kind"] == "credit"
    assert "Insufficient credits" not in json.dumps(manifest)


def test_a_normal_offline_seal_claims_no_failure_kind():
    """No live provider was configured, so no live attempt failed. Claiming a
    kind here would be fiction, and it would also change the sealed bytes of
    every case this project has ever produced."""
    body = _render("degrade-absent")
    assert body["provider_degraded"] is False
    assert "degrade_kind" not in body
    manifest = client.get(body["manifest_url"]).json()
    assert "degrade_kind" not in manifest["illustration"]


def test_a_resumed_degraded_case_still_reports_the_failure(monkeypatch):
    """Resuming a case does not lose why its illustration is a placeholder."""
    monkeypatch.setattr(api.config, "build_provider", lambda: _BrokenLiveProvider())
    body = _render("degrade-resumed")
    resumed = client.get(f"/cases/{body['case_id']}/result").json()
    assert resumed["provider_degraded"] is True
    assert resumed["degrade_kind"] == "credit"


def test_the_operator_can_still_find_the_real_reason_in_the_logs(monkeypatch, caplog):
    monkeypatch.setattr(api.config, "build_provider", lambda: _BrokenLiveProvider())
    with caplog.at_level("WARNING", logger="claimscene.api"):
        _render("degrade-logged")
    logged = caplog.text
    assert "Insufficient credits" in logged      # the upstream detail
    assert "credit" in logged                    # ...next to the kind shown


# ── measured time estimate ───────────────────────────────────────────────────
def test_estimate_is_honest_about_having_measured_nothing():
    body = client.get("/cases/render/estimate").json()
    assert body["samples"] == 0
    assert body["typical_seconds"] is None and body["slow_seconds"] is None


def test_a_completed_render_job_is_measured_and_reported():
    r = client.post("/cases/render/jobs", data={
        "scene": json.dumps(_scene()), "case_id": "estimate-measured",
        "scenario_id": "s02_left_cross"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        status = client.get(f"/cases/render/jobs/{job_id}").json()
        if status.get("status") in jobs.TERMINAL_STATUSES:
            break
        time.sleep(0.02)
    assert status["status"] == "done"

    body = client.get("/cases/render/estimate").json()
    assert body["samples"] >= 1
    # A real duration, in seconds, from a render that actually happened.
    assert body["typical_seconds"] >= 0
    assert body["slow_seconds"] >= body["typical_seconds"]
    assert body["mode"] == "offline"


def test_estimate_ignores_degraded_runs_when_a_live_provider_is_configured(monkeypatch):
    """An offline placeholder seals in about a second. Counting those on a live
    deployment would advertise a wait nobody will actually get."""
    storage = api._tenant_storage(None)
    jobs.record_duration(storage, 1.0, degraded=True)
    jobs.record_duration(storage, 240.0, degraded=False)
    monkeypatch.setattr(api, "_provider", _BrokenLiveProvider())
    body = client.get("/cases/render/estimate").json()
    assert body["samples"] == 1
    assert body["typical_seconds"] == 240


def test_estimate_is_tenant_scoped(bearer_for):
    headers = bearer_for("estimate-tenant")
    jobs.record_duration(api._tenant_storage(None), 99.0, degraded=True)
    assert client.get("/cases/render/estimate").json()["samples"] == 1
    assert client.get("/cases/render/estimate", headers=headers).json()["samples"] == 0
