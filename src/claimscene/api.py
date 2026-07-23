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
GET  /cases/{id}                      the sealed manifest (raw, for in-browser verify)
GET  /cases/{id}/schematic            factual-layer playback (mp4 live / png offline)
GET  /cases/{id}/illustration         illustration playback (302 presign live / stream)

Offline by default: every route works with zero credentials on the
deterministic fakes. In ``live`` mode the real backends are used only when
their credentials are present, and a live-provider failure degrades *this
request* to the offline provider (storage untouched) — the response says so
honestly and the sealed manifest records the provider that actually ran.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from . import config, scenarios
from .adapters import FakeMediaProvider, FakeVisionExtractor
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


def _case_index_match(case_id: str, *, kind: str, suffix: str) -> dict | None:
    """Resolve a stored artifact row for a case from the durable index.

    Content-addressed keys embed the hash, so the lookup scans the index by a
    sanitised ``<case>/<kind>/`` prefix (the same sanitisation applied when the
    key was written) + the artifact filename suffix. The index is re-read first
    so a fresh (scale-to-zero) worker that never saw the write still resolves
    the case — a traversal-shaped id can never probe outside its own prefix.
    """
    if not hasattr(_storage, "index"):  # pragma: no cover - defensive
        return None
    reload_index = getattr(_storage, "reload_index", None)
    if callable(reload_index):
        reload_index()
    prefix = f"{safe_component(case_id)}/{kind}/"
    return next((r for r in _storage.index
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


def _artifact_fetcher(case_id: str) -> Callable[[str], bytes | None]:
    """A ``fetch_bytes(name)`` closure that re-reads a case's stored artifact
    bytes from the store (live B2 or offline) for :func:`verify_all`. Returns
    ``None`` for an unknown/absent artifact — never raises, so a failing fetch
    degrades a single verification check, not the whole receipt."""
    def fetch(name: str) -> bytes | None:
        spec = _ARTIFACT_STORAGE.get(name)
        if spec is None:
            return None
        kind, suffix = spec
        match = _case_index_match(case_id, kind=kind, suffix=suffix)
        if not match:
            return None
        try:
            return _storage.get(match["key"])
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


def _playback(case_id: str, *, kind: str, suffix: str,
              default_media: str) -> Response:
    """Play back a stored case artifact through a stable api-relative URL.

    A 302 to a FRESH presigned GET when the store can mint one (live B2 — the
    canonical storage URL is private), else the bytes streamed straight from
    the store (offline). Presigned URLs are minted per request and never
    persisted, so the sealed manifest keeps the canonical URL + hashes.
    """
    match = _case_index_match(case_id, kind=kind, suffix=suffix)
    if not match:
        raise HTTPException(404, f"no {suffix.lstrip('/')} for case {case_id!r}")
    get_url = getattr(_storage, "get_url", None)
    if callable(get_url):
        return RedirectResponse(url=get_url(match["key"]), status_code=302)
    try:
        data = _storage.get(match["key"])
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
                prior_notes: list[str] | None = None) -> dict:
    """Seal the reviewed scene; degrade THIS request honestly on live failure.

    On a live media-provider failure the same reviewed scene + inputs are
    re-sealed with the offline provider against the *same* storage — the core
    action never 500s because a remote generation backend misbehaved. A failure
    of the offline provider itself is a genuine bug and propagates. The sealed
    approval receipt (proposed→confirmed diff, reviewer, classification) rides
    on the ``CaseSpec`` so both the primary run and the degrade re-run seal it.
    """
    spec = CaseSpec(case_id=case_id, photos=photos, proposed_scene=proposed,
                    reviewer_id=reviewer_id, review_classification=classification,
                    prior_confidence_notes=prior_notes or [])
    extractor = FixedSceneExtractor(scene)
    provider = config.build_provider()
    try:
        result = CasePipeline(extractor, provider, _storage).run(spec)
        return _case_body(result, degraded_request=False)
    except Exception as exc:
        if isinstance(provider, FakeMediaProvider):
            raise
        _log.exception(
            "live media provider %r failed for case %r; re-sealing this "
            "request with the offline provider (storage unchanged)",
            getattr(provider, "name", "?"), case_id,
        )
        result = CasePipeline(extractor, FakeMediaProvider(), _storage).run(spec)
        body = _case_body(result, degraded_request=True)
        body["degrade_reason"] = type(exc).__name__
        return body


# ── routes ───────────────────────────────────────────────────────────────────
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


@app.post("/cases/render")
async def render_case(
    scene: Annotated[str, Form()],
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
    """
    files = files or []
    reviewed = _parse_scene(scene)
    # Capture the AI's real notes BEFORE the human-in-the-loop reset, so the
    # approval receipt can carry them forward non-destructively.
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
    return _run_render(reviewed, photos, effective_id, proposed=proposed,
                       reviewer_id=reviewer_id, classification=classification,
                       prior_notes=prior_notes)


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> Response:
    """The sealed manifest, served as the RAW canonical bytes so the browser
    can recompute the SHA-256 seal exactly (in-browser Verify)."""
    match = _case_index_match(case_id, kind="manifest", suffix="/manifest.json")
    if not match:
        raise HTTPException(404, f"no case named {case_id!r}")
    return Response(content=_storage.get(match["key"]), media_type="application/json")


@app.get("/cases/{case_id}/verify")
def verify_case(case_id: str) -> dict:
    """Server-side re-verification of a sealed case as a named-check receipt.

    Re-fetches the manifest + every recorded artifact from the store and re-runs
    each check from those bytes (:func:`verify_all`) — the artifact hashes, the
    seal, the disclosure/attribution/watermark structural invariants, and the
    approval receipt's self-consistency. Honest-degrade: a genuinely missing
    case is a 404; anything else returns a fully-shaped receipt (never a 500),
    with failing checks doing the talking.
    """
    match = _case_index_match(case_id, kind="manifest", suffix="/manifest.json")
    if not match:
        raise HTTPException(404, f"no case named {case_id!r}")
    try:
        manifest = json.loads(_storage.get(match["key"]))
    except Exception:  # unreadable bytes → a fully-shaped failing receipt, not a 500
        manifest = {}
    return verify_all(manifest, _artifact_fetcher(case_id)).to_dict()


@app.get("/cases/{case_id}/schematic")
def get_case_schematic(case_id: str) -> Response:
    """Factual-layer playback: the animated schematic MP4 when it exists
    (ffmpeg present — live/container), else the deterministic hero PNG. The
    factual layer is real in every mode, so this route always resolves."""
    if _case_index_match(case_id, kind="schematic", suffix="/schematic.mp4"):
        return _playback(case_id, kind="schematic", suffix="/schematic.mp4",
                         default_media="video/mp4")
    return _playback(case_id, kind="schematic", suffix="/schematic.png",
                     default_media="image/png")


@app.get("/cases/{case_id}/illustration")
def get_case_illustration(case_id: str) -> Response:
    """Illustration clip playback (302 presign live / stream offline). Offline
    the bytes are the deterministic fake — the client shows the disclosed
    placeholder rather than feeding them to a <video>."""
    return _playback(case_id, kind="illustration", suffix="/illustration.mp4",
                     default_media="video/mp4")


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
