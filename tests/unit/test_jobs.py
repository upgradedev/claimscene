"""Unit tests for the async job store (``claimscene.jobs``).

Driven directly against a :class:`~claimscene.adapters.InMemoryStorage` (the
same ``StorageBackend`` port the real B2 adapter implements) — no HTTP, no
threading, so these pin the module's own contract in isolation. The full
submit -> background-thread -> poll flow is covered separately in
``tests/e2e/test_render_jobs.py``.

Ported from Cinemory's equivalent suite for the identical job-store design.
"""
from __future__ import annotations

import json

from claimscene import jobs
from claimscene.adapters import InMemoryStorage


def test_new_job_id_is_url_safe_and_unique():
    a = jobs.new_job_id()
    b = jobs.new_job_id()
    assert a != b
    assert len(a) >= 16
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    assert set(a) <= allowed


def test_create_writes_queued_status_and_is_readable_back():
    storage = InMemoryStorage()
    status = jobs.create(storage, "job1")
    assert status["job_id"] == "job1"
    assert status["status"] == "queued"
    assert status["created_at"] == status["updated_at"]

    read_back = jobs.read(storage, "job1")
    assert read_back == status
    # Stored through the same StorageBackend port the rest of the app uses,
    # under the documented key — not a separate in-process registry.
    assert storage.get("jobs/job1/status.json")


def test_read_unknown_job_is_none():
    storage = InMemoryStorage()
    assert jobs.read(storage, "does-not-exist") is None


def test_read_corrupt_bytes_is_none():
    storage = InMemoryStorage()
    storage.put("jobs/broken/status.json", b"not json at all")
    assert jobs.read(storage, "broken") is None


def test_read_non_object_json_is_none():
    """Defensive: valid JSON that isn't an object (e.g. a bare number) still
    degrades to None rather than being handed back as a "status"."""
    storage = InMemoryStorage()
    storage.put("jobs/weird/status.json", b"42")
    assert jobs.read(storage, "weird") is None


def test_mark_running_then_done_preserves_created_at_and_advances_updated_at():
    storage = InMemoryStorage()
    created = jobs.create(storage, "job2")
    running = jobs.mark_running(storage, "job2")
    assert running["status"] == "running"
    assert running["created_at"] == created["created_at"]

    result = {"case_id": "job2", "manifest_hash": "a" * 64, "provider": "fake-media"}
    done = jobs.mark_done(storage, "job2", result)
    assert done["status"] == "done"
    assert done["result"] == result
    assert done["created_at"] == created["created_at"]

    # The poll path (GET /cases/render/jobs/{id}) reads back exactly this.
    assert jobs.read(storage, "job2") == done


def test_mark_failed_records_exception_class_name_only():
    storage = InMemoryStorage()
    jobs.create(storage, "job3")
    failed = jobs.mark_failed(storage, "job3", RuntimeError("some sensitive internal detail"))
    assert failed["status"] == "failed"
    assert failed["error"] == "RuntimeError"
    assert "result" not in failed
    assert "sensitive internal detail" not in json.dumps(failed)


def test_mark_running_without_prior_create_seeds_a_base_status():
    """Defensive fallback: a transition call never raises for a missing
    predecessor object (the normal flow always calls create() first, via
    claimscene.api.create_render_job before the background thread starts)."""
    storage = InMemoryStorage()
    running = jobs.mark_running(storage, "orphan")
    assert running["job_id"] == "orphan"
    assert running["status"] == "running"
    assert running["created_at"]


def test_hostile_job_id_key_stays_sanitised_and_namespaced():
    """A job id can never be shaped into a key outside jobs/<id>/... — the id
    is sanitised the same way every other user-reachable key segment in this
    codebase is (claimscene.keys.safe_component), even though in the real flow
    job ids are always server-generated (secrets.token_urlsafe), never
    attacker-supplied on write."""
    storage = InMemoryStorage()
    jobs.create(storage, "../../evil")
    keys = {row["key"] for row in storage.index}
    assert keys  # something was written
    for key in keys:
        segments = key.split("/")
        assert segments[0] == "jobs"
        assert ".." not in segments
        assert not any(seg.startswith(".") for seg in segments)


# ── measured render durations ────────────────────────────────────────────────
def test_no_recorded_durations_reads_as_empty_not_an_error():
    """A fresh deployment has measured nothing, and says so."""
    assert jobs.read_durations(InMemoryStorage()) == []


def test_corrupt_durations_object_reads_as_empty():
    storage = InMemoryStorage()
    storage.put(jobs.DURATIONS_KEY, b"not json")
    assert jobs.read_durations(storage) == []
    storage.put(jobs.DURATIONS_KEY, b'{"not": "a list"}')
    assert jobs.read_durations(storage) == []
    storage.put(jobs.DURATIONS_KEY, b'[1, 2, {"no_seconds": true}]')
    assert jobs.read_durations(storage) == []


def test_record_duration_appends_and_keeps_the_window_bounded():
    storage = InMemoryStorage()
    for i in range(jobs.MAX_DURATION_SAMPLES + 5):
        jobs.record_duration(storage, float(i), degraded=False)
    samples = jobs.read_durations(storage)
    assert len(samples) == jobs.MAX_DURATION_SAMPLES
    # The window keeps the most RECENT renders, so a provider change shows up.
    assert samples[-1]["seconds"] == float(jobs.MAX_DURATION_SAMPLES + 4)
    assert samples[0]["degraded"] is False and samples[0]["at"]


def test_record_duration_never_raises_when_storage_is_broken():
    """Bookkeeping must never turn a sealed case into an error."""

    class _BrokenStorage(InMemoryStorage):
        def put(self, key, data, content_type="application/octet-stream"):
            raise RuntimeError("storage is down")

    jobs.record_duration(_BrokenStorage(), 12.0, degraded=False)  # no raise


def test_estimate_with_no_samples_admits_it_knows_nothing():
    assert jobs.estimate([]) == {"samples": 0, "typical_seconds": None,
                                 "slow_seconds": None}


def test_estimate_reports_median_and_p90_of_real_durations():
    samples = [{"seconds": s} for s in (100, 120, 140, 160, 900)]
    est = jobs.estimate(samples)
    assert est["samples"] == 5
    assert est["typical_seconds"] == 140          # median, unmoved by the outlier
    assert est["slow_seconds"] == 900             # nearest-rank p90
    # Nearest rank never invents a duration between two real ones.
    assert est["slow_seconds"] in {s["seconds"] for s in samples}


def test_estimate_ignores_unparseable_rows():
    est = jobs.estimate([{"seconds": "quick"}, {"seconds": 60}])
    assert est["samples"] == 1 and est["typical_seconds"] == 60
