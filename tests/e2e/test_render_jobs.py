"""Integration tests for async render generation (submit + poll).

``POST /cases/render/jobs`` accepts the identical body as ``POST
/cases/render`` but returns ``202`` + a job id immediately instead of blocking
for the full two-step generation (establish-shot still, then image-to-video
clip); ``GET /cases/render/jobs/{job_id}`` polls the stored status back.
Driven through the real FastAPI app, offline (InMemoryStorage +
FakeMediaProvider), so "background" generation is effectively instant — the
tests still poll with a short bounded timeout rather than assume one check
suffices, since the work genuinely runs on a separate thread.

The existing synchronous endpoint (``tests/e2e/test_api.py`` and friends) is
untouched by this file and by the refactor that backs it — see
``src/claimscene/api.py``'s ``_prepare_render``/``_run_render``.

Ported from Cinemory's equivalent suite (``tests/integration/test_api_jobs.py``
there) for the identical submit+poll design.
"""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")  # python-multipart, needed for the form routes

from fastapi.testclient import TestClient  # noqa: E402

import claimscene.api as api  # noqa: E402
from claimscene import jobs  # noqa: E402
from claimscene.adapters import FakeMediaProvider, InMemoryStorage  # noqa: E402

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch):
    """Give this file's tests their own fresh, private ``InMemoryStorage``.

    Job-status keys (``jobs/<id>/status.json`` — 3 segments, no 64-char SHA
    anchor) would otherwise land in the SAME shared module-level index other
    test files scan when asserting the "every stored key is content-addressed"
    invariant (see ``tests/security/test_injection_path_traversal.py`` and
    ``tests/e2e/test_api.py``). Those existing scans already scope by a
    case-id prefix so they are not actually at risk, but isolating this file's
    own submit/poll/corrupt-status assertions against a storage instance no
    other test can see makes them fully order-independent from whatever any
    other test file already wrote (or writes later) into the shared store.
    Every function under test reads the module attribute ``api._storage``
    fresh at call time (``_job_storage``, ``_run_render``'s default,
    ``_case_index_match``, the job GET handler) rather than caching it, so
    patching the module attribute for the duration of each test is
    sufficient — no other seam is needed.
    """
    monkeypatch.setattr(api, "_storage", InMemoryStorage())


def _scene(scenario_id: str = "s02_left_cross") -> dict:
    return client.post("/cases/extract", data={"scenario_id": scenario_id}).json()["scene"]


def _poll_until_terminal(c: TestClient, job_id: str, *, timeout: float = 30.0) -> dict:
    """Poll GET /cases/render/jobs/{job_id} until it reaches a terminal status.

    Unlike Cinemory's fully in-memory offline fakes, ClaimScene's DEFAULT
    schematic renderer shells out to a real `ffmpeg` subprocess for the
    animated schematic whenever ffmpeg is on PATH — offline still means "no
    credentials", not "no subprocess". That happens for every render
    (sync or async), so this is not new latency introduced by the async path;
    it just means the bound here has to tolerate a real (if small) amount of
    wall-clock work — including a slower first-ever ffmpeg spawn in the test
    process — rather than assume the near-instant, pure-in-memory generation
    Cinemory's own offline fakes give it.
    """
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = c.get(f"/cases/render/jobs/{job_id}")
        assert r.status_code == 200
        body = r.json()
        if body.get("status") in jobs.TERMINAL_STATUSES:
            return body
        time.sleep(0.02)
    raise AssertionError(
        f"job {job_id} did not reach a terminal status within {timeout}s: {body}"
    )


# ── submit ───────────────────────────────────────────────────────────────────
def test_submit_returns_202_with_job_id_and_queued_status():
    scene = _scene()
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "case_id": "asyncjob-submit"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert isinstance(body["job_id"], str) and body["job_id"]


