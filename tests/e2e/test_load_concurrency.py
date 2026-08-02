"""Load coverage for the endpoints added after the original load test.

``tests/integration/test_load.py`` exercises the ``CasePipeline`` directly
under a thread pool, which proves the pipeline itself is thread-safe. It does
not touch the two API surfaces added later, and those are exactly the ones
where concurrency is risky:

* ``POST /cases/render/jobs`` hands out a job id and starts a REAL background
  thread per submission, so under load many threads race to write their own
  ``jobs/<id>/status.json`` into the same storage.
* Tenant isolation is enforced by a prefixed view over one shared storage
  object (``api._TenantScopedStorage``). ``tests/security/`` and
  ``tests/e2e/test_multitenancy.py`` prove it holds for SEQUENTIAL requests.
  A prefix filter that is correct sequentially can still leak if writes from
  several tenants interleave, so it has to be shown under real concurrency.

Mirrors the equivalent coverage in the sibling Cinemory repo. Fully offline
(``FakeMediaProvider`` + ``InMemoryStorage``), so no provider is called and
nothing is billed.
"""
from __future__ import annotations

import concurrent.futures
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")  # python-multipart, needed for the form routes

from fastapi.testclient import TestClient  # noqa: E402

import claimscene.api as api  # noqa: E402
from claimscene import jobs  # noqa: E402
from claimscene.adapters import InMemoryStorage  # noqa: E402

client = TestClient(api.app)

#: Submissions fired at once. Deliberately modest: the point is to interleave
#: writes, not to benchmark throughput, and each case still renders a real
#: schematic (which shells out to ffmpeg when it is on PATH).
_CONCURRENCY = 8
_MAX_WORKERS = 4
#: Generous ceiling for one offline job to reach a terminal status. Offline
#: generation is effectively instant, but the work genuinely runs on another
#: thread, so this is a bound rather than an expected wait.
_JOB_TIMEOUT_S = 60.0


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch):
    """Give this file's tests their own fresh, private storage.

    Same reasoning as ``test_render_jobs.py``: ``jobs/<id>/status.json`` keys
    are not content-addressed, so they must not land in the shared index other
    files scan when asserting that invariant.

    Nothing else is patched. The render path builds its own provider through
    ``config.build_provider()`` per request, which is the offline fake here, and
    it constructs its own renderer, so there is no module-level seam for either
    and none is needed: the point of this file is to exercise the real request
    path under concurrency, not a stripped-down one.
    """
    monkeypatch.setattr(api, "_storage", InMemoryStorage(bucket="claimscene-load-e2e"))


def _scene(scenario_id: str = "s02_left_cross") -> dict:
    return client.post("/cases/extract", data={"scenario_id": scenario_id}).json()["scene"]


def _poll_until_terminal(job_id: str, *, timeout: float = _JOB_TIMEOUT_S) -> dict:
    """Bounded poll on the shared client until the job reaches a terminal state."""
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/cases/render/jobs/{job_id}")
        if r.status_code == 200:
            body = r.json()
            if body.get("status") in jobs.TERMINAL_STATUSES:
                return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached a terminal status in {timeout}s: {body}")


def test_concurrent_render_job_submission_load():
    """Many concurrent submissions: every one accepted, every id unique, all done.

    The risky part is that each submission spins up its own background thread
    writing its own status object into one shared storage. A race in job-id
    generation or in status creation would show up here as a duplicate id, a
    500, or a job that never advances.
    """
    scene_json = json.dumps(_scene())

    def _submit(worker_id: int) -> tuple[int, str]:
        r = client.post(
            "/cases/render/jobs",
            data={"scene": scene_json, "case_id": f"load-job-{worker_id}"},
        )
        if r.status_code != 202:
            raise AssertionError(f"worker {worker_id} got {r.status_code}: {r.text}")
        return worker_id, r.json()["job_id"]

    job_ids: dict[int, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_submit, i): i for i in range(_CONCURRENCY)}
        for future in concurrent.futures.as_completed(futures):
            worker_id = futures[future]
            try:
                wid, job_id = future.result()
            except Exception as exc:  # noqa: BLE001 - surface the real failure
                pytest.fail(f"worker {worker_id} submission failed: {exc}")
            job_ids[wid] = job_id

    assert len(job_ids) == _CONCURRENCY
    assert len(set(job_ids.values())) == _CONCURRENCY, "every job_id must be unique"

    for worker_id, job_id in job_ids.items():
        status = _poll_until_terminal(job_id)
        assert status["status"] == "done", (
            f"job {job_id} (worker {worker_id}) ended {status.get('status')}: {status}"
        )


def test_tenant_isolation_holds_under_concurrent_case_creation(bearer_for):
    """Several tenants plus guest rendering at the same time never bleed.

    ``tests/e2e/test_multitenancy.py`` proves isolation for sequential
    requests. This proves the prefixed-storage view still partitions correctly
    when writes from different tenants interleave, which is the failure mode a
    sequential test cannot see.
    """
    tenants = ["load-a", "load-b", "load-c"]
    per_tenant = 3
    guest_cases = 3
    headers = {t: bearer_for(t) for t in tenants}
    scene_json = json.dumps(_scene())

    work: list[tuple[str | None, str]] = [
        (t, f"load-case-{t}-{i}") for t in tenants for i in range(per_tenant)
    ] + [(None, f"load-case-guest-{i}") for i in range(guest_cases)]

    def _render(job: tuple[str | None, str]) -> tuple[str | None, str, int, str]:
        tenant, case_id = job
        r = client.post(
            "/cases/render",
            data={"scene": scene_json, "case_id": case_id},
            headers=headers[tenant] if tenant else {},
        )
        sealed = r.json().get("case_id", "") if r.status_code == 200 else ""
        return tenant, case_id, r.status_code, sealed

    results: list[tuple[str | None, str, int, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [pool.submit(_render, job) for job in work]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"concurrent render failed: {exc}")

    assert len(results) == len(work)
    for tenant, case_id, status, _ in results:
        assert status == 200, f"{tenant or 'guest'} case {case_id!r} returned {status}"

    sealed_by_tenant: dict[str | None, set[str]] = {}
    for tenant, _, _, sealed in results:
        sealed_by_tenant.setdefault(tenant, set()).add(sealed)

    # Subset plus negative checks, not exact equality: storage is shared
    # process-wide, so another test's cases may also be present under guest.
    for tenant in tenants:
        library = client.get("/me/library", headers=headers[tenant]).json()
        own = {c["case_id"] for c in library["cases"]}
        assert sealed_by_tenant[tenant] <= own, f"{tenant} lost one of its own cases"
        for other in tenants:
            if other == tenant:
                continue
            leaked = sealed_by_tenant[other] & own
            assert not leaked, f"{tenant} can see {other}'s cases: {leaked}"
        guest_leak = sealed_by_tenant.get(None, set()) & own
        assert not guest_leak, f"{tenant} can see guest cases: {guest_leak}"
