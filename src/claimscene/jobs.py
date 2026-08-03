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
import math
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


# ── measured render durations (the "how long will this take" answer) ──────────
# A live render takes minutes, and the honest way to say how many is to measure
# it rather than print a number someone once guessed. Every completed render
# appends how long it actually took, and ``GET /cases/render/estimate`` (see
# ``claimscene.api``) summarises the recent ones. With nothing recorded yet the
# API says exactly that, and the client says "we have not timed one here yet"
# instead of inventing a figure.

#: Rolling sample file. Two segments, like a job status key's prefix — it can
#: never collide with a case artifact key (five segments; see
#: ``claimscene.keys.make_key``).
DURATIONS_KEY = f"{_KEY_PREFIX}/durations.json"

#: How many recent renders the estimate is computed over. Small on purpose: a
#: change in model, preset or provider should show up in the estimate within a
#: handful of renders, not be diluted by months of history.
MAX_DURATION_SAMPLES = 20


def read_durations(storage: StorageBackend) -> list[dict]:
    """Recorded render durations, oldest first. ``[]`` when nothing is recorded.

    Honest-degrade like :func:`read`: a missing object, a storage error or
    corrupt JSON all read as "no measurements", so the estimate endpoint says
    it has none rather than failing.
    """
    try:
        parsed: Any = json.loads(storage.get(DURATIONS_KEY))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [row for row in parsed if isinstance(row, dict) and "seconds" in row]


def record_duration(storage: StorageBackend, seconds: float, *, degraded: bool) -> None:
    """Append one completed render's wall-clock duration. Never raises.

    ``degraded`` records whether that run produced a real generative
    illustration or the offline placeholder, because the two have wildly
    different durations and only one of them answers "how long will MY live
    render take" (see the estimate endpoint's filtering).

    Read-modify-write on a single small object, with no lock: two renders
    finishing within the same instant can drop one sample. That is acceptable
    for a rolling estimate and deliberately not worth a coordination mechanism
    — a lost sample changes a median by seconds. Any failure at all is
    swallowed: a bookkeeping write must never turn a sealed case into an error.
    """
    try:
        samples = read_durations(storage)
        samples.append({
            "seconds": round(float(seconds), 1),
            "degraded": bool(degraded),
            "at": _now(),
        })
        storage.put(
            DURATIONS_KEY,
            json.dumps(samples[-MAX_DURATION_SAMPLES:], indent=2).encode("utf-8"),
            content_type="application/json",
        )
    except Exception:  # pragma: no cover - defensive; storage is already tested
        _log.warning("could not record render duration", exc_info=True)


def _nearest_rank(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile — no interpolation between samples.

    With a handful of samples, an interpolated percentile invents a duration
    that no render ever took. Nearest-rank always returns a number that really
    happened, which is the claim we want to be able to defend.
    """
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def estimate(samples: list[dict]) -> dict:
    """Summarise durations into ``{samples, typical_seconds, slow_seconds}``.

    ``typical`` is the median and ``slow`` the 90th percentile (nearest rank),
    both rounded to whole seconds — a range, not a promise. With no samples
    both are ``None`` and ``samples`` is ``0``: the caller must then say it
    does not know, not fall back to a fabricated default.
    """
    values = []
    for row in samples:
        try:
            values.append(float(row["seconds"]))
        except (TypeError, ValueError, KeyError):  # pragma: no cover - defensive
            continue
    if not values:
        return {"samples": 0, "typical_seconds": None, "slow_seconds": None}
    return {
        "samples": len(values),
        "typical_seconds": round(_nearest_rank(values, 0.5)),
        "slow_seconds": round(_nearest_rank(values, 0.9)),
    }