def test_submit_invalid_scene_is_422_synchronously():
    """The constrained-vocabulary gate runs BEFORE a job is created — same 422
    as the sync endpoint, no job id handed out for a request that will never
    render."""
    scene = _scene()
    scene["vehicles"] = [{"id": "v", "kind": "hovercraft", "color": "red"}]
    r = client.post("/cases/render/jobs", data={"scene": json.dumps(scene)})
    assert r.status_code == 422
    assert "job_id" not in r.json()


def test_submit_malformed_scene_json_is_422_synchronously():
    r = client.post("/cases/render/jobs", data={"scene": "not json at all"})
    assert r.status_code == 422


def test_submit_unknown_scenario_is_404_synchronously():
    scene = _scene()
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "scenario_id": "does-not-exist"})
    assert r.status_code == 404


# ── poll → done ──────────────────────────────────────────────────────────────
def test_poll_reaches_done_with_real_sealed_case_result():
    scene = _scene()
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "case_id": "asyncjob-done",
                          "scenario_id": "s02_left_cross"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = _poll_until_terminal(client, job_id)
    assert status["status"] == "done"
    assert status["job_id"] == job_id
    assert status["created_at"] and status["updated_at"]

    result = status["result"]
    assert result["case_id"].startswith("asyncjob-done-")
    assert result["manifest_hash"]
    assert result["degraded"] is True  # offline illustration is deterministic bytes
    assert "manifest_url" in result and "schematic_url" in result

    # The job's render really landed in storage — fetchable the normal way.
    manifest = client.get(result["manifest_url"]).json()
    assert manifest["case_id"] == result["case_id"]


def test_poll_with_uploaded_photos_matches_sync_shape():
    """The async path accepts real photo uploads too, not just scenario ids —
    same ingest as the synchronous endpoint."""
    scene = _scene()
    files = [("files", ("scene.png", api._PNG_1x1 + b"a", "image/png"))]
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "case_id": "asyncjob-upload"},
                    files=files)
    assert r.status_code == 202
    status = _poll_until_terminal(client, r.json()["job_id"])
    assert status["status"] == "done"
    manifest = client.get(status["result"]["manifest_url"]).json()
    assert manifest["inputs"][0]["source"] == "user_upload"


# ── poll → unknown / corrupt / failed ───────────────────────────────────────
def test_unknown_job_id_is_404():
    r = client.get("/cases/render/jobs/does-not-exist")
    assert r.status_code == 404


def test_corrupt_stored_status_degrades_to_404_not_500():
    """A status object that exists but doesn't parse degrades the same way an
    unknown job id does (see claimscene.jobs.read) — never a 500."""
    api._storage.put("jobs/corrupt-job/status.json", b"not valid json")
    r = client.get("/cases/render/jobs/corrupt-job")
    assert r.status_code == 404


def test_job_forced_failure_marks_status_failed_not_500(monkeypatch):
    """A generation failure surfaces ONLY through the polled status — never as
    an HTTP error anywhere in the submit/poll flow. Mirrors the sync path's
    ``test_render_propagates_a_genuine_offline_bug`` fixture (a
    FakeMediaProvider subclass that raises), except here the offline failure
    has nowhere to surface as a request 500, because the generation never
    happens on a request thread at all."""

    class _BrokenFake(FakeMediaProvider):
        def generate(self, **_kwargs) -> bytes:
            raise RuntimeError("offline provider bug (forced for this test)")

    monkeypatch.setattr(api.config, "build_provider", lambda: _BrokenFake())

    scene = _scene()
    r = client.post("/cases/render/jobs",
                    data={"scene": json.dumps(scene), "case_id": "asyncjob-forced-fail"})
    # The submit itself never sees the failure — it happens later, in the
    # background thread.
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = _poll_until_terminal(client, job_id)
    assert status["status"] == "failed"
    assert status["error"] == "RuntimeError"
    assert "result" not in status
    # Class name only — the raw exception text never reaches the response.
    assert "forced for this test" not in json.dumps(status)
