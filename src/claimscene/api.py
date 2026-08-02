"""ClaimScene HTTP API (FastAPI).

The web surface over the offline-first pipeline. The review-adjust loop is the
product's centrepiece, so the routes fall into three groups:

  extract  →  the VLM (or, offline, a shipped sample) proposes a *constrained*
              SceneGraph from photos.
  preview  →  the deterministic LayoutEngine + blueprint renderer turn a
              (possibly human-edited) SceneGraph into a fast static top-down
              schematic — the live feedback the reviewer edits against.
  render   →  the full pipeline seals the reviewed scene: animated schematic
              (factual layer) + Genblaze illustration (disclosed) + report +
              a canonical-SHA-256 manifest, all stored.

Routes
------
GET  /health                          liveness + effective backends
GET  /scenarios                       committed sample scenarios (zero-photo demo)
GET  /scenarios/{id}/images/{name}    a scenario thumbnail (path-sanitised)
POST /cases/extract                   photos|scenario → constrained SceneGraph
POST /cases/preview-schematic         SceneGraph → static schematic (live review)
POST /cases/render                    reviewed SceneGraph → sealed case
POST /cases/render/jobs               submit a render as a background job (202 +
                                       job_id) — does not block for the full
                                       two-step generation
GET  /cases/render/jobs/{job_id}      poll a submitted render job's status
                                       (queued/running/done/failed)
GET  /cases/{id}                      the sealed manifest (raw, for in-browser verify)
GET  /cases/{id}/verify               server-side re-verification (named-check receipt)
GET  /cases/{id}/receipt              the detached, self-sealed receipt (raw bytes)
GET  /cases/{id}/schematic            factual-layer playback (mp4 live / png offline)
GET  /cases/{id}/illustration         illustration playback (302 presign live / stream)
GET  /me/library                      an authed tenant's own cases (401 for guest)
DELETE /me/data                       delete every object under an authed tenant's own
                                       prefix (401 for guest)

Offline by default: every route works with zero credentials on the
deterministic fakes. In ``live`` mode the real backends are used only when
their credentials are present, and a live-provider failure degrades *this
request* to the offline provider (storage untouched) — the response says so
honestly and the sealed manifest records the provider that actually ran.

Async submit + poll (``POST /cases/render/jobs`` / ``GET
/cases/render/jobs/{job_id}``): the synchronous ``POST /cases/render`` above
runs a real two-step generation (an establish-shot still, then an
image-to-video clip chained from it) that can take minutes — see
``deploy/CLOUDRUN.md``'s ``--timeout 600`` note — longer than the Firebase
Hosting proxy cap (~60s), so a caller that cannot hold one request open that
long submits a job and polls it instead. The async route runs the exact same
ingest validation and render path as the synchronous one (see
``_run_render_job`` below and the ``claimscene.jobs`` module for the job-store
design and its honest limitation — the worker runs in-process on the instance
that accepted the submit).

Optional per-user multitenancy (see ``claimscene.auth`` and ``get_tenant``
below, ported from our other MIT entry, Cinemory, which pioneered this
pattern): every case route accepts an optional ``Authorization: Bearer
<Firebase ID token>`` header. Absent — or ``FIREBASE_PROJECT_ID`` unset on
this deployment — every request is GUEST, the exact behaviour this API has
always had. A verified token scopes that request's storage to a
``tenants/<uid>/`` prefix (``_tenant_storage``/``_TenantScopedStorage``), so
a signed-in caller's cases are isolated from every other tenant and from
guest data — by construction (a tenant's index scan can only ever see rows
under its own prefix), not by a check that could be forgotten. See ``GET
/me/library`` and ``DELETE /me/data`` for self-service listing/erasure.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NamedTuple
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from . import auth, config, jobs, scenarios
from .adapters import FakeMediaProvider, FakeVisionExtractor, InMemoryStorage
from .case import (
    CLIENT_REVIEW_CLASSIFICATIONS,
    CasePhoto,
    CaseSpec,
    PhotoRole,
    PhotoSource,
    ReviewClassification,
)
from .ingest import MAX_PHOTO_BYTES, MAX_PHOTOS, UploadError, reject_dangerous_bytes
from .keys import safe_component
from .layout import LayoutEngine
from .pipeline import CasePipeline, CaseResult
from .ports import StorageBackend
from .provenance import input_record, verify_all
from .scene import SceneGraph, scene_to_json, semantic_warnings
from .schematic import build_static_svg

_log = logging.getLogger("claimscene.api")

app = FastAPI(title="ClaimScene API", version="0.1.0")

# Storage is process-lived so a sealed case can be fetched + played back by id.
_storage = config.build_storage()
# Effective backends resolved once for the honest /health surface.
_extractor = config.build_extractor()
_provider = config.build_provider()

# A tiny valid 1x1 PNG for the "render with no supplied inputs" fallback, so a
# pure type-a-scene-and-render flow still seals a full, honest case.
_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc``\x00\x00\x00\x04"
    b"\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ── the reviewed-scene extractor ─────────────────────────────────────────────
class FixedSceneExtractor:
    """A :class:`~claimscene.ports.VisionExtractor` that returns a scene the
    caller already has — the human-reviewed SceneGraph from the render request.

    This lets ``/cases/render`` reuse the *exact* production pipeline (inputs →
    scene → schematic → illustration → sealed manifest) without re-running a
    model over the photos: the reviewer's confirmed scene is what gets sealed.
    """

    name = "reviewed-scene"

    def __init__(self, scene: SceneGraph) -> None:
        self._scene = scene

    def extract(self, photos, *, context: str | None = None) -> SceneGraph:
        return self._scene.model_copy(deep=True)


# ── helpers ──────────────────────────────────────────────────────────────────
def _infer_role(filename: str) -> PhotoRole:
    lower = filename.lower()
    if "damage" in lower:
        return PhotoRole.damage_photo
    if "road" in lower:
        return PhotoRole.road_photo
    return PhotoRole.scene_photo


def _parse_scene(raw: str) -> SceneGraph:
    """Parse a client SceneGraph, mapping validation failures to HTTP 422.

    Pydantic's constrained vocabulary is the anti-hallucination gate: an
    unknown enum, an out-of-range clock, an extra field or a dangling
    reference is rejected here before anything is rendered or sealed.
    """
    try:
        return SceneGraph.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(422, {"error": "invalid SceneGraph",
                                  "detail": json.loads(exc.json())}) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(422, f"scene is not valid JSON: {exc}") from exc


async def _read_uploads(files: list[UploadFile]) -> list[bytes]:
    """Read uploaded photo bytes, enforcing the ingest guardrails at the boundary.

    Bounds are applied so a hostile or oversized upload is a clean 4xx, never a
    5xx / OOM / a disguised executable in the content-addressed store:

      * the file **count** is capped *before any bytes are read* → 413,
      * each file's **size** is capped after read → 413,
      * dangerous **magic bytes** (executables / active markup) are rejected → 400.

    Both upload routes (``/cases/extract``, ``/cases/render``) funnel their
    files through here, so the guard cannot be bypassed on either surface.
    """
    if len(files) > MAX_PHOTOS:
        raise HTTPException(413, f"too many files (max {MAX_PHOTOS})")
    datas: list[bytes] = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(
                413, f"file {f.filename!r} exceeds the "
                     f"{MAX_PHOTO_BYTES // (1024 * 1024)}MB per-file limit")
        try:
            reject_dangerous_bytes(data)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from exc
        datas.append(data)
    return datas


def _uploaded_photos(files: list[UploadFile], datas: list[bytes],
                     roles_csv: str | None) -> list[CasePhoto]:
    roles = [r.strip() for r in roles_csv.split(",")] if roles_csv else []
    photos: list[CasePhoto] = []
    for i, (f, data) in enumerate(zip(files, datas, strict=True)):
        name = f.filename or f"photo_{i}.png"
        role = PhotoRole.scene_photo
        if i < len(roles) and roles[i] in PhotoRole._value2member_map_:
            role = PhotoRole(roles[i])
        else:
            role = _infer_role(name)
        photos.append(CasePhoto(
            filename=name, data=data, media_type=f.content_type or "image/png",
            role=role, source=PhotoSource.user_upload,
        ))
    return photos


def _case_index_match(
    case_id: str, *, kind: str, suffix: str, storage: StorageBackend | None = None
) -> dict | None:
    """Resolve a stored artifact row for a case from the durable index.

    Content-addressed keys embed the hash, so the lookup scans the index by a
    sanitised ``<case>/<kind>/`` prefix (the same sanitisation applied when the
    key was written) + the artifact filename suffix. The index is re-read first
    so a fresh (scale-to-zero) worker that never saw the write still resolves
    the case — a traversal-shaped id can never probe outside its own prefix.

    ``storage`` defaults to the module-level ``_storage`` (the exact original
    behaviour) when omitted; a caller passes a tenant-scoped storage so the
    scan only ever sees that tenant's own rows (see ``_tenant_storage``).
    """
    storage = _storage if storage is None else storage
    if not hasattr(storage, "index"):  # pragma: no cover - defensive
        return None
    reload_index = getattr(storage, "reload_index", None)
    if callable(reload_index):
        reload_index()
    prefix = f"{safe_component(case_id)}/{kind}/"
    return next((r for r in storage.index
                 if r["key"].startswith(prefix) and r["key"].endswith(suffix)), None)


# Manifest artifact name → (index kind, key suffix) for re-fetching the exact
# stored bytes during server-side verification (mirrors the pipeline's keys).
_ARTIFACT_STORAGE = {
    "scene_graph": ("scene", "/scene.json"),
    "timeline": ("timeline", "/timeline.json"),
    "schematic_svg": ("schematic", "/schematic.svg"),
    "schematic_hero": ("schematic", "/schematic.png"),
    "schematic_animation": ("schematic", "/schematic.mp4"),
    "illustration_still": ("illustration", "/illustration.png"),
    "illustration": ("illustration", "/illustration.mp4"),
    "report": ("report", "/report.md"),
}


def _artifact_fetcher(
    case_id: str, storage: StorageBackend | None = None
) -> Callable[[str], bytes | None]:
    """A ``fetch_bytes(name)`` closure that re-reads a case's stored artifact
    bytes from the store (live B2 or offline) for :func:`verify_all`. Returns
    ``None`` for an unknown/absent artifact — never raises, so a failing fetch
    degrades a single verification check, not the whole receipt.

    ``storage`` defaults to the module-level ``_storage`` when omitted; a
    caller passes a tenant-scoped storage so every artifact resolved here
    comes only from that tenant's own rows (see ``_tenant_storage``).
    """
    storage = _storage if storage is None else storage

    def fetch(name: str) -> bytes | None:
        spec = _ARTIFACT_STORAGE.get(name)
        if spec is None:
            return None
        kind, suffix = spec
        match = _case_index_match(case_id, kind=kind, suffix=suffix, storage=storage)
        if not match:
            return None
        try:
            return storage.get(match["key"])
        except Exception:  # pragma: no cover - index row without a live object
            return None
    return fetch


def _is_authenticated_request() -> bool:
    """Whether the current request carries a genuinely authenticated principal.

    The public demo ships without an auth layer, so this is always ``False`` —
    which is exactly why an ``authenticated_human`` request is downgraded to
    ``interactive_demo`` (a demo click can never be sealed as an authenticated
    approval). A future authenticated deployment overrides this one seam."""
    return False


def _resolve_classification(raw: str | None) -> ReviewClassification:
    """Validate + honesty-gate the client's requested review classification.

    An out-of-taxonomy value is a 422 (the closed-enum gate). The server-only
    ``unverified_no_baseline`` cannot be claimed by a client. And absent a
    genuinely authenticated principal, an ``authenticated_human`` request is
    honestly downgraded to ``interactive_demo``."""
    if raw is None:
        return ReviewClassification.interactive_demo
    try:
        requested = ReviewClassification(raw)
    except ValueError as exc:
        raise HTTPException(422, {
            "error": "invalid review_classification",
            "allowed": sorted(c.value for c in CLIENT_REVIEW_CLASSIFICATIONS),
        }) from exc
    if requested not in CLIENT_REVIEW_CLASSIFICATIONS:
        raise HTTPException(422, {
            "error": "review_classification is server-set only", "value": raw})
    if (requested is ReviewClassification.authenticated_human
            and not _is_authenticated_request()):
        return ReviewClassification.interactive_demo
    return requested


def _playback(case_id: str, *, kind: str, suffix: str, default_media: str,
              storage: StorageBackend | None = None) -> Response:
    """Play back a stored case artifact through a stable api-relative URL.

    A 302 to a FRESH presigned GET when the store can mint one (live B2 — the
    canonical storage URL is private), else the bytes streamed straight from
    the store (offline). Presigned URLs are minted per request and never
    persisted, so the sealed manifest keeps the canonical URL + hashes.

    ``storage`` defaults to the module-level ``_storage`` when omitted; a
    caller passes a tenant-scoped storage so playback only ever resolves
    that tenant's own artifact, and the presigned URL is minted for the
    PREFIXED key (see ``_TenantScopedStorage.get_url``), never a bare one.
    """
    storage = _storage if storage is None else storage
    match = _case_index_match(case_id, kind=kind, suffix=suffix, storage=storage)
    if not match:
        raise HTTPException(404, f"no {suffix.lstrip('/')} for case {case_id!r}")
    get_url = getattr(storage, "get_url", None)
    if callable(get_url):
        return RedirectResponse(url=get_url(match["key"]), status_code=302)
    try:
        data = storage.get(match["key"])
    except Exception as exc:  # pragma: no cover - index row without object
        raise HTTPException(404, f"artifact missing for case {case_id!r}") from exc
    return Response(content=data, media_type=match.get("content_type") or default_media)


def _case_body(result: CaseResult, *, degraded_request: bool) -> dict:
    cid = result.case_id
    animation = "schematic_animation" in result.artifacts
    return {
        "case_id": cid,
        "manifest_hash": result.manifest_hash,
        "manifest_url": f"/cases/{quote(cid, safe='')}",
        "provider": result.provider_name,
        # ``degraded`` = the illustration is deterministic offline bytes, not a
        # real generative render (true offline, or on a live-provider fallback).
        "degraded": result.degraded or degraded_request,
        "provider_degraded": degraded_request,
        "has_schematic_animation": animation,
        "schematic_kind": "animation" if animation else "static",
        "schematic_url": f"/cases/{quote(cid, safe='')}/schematic",
        "illustration_url": f"/cases/{quote(cid, safe='')}/illustration",
        "report_markdown": result.payloads["report"].decode("utf-8"),
        "scene": json.loads(scene_to_json(result.scene)),
        "warnings": list(result.scene.confidence_notes),
        "artifacts": {
            name: {"sha256": ref.sha256, "size_bytes": ref.size_bytes,
                   "content_type": ref.content_type, "url": ref.url}
            for name, ref in result.artifacts.items()
        },
    }


def _run_render(scene: SceneGraph, photos: list[CasePhoto], case_id: str, *,
                proposed: SceneGraph | None = None, reviewer_id: str | None = None,
                classification: ReviewClassification = ReviewClassification.interactive_demo,
                prior_notes: list[str] | None = None,
                storage: StorageBackend | None = None) -> dict:
    """Seal the reviewed scene; degrade THIS request honestly on live failure.

    On a live media-provider failure the same reviewed scene + inputs are
    re-sealed with the offline provider against the *same* storage — the core
    action never 500s because a remote generation backend misbehaved. A failure
    of the offline provider itself is a genuine bug and propagates. The sealed
    approval receipt (proposed→confirmed diff, reviewer, classification) rides
    on the ``CaseSpec`` so both the primary run and the degrade re-run seal it.

    ``storage`` defaults to the module-level ``_storage``. The async job
    worker (``_run_render_job``) passes its own isolated storage instead (see
    ``_job_storage``), so a background thread never shares mutable adapter
    state with a request thread — the honest-degrade fallback below then also
    writes to THAT storage, not necessarily the module-level one.
    """
    storage = storage if storage is not None else _storage
    spec = CaseSpec(case_id=case_id, photos=photos, proposed_scene=proposed,
                    reviewer_id=reviewer_id, review_classification=classification,
                    prior_confidence_notes=prior_notes or [])
    extractor = FixedSceneExtractor(scene)
    provider = config.build_provider()
    try:
        result = CasePipeline(extractor, provider, storage).run(spec)
        return _case_body(result, degraded_request=False)
    except Exception as exc:
        if isinstance(provider, FakeMediaProvider):
            raise
        _log.exception(
            "live media provider %r failed for case %r; re-sealing this "
            "request with the offline provider (storage unchanged)",
            getattr(provider, "name", "?"), case_id,
        )
        result = CasePipeline(extractor, FakeMediaProvider(), storage).run(spec)
        body = _case_body(result, degraded_request=True)
        body["degrade_reason"] = type(exc).__name__
        return body


# ── Optional per-user multitenancy (see claimscene.auth) ───────────────────────
# Ported from our other MIT entry, Cinemory, which pioneered this pattern. A
# signed-in caller's cases are isolated to their OWN tenant prefix — BY
# CONSTRUCTION, not by a check that could be forgotten: every lookup in this
# module resolves a case through a storage object's ``index``, and
# ``_TenantScopedStorage.index`` physically cannot contain a row outside its
# own prefix (see its docstring). Guest (no/absent header, or multitenancy off
# entirely — see ``get_tenant``) always resolves to the SAME objects this API
# used before this section existed (``_storage`` directly, no wrapper), so
# guest behaviour is byte-identical.


def get_tenant(authorization: Annotated[str | None, Header()] = None) -> str | None:
    """FastAPI dependency: the caller's verified tenant id, or ``None`` for guest.

    - ``FIREBASE_PROJECT_ID`` unset -> multitenancy is OFF for this deployment:
      ALWAYS returns ``None``, even when ``authorization`` is present — this is
      what keeps a deploy with no such env var configured (the current
      production default) behaving exactly as it did before this dependency
      existed. The header is not even inspected in that case.
    - No/empty ``authorization`` -> ``None`` (GUEST; not an error).
    - A well-formed, valid ``Bearer <token>`` -> the token's verified ``uid``.
    - A PRESENT but invalid/expired/malformed credential -> ``HTTPException
      (401)``. A bad credential must never silently downgrade to guest (see
      ``auth.uid_from_authorization``'s own contract).

    The tenant id comes ONLY from here — the verified token's ``sub`` claim.
    Nothing in this module ever reads a tenant from a query param, request
    body field, cookie, or any header other than ``Authorization``, so it is
    structurally impossible for a caller to set their own tenant.
    """
    project_id = os.environ.get(auth.PROJECT_ID_ENV_VAR)
    if not project_id:
        return None
    try:
        return auth.uid_from_authorization(authorization, project_id=project_id)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc


def _tenant_prefix(uid: str) -> str:
    """The storage-key prefix a verified tenant's data lives under."""
    return f"tenants/{safe_component(uid)}/"


class _TenantScopedStorage:
    """A tenant-prefixing view over a base :class:`~claimscene.ports.StorageBackend`
    — the isolation core of multitenancy.

    Every key a caller passes to ``put``/``get``/``exists``/``get_url``/
    ``delete`` is a TENANT-RELATIVE key; ``prefix`` is prepended before it ever
    reaches the base backend. ``index`` does the reverse for reads: it returns
    ONLY the base backend's rows whose key starts with ``prefix``, with the
    prefix stripped back off — so the existing ``<case>/<kind>/<shard>/
    <sha256>/<file>`` matching in ``_case_index_match``/``_artifact_fetcher``
    (and ``claimscene.jobs``' ``jobs/<id>/status.json`` keys) works completely
    unchanged against a tenant-scoped view, and a tenant's index scan is
    PHYSICALLY UNABLE to enumerate a row outside its own prefix — a
    cross-tenant (or guest) lookup 404s because the row is never in the list to
    begin with, not because a comparison happened to reject it.

    There is no method on this class parameterised by anything a caller
    controls: ``prefix`` is fixed at construction from a VERIFIED uid (see
    ``_tenant_storage``), so nothing reachable through this object can ever
    address a key outside it.

    ``get_url``/``delete`` are exposed only when the base backend has them —
    mirrors this codebase's existing ``getattr(storage, "get_url", None)``
    capability-probing pattern, so a InMemoryStorage-backed tenant (no
    ``get_url``) falls through to the byte-streaming playback path exactly
    like guest InMemoryStorage does, instead of a broken redirect.

    Ported from Cinemory's identical class (that repo's own multitenancy
    isolation core).
    """

    def __init__(self, base: StorageBackend, prefix: str) -> None:
        self._base = base
        self._prefix = prefix

        base_get_url = getattr(base, "get_url", None)
        if callable(base_get_url):
            def get_url(key: str, *, expires_in: int = 3600) -> str:
                return base_get_url(f"{prefix}{key}", expires_in=expires_in)
            self.get_url = get_url

        base_delete = getattr(base, "delete", None)
        if callable(base_delete):
            def delete(key: str) -> None:
                return base_delete(f"{prefix}{key}")
            self.delete = delete

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        return self._base.put(f"{self._prefix}{key}", data, content_type=content_type)

    def get(self, key: str) -> bytes:
        return self._base.get(f"{self._prefix}{key}")

    def exists(self, key: str) -> bool:
        return self._base.exists(f"{self._prefix}{key}")

    @property
    def index(self) -> list[dict]:
        if not hasattr(self._base, "index"):  # pragma: no cover - defensive
            raise AttributeError("index")
        return [
            {**row, "key": row["key"][len(self._prefix):]}
            for row in self._base.index
            if row.get("key", "").startswith(self._prefix)
        ]

    def reload_index(self) -> list[dict]:
        """Always safe to call, regardless of base capability — mirrors this
        codebase's ``if callable(reload_index): reload_index()`` opportunistic
        pattern; a base without one is just a no-op here."""
        reload = getattr(self._base, "reload_index", None)
        if callable(reload):
            reload()
        return self.index


def _tenant_storage(uid: str | None) -> StorageBackend:
    """The request-time storage for ``uid`` — tenant-scoped, or GUEST.

    GUEST (``uid is None``) returns the EXACT module-level ``_storage``
    object — no wrapper — so every existing guest code path (including tests
    that monkeypatch ``api._storage`` directly) is byte-identical to before
    multitenancy existed. An authed uid gets a fresh ``_TenantScopedStorage``
    over that SAME shared storage.
    """
    if uid is None:
        return _storage
    return _TenantScopedStorage(_storage, _tenant_prefix(uid))


# ── routes ───────────────────────────────────────────────────────────────────
def _build_info() -> dict:
    """Which commit the running image was built from, and when.

    Baked into the image at build time (see ``Dockerfile`` and
    ``deploy/cloudbuild.yaml``), so anyone can confirm that a deployment really
    is the commit they are reading on GitHub, without access to the project:

        curl -s <url>/health | jq -r .build.commit

    Both values are ``None`` when the app was not built through that path (a
    local run, or CI), which is the honest answer rather than a fabricated one.
    A commit hash is public information, so exposing it leaks nothing.
    """
    return {
        "commit": os.environ.get("CLAIMSCENE_BUILD_SHA") or None,
        "built_at": os.environ.get("CLAIMSCENE_BUILD_TIME") or None,
    }


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "claimscene-api",
        "mode": config.mode(),
        # Effective backends after credential-aware resolution, so a live deploy
        # running without creds is visibly degraded (not silently mislabelled).
        "provider": _provider.name,
        "extractor": _extractor.name,
        "storage": type(_storage).__name__,
        "build": _build_info(),
    }


