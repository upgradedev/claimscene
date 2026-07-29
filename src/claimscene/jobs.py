"""Storage-backed job store for async render generation (the submit + poll
pattern).

A case render runs a real two-step generation (an establish-shot still, then
an image-to-video clip chained from it) that can take minutes — see
``deploy/CLOUDRUN.md``'s ``--timeout 600`` note — which is longer than the
Firebase Hosting proxy cap (~60s) and comparable to Cloud Run's own request
ceiling. ``POST /cases/render/jobs`` (see ``claimscene.api``) avoids blocking
on that: it validates the request, writes a "queued" status object, starts the
real render in a background thread, and returns immediately with a job id.
``GET /cases/render/jobs/{job_id}`` polls that status back.

A job's state is a small JSON object stored through the SAME
:class:`~claimscene.ports.StorageBackend` the rest of the app already uses (B2
in live mode, the in-memory fake offline), under ``jobs/<job_id>/status.json``
— NOT a separate in-process registry. That is what makes status pollable
across Cloud Run instances: the object store, not any one process's memory, is
the source of truth, so a scale-to-zero instance that never saw the submit can
still answer the poll by re-reading the key. The job id itself is a
:func:`secrets.token_urlsafe` token (unguessable, not a sequential id or a
``uuid1`` that would leak MAC address / timestamp bits).

Ported from our other MIT entry, Cinemory, which pioneered this pattern for
its own async reel generation.

Honest limitation — read before assuming this is durable
----------------------------------------------------------
The background worker runs **in-process, on the Cloud Run instance that
accepted the submit** (see ``claimscene.api._run_render_job``). There is no
external queue and no separate worker fleet. A client that keeps polling keeps
that instance warm (a request in flight resets Cloud Run's idle-scale-down
clock), so in practice the job completes. But if that specific instance is
scaled down mid-job — e.g. nobody polls for a while, or Cloud Run reclaims it
for another reason — the in-flight generation is lost with no automatic retry;
the stored status simply stops advancing past whatever it last reached. This
is an acceptable, deliberate tradeoff for a demo, not a production job queue. A
production version would hand the work to a durable queue (e.g. Cloud Tasks)
consumed by a Cloud Run *Job* (not a request-serving instance), so the work
outlives any one instance. See ``deploy/CLOUDRUN.md`` for the Cloud Run
``--no-cpu-throttling`` flag this needs even for the demo version (without it,
Cloud Run throttles CPU to near-zero between requests, and the background
thread would barely make progress between polls).
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from .keys import safe_component
from .ports import StorageBackend

_log = logging.getLogger("claimscene.jobs")

#: Every job status object lives under this prefix, in its own id-named folder
#: — a namespace that can never collide with a case's own
#: ``<case>/<kind>/<shard>/<sha256>/<file>`` keys (different segment count;
#: see ``claimscene.keys.make_key``).
_KEY_PREFIX = "jobs"

#: Terminal statuses a poller should stop on.
TERMINAL_STATUSES = ("done", "failed")


def new_job_id() -> str:
    """A fresh, unguessable job id.

    ``secrets.token_urlsafe`` (a CSPRNG), never ``uuid1`` (leaks MAC address +
    timestamp bits) and never a sequential counter (guessable/enumerable).
    """
    return secrets.token_urlsafe(18)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(job_id: str) -> str:
    # job_id is server-generated (url-safe alphabet, no '/'), but a lookup by
    # job_id also flows in from the URL path on GET — sanitise defensively the
    # same way every other user-reachable key segment in this codebase is
    # (see claimscene.keys.safe_component), so a hostile id can never be
    # shaped into a key outside this job's own prefix. A legitimate id is
    # untouched by this (it is already within the safe charset).
    return f"{_KEY_PREFIX}/{safe_component(job_id)}/status.json"


def _write(storage: StorageBackend, job_id: str, status: dict) -> dict:
    storage.put(
        _key(job_id),
        json.dumps(status, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    return status


def create(storage: StorageBackend, job_id: str) -> dict:
    """Seed a new job as ``queued`` and persist it. The first write for this id."""
    now = _now()
    return _write(storage, job_id, {
        "job_id": job_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    })


def read(storage: StorageBackend, job_id: str) -> dict | None:
    """The stored status dict, or ``None`` if unknown/unreadable.

    Honest-degrade, mirroring ``claimscene.api.verify_case``'s "unreadable
    bytes never 500" contract: a job id that was never created, a storage read
    error, corrupt JSON, or (defensively) JSON that isn't an object all read
    the same way — ``None`` — so the caller (``GET /cases/render/jobs/{job_id}``)
    can turn any of them into one honest 404 rather than leaking a stack trace.
    """
    try:
        parsed: Any = json.loads(storage.get(_key(job_id)))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _update(storage: StorageBackend, job_id: str, **fields: Any) -> dict:
    """Read-modify-write the stored status: apply ``fields``, stamp ``updated_at``.

    Falls back to a fresh ``queued`` base if nothing was stored yet (defensive
    only — the normal flow always calls :func:`create` first), so a transition
    call can never itself raise for a missing predecessor object.
    """
    current = read(storage, job_id) or {
        "job_id": job_id, "status": "queued", "created_at": _now(),
    }
    current.update(fields)
    current["updated_at"] = _now()
    return _write(storage, job_id, current)


def mark_running(storage: StorageBackend, job_id: str) -> dict:
    return _update(storage, job_id, status="running")


def mark_done(storage: StorageBackend, job_id: str, result: dict) -> dict:
    return _update(storage, job_id, status="done", result=result)


def mark_failed(storage: StorageBackend, job_id: str, exc: BaseException) -> dict:
    # Full detail goes to the log only; the stored/returned status carries the
    # exception CLASS NAME alone — exception text may embed URLs/identifiers
    # that don't belong in an API-readable object. Mirrors
    # claimscene.api._run_render's ``degrade_reason`` (same reasoning, same
    # shape).
    _log.warning("job %r failed: %s: %s", job_id, type(exc).__name__, exc)
    return _update(storage, job_id, status="failed", error=type(exc).__name__)