@app.get("/scenarios")
def get_scenarios() -> dict:
    """The committed synthetic scenarios — judges run the whole app, review
    loop included, without supplying their own accident photos."""
    return {"scenarios": scenarios.list_scenarios()}


@app.get("/scenarios/{scenario_id}/images/{name}")
def scenario_image(scenario_id: str, name: str) -> Response:
    """Serve one scenario thumbnail. Both path segments are sanitised and the
    resolved path is asserted to stay inside the scenarios directory."""
    try:
        path = scenarios.resolve_image_path(scenario_id, name)
    except scenarios.ScenarioError as exc:
        raise HTTPException(404, str(exc)) from exc
    media = scenarios._EXT_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return Response(content=path.read_bytes(), media_type=media,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/cases/extract")
async def extract_case(
    context: Annotated[str | None, Form()] = None,
    scenario_id: Annotated[str | None, Form()] = None,
    roles: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    """Propose a constrained SceneGraph from uploaded photos OR a scenario id.

    Offline + a scenario id → we return the scenario's shipped ground-truth
    scene (labelled ``committed_ground_truth``, NOT dressed up as an
    extraction); live mode runs the VLM ladder on the scenario photos. Uploaded
    photos always run the configured extractor (offline fake / live VLM).
    """
    files = files or []
    if scenario_id:
        try:
            photos = scenarios.scenario_photos(scenario_id)
        except scenarios.ScenarioError as exc:
            raise HTTPException(404, str(exc)) from exc
        if isinstance(_extractor, FakeVisionExtractor):
            scene = _parse_scene(scenarios.scenario_truth_json(scenario_id))
            scene.confidence_notes = [
                "offline sample: shipped ground-truth scene for this scenario; "
                "live mode runs the VLM ladder on the photos"
            ]
            source, mechanism = "committed_ground_truth", "committed-ground-truth"
        else:
            scene = _extractor.extract(photos, context=context)
            source, mechanism = "vlm_extraction", _extractor.name
    elif files:
        datas = await _read_uploads(files)
        if not any(datas):
            raise HTTPException(400, "uploaded files are empty")
        photos = _uploaded_photos(files, datas, roles)
        scene = _extractor.extract(photos, context=context)
        source = ("vlm_extraction" if not isinstance(_extractor, FakeVisionExtractor)
                  else "fake_extraction")
        mechanism = _extractor.name
    else:
        raise HTTPException(400, "provide photo files or a scenario_id")

    return {
        "scene": json.loads(scene_to_json(scene)),
        "inputs": [input_record(p) for p in photos],
        "extraction": {"extractor": mechanism, "source": source,
                       "mode": config.mode()},
    }


@app.post("/cases/preview-schematic")
def preview_schematic(scene: SceneGraph) -> dict:
    """Reviewed SceneGraph → fast STATIC top-down schematic (SVG) for live
    review feedback. No video, no illustration, nothing stored — this is the
    tight edit loop the reviewer works against.

    Warnings are recomputed server-side from the geometry; any client-sent
    ``confidence_notes`` are ignored (never trusted into feedback)."""
    timeline = LayoutEngine().build(scene)
    svg = build_static_svg(timeline, title="preview")
    warnings = semantic_warnings(scene) + list(timeline.notes)
    return {
        "svg": svg,
        "warnings": warnings,
        "contact_point": (list(timeline.contact_point)
                          if timeline.contact_point else None),
        "vehicle_count": len(scene.vehicles),
        "road": {"layout": scene.road.layout.value,
                 "lanes_per_direction": scene.road.lanes_per_direction,
                 "signal": scene.road.signal.value},
    }


class _RenderInputs(NamedTuple):
    """Parsed + validated render inputs — shared by the sync (``POST
    /cases/render``) and async (``POST /cases/render/jobs``) submit paths, so
    both run the identical ingest + 422-vocabulary checks before any
    generation starts."""

    scene: SceneGraph
    photos: list[CasePhoto]
    case_id: str
    proposed: SceneGraph | None
    reviewer_id: str | None
    classification: ReviewClassification
    prior_notes: list[str]


async def _prepare_render(
    scene: str, case_id: str, *, scenario_id: str | None, roles: str | None,
    proposed_scene: str | None, reviewer_id: str | None,
    review_classification: str | None, files: list[UploadFile],
) -> _RenderInputs:
    """Ingest validation shared by every render path — sync AND async.

    Inputs are resolved (in order): a ``scenario_id`` → the committed sample
    views; else uploaded ``files``; else a single staged placeholder so a
    type-a-scene flow still seals honestly. The reviewed scene's
    ``confidence_notes`` are reset server-side to a human-in-the-loop note, so
    only server-derived text is sealed. The AI's real notes are captured into
    ``prior_notes`` BEFORE that reset, so an approval receipt can carry them
    forward non-destructively. Raises the identical ``HTTPException`` (422 for
    a hallucinated/malformed scene, 404 for an unknown scenario) either path
    would raise — this runs entirely before any job is created or any
    generation starts.
    """
    reviewed = _parse_scene(scene)
    prior_notes = list(reviewed.confidence_notes)
    reviewed.confidence_notes = [
        "scene reviewed and confirmed by a human operator before rendering "
        "(human-in-the-loop)"
    ]
    proposed = _parse_scene(proposed_scene) if proposed_scene else None
    classification = _resolve_classification(review_classification)

    if scenario_id:
        try:
            photos = scenarios.scenario_photos(scenario_id)
        except scenarios.ScenarioError as exc:
            raise HTTPException(404, str(exc)) from exc
    elif files:
        datas = await _read_uploads(files)
        photos = _uploaded_photos(files, datas, roles)
    else:
        photos = [CasePhoto(filename="staged_input.png", data=_PNG_1x1,
                            role=PhotoRole.scene_photo,
                            source=PhotoSource.staged_demo,
                            attribution="staged placeholder (no photo supplied)",
                            license="CC0-1.0")]

    # A unique, url-safe id per render so each sealed case is unambiguously
    # addressable (content-addressed keys otherwise collide on the case prefix).
    effective_id = f"{safe_component(case_id) or 'case'}-{uuid.uuid4().hex[:8]}"
    return _RenderInputs(reviewed, photos, effective_id, proposed, reviewer_id,
                         classification, prior_notes)


@app.post("/cases/render")
async def render_case(
    scene: Annotated[str, Form()],
    tenant: Annotated[str | None, Depends(get_tenant)],
    case_id: Annotated[str, Form()] = "case",
    scenario_id: Annotated[str | None, Form()] = None,
    roles: Annotated[str | None, Form()] = None,
    proposed_scene: Annotated[str | None, Form()] = None,
    reviewer_id: Annotated[str | None, Form()] = None,
    review_classification: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    """Seal a human-reviewed SceneGraph into a full, verifiable case.

    Inputs are resolved (in order): a ``scenario_id`` → the committed sample
    views; else uploaded ``files``; else a single staged placeholder so a
    type-a-scene flow still seals honestly. The reviewed scene's
    ``confidence_notes`` are reset server-side to a human-in-the-loop note, so
    only server-derived text is sealed.

    When an AI-``proposed_scene`` is supplied, the server seals a signed
    approval receipt — the proposed→confirmed field diff it computes itself
    (never the client's), the ``reviewer_id``, and an honesty-gated
    ``review_classification``. Absent one, an honest ``unverified_no_baseline``
    receipt is sealed. The AI's real notes are carried into the receipt before
    the confirmed scene's notes are reset (non-destructive).

    A signed-in ``tenant`` (see ``get_tenant``) seals into their own storage
    prefix; GUEST (``tenant is None``) seals at the unprefixed root exactly as
    before multitenancy existed (``_tenant_storage(None)`` is the module-level
    ``_storage`` itself).
    """
    ri = await _prepare_render(
        scene, case_id, scenario_id=scenario_id, roles=roles,
        proposed_scene=proposed_scene, reviewer_id=reviewer_id,
        review_classification=review_classification, files=files or [],
    )
    return _run_render(ri.scene, ri.photos, ri.case_id, proposed=ri.proposed,
                       reviewer_id=ri.reviewer_id, classification=ri.classification,
                       prior_notes=ri.prior_notes, storage=_tenant_storage(tenant))


# ── Async render submission (submit + poll) ─────────────────────────────────
# See the ``claimscene.jobs`` module for the job-store design and its honest
# limitation (the worker below runs in-process on the accepting instance).


def _job_storage() -> StorageBackend:
    """Storage instance a background job thread may safely read/write.

    ``InMemoryStorage`` (offline/tests) is pure in-memory with no remote
    substrate — a FRESH instance here would be invisible to request threads
    (including this same process's own ``GET /cases/render/jobs/{job_id}``
    reads), so the shared module-level ``_storage`` is reused; its plain
    dict/list mutations are safe to share across threads under the GIL (no
    read-modify-write cycle).

    ``B2Storage`` (live) is different: it keeps a mutable local ``index`` list
    that a concurrent ``put`` from another thread could also be mutating.
    Sharing the SAME Python object — and its local ``index`` list — between
    this background thread and request-serving threads would add an avoidable
    race (a shared list can be reassigned/appended by one thread while another
    thread is mid-iteration over the old reference — a lost update). So live
    mode gets its own fresh ``B2Storage`` per job; its constructor re-reads the
    bucket's ``index.jsonl``, so nothing already-durable is lost — the bucket,
    not the Python object, is the source of truth.

    Adapted from Cinemory's identical helper. ``B2Storage._persist_index``
    (this repo, ``adapters/b2_storage.py``) now does the same remote
    read-modify-write MERGE (by key) before every write that Cinemory's does,
    so two independent ``B2Storage`` instances (e.g. a job's and a concurrent
    request's) writing around the same time converge instead of
    last-writer-wins clobbering each other's index rows. Giving the job its
    own instance still matters — see ``_persist_index``'s own docstring for
    the small residual read-modify-write race window merge-on-write does not
    close (accepted, not eliminated) — but the routine data-loss failure mode
    this docstring used to describe is fixed.
    """
    if isinstance(_storage, InMemoryStorage):
        return _storage
    return config.build_storage()  # pragma: no cover - exercised only in live mode


def _tenant_job_storage(uid: str | None) -> StorageBackend:
    """Like ``_tenant_storage``, but for the background-job worker thread.

    GUEST (``uid is None``) returns EXACTLY ``_job_storage()`` — the identical
    call the pre-multitenancy worker always made (see that function's own
    docstring for why the background thread needs its own storage instance in
    live mode). An authed uid wraps that SAME choice with the tenant prefix,
    so a tenant's jobs live under their own prefix too and only ever poll
    their own (``jobs/<id>/status.json`` becomes
    ``tenants/<uid>/jobs/<id>/status.json``).
    """
    if uid is None:
        return _job_storage()
    return _TenantScopedStorage(_job_storage(), _tenant_prefix(uid))


def _run_render_job(
    job_id: str, scene: SceneGraph, photos: list[CasePhoto], case_id: str, *,
    proposed: SceneGraph | None, reviewer_id: str | None,
    classification: ReviewClassification, prior_notes: list[str],
    uid: str | None = None,
) -> None:
    """Background worker thread: run the render, updating the stored status.

    Runs the SAME honest-degrade path the synchronous endpoint uses
    (``_run_render``), against an isolated storage instance (see
    ``_job_storage``) so this thread never mutates state a request thread also
    touches. Never raises out of the thread and never produces an HTTP 500 for
    a generation failure: caught here and recorded as status ``failed`` with
    the exception CLASS NAME only (mirrors ``_run_render``'s
    ``degrade_reason``) — the full traceback goes to the log.

    ``uid``: the verified tenant id for a tenant-scoped submission, or
    ``None`` for guest — the exact, unchanged behaviour this function always
    had (every statement below is identical to before multitenancy existed
    when ``uid is None``, since ``_tenant_job_storage(None)`` is exactly
    ``_job_storage()``).
    """
    storage = _tenant_job_storage(uid)
    try:
        jobs.mark_running(storage, job_id)
        result = _run_render(scene, photos, case_id, proposed=proposed,
                             reviewer_id=reviewer_id, classification=classification,
                             prior_notes=prior_notes, storage=storage)
        jobs.mark_done(storage, job_id, result)
    except Exception as exc:
        _log.exception("background render job %r failed", job_id)
        jobs.mark_failed(storage, job_id, exc)


@app.post("/cases/render/jobs", status_code=202)
async def create_render_job(
    scene: Annotated[str, Form()],
    tenant: Annotated[str | None, Depends(get_tenant)],
    case_id: Annotated[str, Form()] = "case",
    scenario_id: Annotated[str | None, Form()] = None,
    roles: Annotated[str | None, Form()] = None,
    proposed_scene: Annotated[str | None, Form()] = None,
    reviewer_id: Annotated[str | None, Form()] = None,
    review_classification: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    """Submit a render as a background job; poll ``GET /cases/render/jobs/{id}``.

    Accepts the IDENTICAL body as ``POST /cases/render`` — same ingest
    validation, same 422 constrained-vocabulary rejection, same 404 for an
    unknown scenario — but returns immediately (``202``) with a job id instead
    of blocking for the full two-step generation. See the module docstring and
    ``claimscene.jobs`` for why (edge proxy timeouts) and for the honest
    limitation of the in-process worker.

    GUEST (``tenant is None``) is byte-identical to before multitenancy:
    ``_tenant_job_storage(None)`` is exactly ``_job_storage()`` and
    ``_run_render_job(..., uid=None)`` runs the exact original job body.
    """
    ri = await _prepare_render(
        scene, case_id, scenario_id=scenario_id, roles=roles,
        proposed_scene=proposed_scene, reviewer_id=reviewer_id,
        review_classification=review_classification, files=files or [],
    )
    job_id = jobs.new_job_id()
    jobs.create(_tenant_job_storage(tenant), job_id)
    threading.Thread(
        target=_run_render_job,
        args=(job_id, ri.scene, ri.photos, ri.case_id),
        kwargs={
            "proposed": ri.proposed, "reviewer_id": ri.reviewer_id,
            "classification": ri.classification, "prior_notes": ri.prior_notes,
            "uid": tenant,
        },
        daemon=True, name=f"claimscene-job-{job_id}",
    ).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/cases/render/jobs/{job_id}")
def get_render_job(job_id: str, tenant: Annotated[str | None, Depends(get_tenant)]) -> dict:
    """Poll a submitted render job's status.

    404 for an unknown job id. A stored-but-unreadable status object degrades
    to the identical 404 (see ``claimscene.jobs.read``) rather than a 500 —
    from the caller's side an unreadable job and a nonexistent one are the
    same "nothing to show" answer.

    Tenant-scoped the same way ``create_render_job`` writes: a tenant's jobs
    live under their own prefix (see ``_tenant_job_storage``), so polling a
    known job id under someone else's tenant (or as guest) resolves to no
    object under THIS caller's prefix — the identical 404 as an unknown id.
    GUEST (``tenant is None``) reads via ``_tenant_storage(None)``, which is
    exactly the module-level ``_storage`` — byte-identical to before.
    """
    status = jobs.read(_tenant_storage(tenant), job_id)
    if status is None:
        raise HTTPException(404, f"no job {job_id!r}")
    return status


@app.get("/cases/{case_id}")
def get_case(case_id: str, tenant: Annotated[str | None, Depends(get_tenant)]) -> Response:
    """The sealed manifest, served as the RAW canonical bytes so the browser
    can recompute the SHA-256 seal exactly (in-browser Verify)."""
    storage = _tenant_storage(tenant)
    match = _case_index_match(case_id, kind="manifest", suffix="/manifest.json", storage=storage)
    if not match:
        raise HTTPException(404, f"no case named {case_id!r}")
    return Response(content=storage.get(match["key"]), media_type="application/json")


@app.get("/cases/{case_id}/verify")
def verify_case(case_id: str, tenant: Annotated[str | None, Depends(get_tenant)]) -> dict:
    """Server-side re-verification of a sealed case as a named-check receipt.

    Re-fetches the manifest + every recorded artifact from the store and re-runs
    each check from those bytes (:func:`verify_all`) — the artifact hashes, the
    seal, the disclosure/attribution/watermark structural invariants, and the
    approval receipt's self-consistency. Honest-degrade: a genuinely missing
    case is a 404; anything else returns a fully-shaped receipt (never a 500),
    with failing checks doing the talking.
    """
    storage = _tenant_storage(tenant)
    match = _case_index_match(case_id, kind="manifest", suffix="/manifest.json", storage=storage)
    if not match:
        raise HTTPException(404, f"no case named {case_id!r}")
    try:
        manifest = json.loads(storage.get(match["key"]))
    except Exception:  # unreadable bytes → a fully-shaped failing receipt, not a 500
        manifest = {}
    return verify_all(manifest, _artifact_fetcher(case_id, storage)).to_dict()


@app.get("/cases/{case_id}/receipt")
def get_case_receipt(
    case_id: str, tenant: Annotated[str | None, Depends(get_tenant)]
) -> Response:
    """The detached, self-sealed verification receipt as the RAW canonical bytes.

    Stored as its own small object alongside the case artifacts when the case
    seals (see :func:`~claimscene.provenance.build_detached_receipt`). Serving
    the raw bytes lets a client recompute ``receipt_digest`` exactly and bind
    the cited illustration/review digests back to the manifest — an
    independently re-verifiable attestation, not a live recomputation. A case
    that has no stored receipt is a 404.
    """
    storage = _tenant_storage(tenant)
    match = _case_index_match(case_id, kind="receipt", suffix="/receipt.json", storage=storage)
    if not match:
        raise HTTPException(404, f"no receipt for case {case_id!r}")
    return Response(content=storage.get(match["key"]), media_type="application/json")


@app.get("/cases/{case_id}/schematic")
def get_case_schematic(
    case_id: str, tenant: Annotated[str | None, Depends(get_tenant)]
) -> Response:
    """Factual-layer playback: the animated schematic MP4 when it exists
    (ffmpeg present — live/container), else the deterministic hero PNG. The
    factual layer is real in every mode, so this route always resolves."""
    storage = _tenant_storage(tenant)
    if _case_index_match(case_id, kind="schematic", suffix="/schematic.mp4", storage=storage):
        return _playback(case_id, kind="schematic", suffix="/schematic.mp4",
                         default_media="video/mp4", storage=storage)
    return _playback(case_id, kind="schematic", suffix="/schematic.png",
                     default_media="image/png", storage=storage)


@app.get("/cases/{case_id}/illustration")
def get_case_illustration(
    case_id: str, tenant: Annotated[str | None, Depends(get_tenant)]
) -> Response:
    """Illustration clip playback (302 presign live / stream offline). Offline
    the bytes are the deterministic fake — the client shows the disclosed
    placeholder rather than feeding them to a <video>."""
    storage = _tenant_storage(tenant)
    return _playback(case_id, kind="illustration", suffix="/illustration.mp4",
                     default_media="video/mp4", storage=storage)


@app.get("/me/library")
def get_my_library(tenant: Annotated[str | None, Depends(get_tenant)]) -> dict:
    """List the authed tenant's own cases, from THEIR tenant-scoped index only.

    401 for guest (``tenant is None``) — this route only ever makes sense for
    an authenticated caller. The listing is isolated by construction: a
    ``_TenantScopedStorage.index`` scan can physically only contain rows under
    this caller's own ``tenants/<uid>/`` prefix (see that class's docstring),
    so this can never leak another tenant's — or a guest's — cases.
    """
    if tenant is None:
        raise HTTPException(401, "sign in required")
    storage = _tenant_storage(tenant)
    reload_index = getattr(storage, "reload_index", None)
    if callable(reload_index):
        reload_index()

    cases: list[dict] = []
    for row in storage.index:
        key = row["key"]
        parts = key.split("/")
        if len(parts) < 2 or parts[1] != "manifest" or not key.endswith("/manifest.json"):
            continue
        entry: dict = {"case_id": parts[0], "manifest_hash": None, "created_at": None}
        try:
            manifest = json.loads(storage.get(key))
        except Exception:  # unreadable manifest → still list the case, best-effort
            manifest = None
        if isinstance(manifest, dict):
            entry["manifest_hash"] = manifest.get("manifest_hash")
            entry["created_at"] = manifest.get("created_at")
        cases.append(entry)
    return {"cases": cases}


@app.delete("/me/data")
def delete_my_data(tenant: Annotated[str | None, Depends(get_tenant)]) -> dict:
    """Delete every object under the authed tenant's own storage prefix.

    401 for guest (``tenant is None``). The deletion is STRUCTURALLY scoped:
    ``storage`` is a ``_TenantScopedStorage`` built from the VERIFIED uid (see
    ``_tenant_storage``/``_tenant_prefix``), so every key this loop can ever
    construct is ``tenants/<uid>/<key>`` — there is no code path here that can
    address a guest key (no prefix) or another tenant's prefix. Deleting each
    key also removes its row from the BASE storage's own index (see
    ``InMemoryStorage.delete``/``B2Storage.delete``), so this tenant's
    ``index`` (and therefore ``/me/library``) is empty afterward with no
    separate index-cleanup step needed.
    """
    if tenant is None:
        raise HTTPException(401, "sign in required")
    storage = _tenant_storage(tenant)
    reload_index = getattr(storage, "reload_index", None)
    if callable(reload_index):
        reload_index()
    keys = [row["key"] for row in storage.index]  # materialise before mutating
    for key in keys:
        storage.delete(key)
    return {"deleted": len(keys)}


# Serve the compiled web client from the same origin as the API (single Cloud
# Run container / single port). Mounted LAST + guarded by directory existence,
# so the explicit API routes win and CI/local runs without a built client are
# unaffected. CLAIMSCENE_WEB_DIR points at the folder holding index.html.
_WEB_DIR = Path(os.environ.get("CLAIMSCENE_WEB_DIR", "")).expanduser()
if _WEB_DIR.is_dir() and (_WEB_DIR / "index.html").is_file():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")


def main() -> None:  # pragma: no cover - manual entrypoint
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
