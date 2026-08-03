"""Case orchestration: photos → scene → schematic → illustration → report,
sealed end-to-end with content-addressed provenance.

The orchestrator depends only on ports (``VisionExtractor``,
``MediaProvider``, ``StorageBackend``, ``Renderer``), so the exact same code
path runs against the real adapters or the offline fakes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .camera import SEED_SCALE as CAMERA_SEED_SCALE
from .camera import apply_camera_push
from .case import CaseSpec, ReviewClassification
from .evaluation import diff_scenes, review_counts
from .keys import KeyStrategy, make_key
from .layout import LayoutEngine, Timeline, timeline_to_json
from .ports import MediaProvider, Renderer, StorageBackend, VisionExtractor
from .provenance import (
    AUTHORSHIP_NOTE,
    DISCLOSURE,
    WATERMARK,
    build_detached_receipt,
    build_manifest,
    build_review,
    canonical_json,
    input_record,
    sha256_bytes,
    verify_all,
)
from .report import (
    CAMERA_PUSH_NOTE,
    ILLUSTRATION_NEGATIVE_PROMPT,
    ILLUSTRATION_SEED_NOTE,
    build_report,
    illustration_prompt,
)
from .scene import SceneGraph, scene_to_json, semantic_warnings
from .schematic import PillowSchematicRenderer
from .watermark import burn_clip_watermark, burn_still_watermark

# ── illustration clip generation params ──────────────────────────────────────
# Determined offline (no live provider key available): the SDK's own
# ModelSpec for ``pixverse-v6-i2v`` carries an EMPTY ``param_schemas`` -- it
# performs no value validation for either ``quality`` or ``resolution``, so
# the accepted set for either field genuinely cannot be confirmed against
# the SDK alone. What IS confirmed: the Pixverse model family's own
# docstring (``genblaze_gmicloud.models.video``) states ``quality`` is
# "required by the upstream API" for this family (the field this pipeline
# already sent successfully at "360p"), and
# ``genblaze_core.providers.canonical_params.RESOLUTIONS_TIERED`` -- the
# SDK's own portable resolution vocabulary -- lists "720p" alongside
# 480p/1080p/1440p/4k as a standard tier (notably, "360p" is NOT in that
# set, despite demonstrably working today). "720p" is the standard next
# tier up and the sensible target the task asked for; it is not verified
# against a live render. Kept as a module constant, trivial to change.
ILLUSTRATION_CLIP_QUALITY = "720p"
# The seed image (``schematic.SchematicArtifacts.seed_png``) is rendered at
# ``PillowSchematicRenderer``'s own default 960x720 -- exactly 4:3. Stating
# that ratio explicitly (rather than leaving it to the provider's own
# default) is what lets ``claimscene.camera``'s deterministic push assume
# the delivered clip shares the seed's field of view -- see camera.py's
# module docstring for exactly what this does and does not guarantee.
ILLUSTRATION_CLIP_ASPECT_RATIO = "4:3"
ILLUSTRATION_CLIP_DURATION_S = 5


@dataclass
class ArtifactRef:
    name: str
    key: str
    sha256: str
    size_bytes: int
    content_type: str
    url: str


@dataclass
class CaseResult:
    case_id: str
    scene: SceneGraph
    timeline: Timeline
    manifest: dict
    manifest_hash: str
    provider_name: str
    degraded: bool
    artifacts: dict[str, ArtifactRef] = field(default_factory=dict)
    payloads: dict[str, bytes] = field(default_factory=dict)
    # The detached, independently re-verifiable receipt (also its own stored
    # object under the ``receipt`` kind); ``None`` until the case is sealed.
    receipt: dict | None = None


_KIND_BY_ARTIFACT = {
    "scene_graph": "scene",
    "timeline": "timeline",
    "schematic_svg": "schematic",
    "schematic_hero": "schematic",
    "schematic_animation": "schematic",
    "illustration_still": "illustration",
    "illustration": "illustration",
    "report": "report",
    "manifest": "manifest",
    "receipt": "receipt",
}


class CasePipeline:
    def __init__(
        self,
        extractor: VisionExtractor,
        provider: MediaProvider,
        storage: StorageBackend,
        *,
        renderer: Renderer | None = None,
        layout: LayoutEngine | None = None,
        key_strategy: KeyStrategy = KeyStrategy.HIERARCHICAL,
    ) -> None:
        self.extractor = extractor
        self.provider = provider
        self.storage = storage
        self.renderer = renderer or PillowSchematicRenderer()
        self.layout = layout or LayoutEngine()
        self.key_strategy = key_strategy

    # ── storage helper ───────────────────────────────────────────────────────
    def _store(self, result: CaseResult, name: str, kind: str, filename: str,
               data: bytes, content_type: str) -> ArtifactRef:
        digest = sha256_bytes(data)
        key = make_key(self.key_strategy, case=result.case_id, kind=kind,
                       sha256=digest, name=filename)
        url = self.storage.put(key, data, content_type=content_type)
        ref = ArtifactRef(name=name, key=key, sha256=digest,
                          size_bytes=len(data), content_type=content_type, url=url)
        result.artifacts[name] = ref
        result.payloads[name] = data
        return ref

    # ── main ─────────────────────────────────────────────────────────────────
    def run(self, spec: CaseSpec) -> CaseResult:
        degraded = self.provider.name.startswith("fake")
        result = CaseResult(
            case_id=spec.case_id, scene=None, timeline=None,  # type: ignore[arg-type]
            manifest={}, manifest_hash="", provider_name=self.provider.name,
            degraded=degraded,
        )

        # 1. Persist the input photos (input provenance, with per-input source).
        input_records = []
        for i, photo in enumerate(spec.photos):
            self._store(result, f"input:{i}:{photo.filename}", "inputs",
                        photo.filename, photo.data, photo.media_type)
            input_records.append(input_record(photo))

        # 2. Extract the constrained scene (the only step a VLM may influence,
        #    and it can only speak the closed vocabulary).
        scene = self.extractor.extract(spec.photos, context=spec.context)
        for warning in semantic_warnings(scene):
            scene.confidence_notes.append(warning)
        result.scene = scene
        scene_bytes = scene_to_json(scene).encode("utf-8")
        self._store(result, "scene_graph", "scene", "scene.json",
                    scene_bytes, "application/json")

        # 3. Deterministic layout (the factual layer's geometry).
        timeline = self.layout.build(scene)
        result.timeline = timeline
        timeline_bytes = timeline_to_json(timeline).encode("utf-8")
        self._store(result, "timeline", "timeline", "timeline.json",
                    timeline_bytes, "application/json")

        # 4. Schematic rendering (always produced; watermarked on every frame).
        art = self.renderer.render(timeline, title=f"case {spec.case_id}")
        svg_bytes = art.static_svg.encode("utf-8")
        self._store(result, "schematic_svg", "schematic", "schematic.svg",
                    svg_bytes, "image/svg+xml")
        self._store(result, "schematic_hero", "schematic", "schematic.png",
                    art.hero_png, "image/png")
        if art.animation_mp4 is not None:
            self._store(result, "schematic_animation", "schematic",
                        "schematic.mp4", art.animation_mp4, "video/mp4")

        # 5. Illustration (generative, explicitly sealed as such). The clip
        #    is seeded by the case's OWN deterministic schematic raster --
        #    ``art.seed_png``, the impact-frame render from step 4 with its
        #    text/watermark omitted (see
        #    ``schematic.SchematicArtifacts.seed_png`` /
        #    ``render_frame``'s ``annotate`` flag) -- instead of a
        #    text-to-image generation. A live render on 2026-07-30 (case
        #    ``live-forensic-retry-0bb32eeb``) rendered a stated rear-end
        #    collision as a head-on collision even with an explicit geometry
        #    sentence already in the prompt: prompt-level control alone
        #    cannot pin geometry a diffusion model is free to reinterpret.
        #    Seeding from the schematic makes the layout INHERITED rather
        #    than requested, so it cannot drift. ``prompt`` is still built
        #    from ``scene`` AND ``timeline`` (see ``illustration_prompt``),
        #    restating the same geometry sentence so the text and the seed
        #    image agree. The prompt also asks for no on-image text -- the
        #    disclosure is guaranteed instead by burning it into the actual
        #    pixels here, deterministically, after generation (see
        #    watermark.py). The RAW (unwatermarked) seed bytes are what feed
        #    the video step; only the STORED/SEALED copy
        #    (``illustration_still``) is watermarked, so the clip's own
        #    burned-in caption stays the single on-image text (double text
        #    is exactly why the seed must be unwatermarked: the schematic's
        #    OWN hero frame already carries "ILLUSTRATION -- NOT EVIDENCE"
        #    burned in twice over -- feeding that forward would risk a third,
        #    model-garbled copy on top). Because the seed was never generated
        #    by this provider instance, the Genblaze adapter's chained-output
        #    shortcut (same sha256 -> reuse the hosted URL, see
        #    GenblazeMediaProvider._hosted_by_sha) never applies here any
        #    more -- it always persists through the storage backend instead
        #    (see GenblazeMediaProvider._external_inputs), which needs B2
        #    configured; in practice it always is, since B2 is ClaimScene's
        #    primary storage backend for every other artifact too.
        #
        #    The camera is no longer requested from the model at all (a live
        #    render on 2026-07-31 asked for "gentle camera parallax and a
        #    slow push" and still drifted into an unusable extreme
        #    close-up): the prompt now asks for a locked-off, static shot,
        #    and the push that used to be requested in words is instead
        #    computed deterministically from THIS SAME ``timeline`` and
        #    applied to the model's raw output below, before the disclosure
        #    is burned in -- see ``claimscene.camera``.
        still_raw = art.seed_png
        prompt = illustration_prompt(scene, timeline)
        # ``seed``: derived from the seed raster's own bytes, so the same case
        # asks the provider for the same clip every time. The raster is a pure
        # function of the factual Timeline, which makes this reproducible from
        # the sealed record rather than from a stored random number. Bounded to
        # a positive 31-bit int, the range these APIs accept.
        clip_seed = int(hashlib.sha256(still_raw).hexdigest()[:8], 16) % (2**31 - 1)
        illustration_raw = self.provider.generate(
            model=spec.illustration_model, prompt=prompt, modality="video",
            inputs=[still_raw],
            # ``negative_prompt`` names every camera motion the model might
            # still produce despite the positive prompt's locked-off ask
            # (measured: it has drifted both ways -- pulling out into a wide
            # shot on 2026-07-30, pulling in to a close-up on 2026-07-31);
            # this model exposes no motion or camera parameter, so naming
            # the unwanted behaviour is the strongest lever available. See
            # report.ILLUSTRATION_NEGATIVE_PROMPT. ``aspect_ratio`` matches
            # the seed raster's own 4:3 canvas, so the deterministic camera
            # push below can assume the delivered clip shares the seed's
            # field of view (see claimscene.camera's module docstring).
            params={"duration": ILLUSTRATION_CLIP_DURATION_S,
                    "quality": ILLUSTRATION_CLIP_QUALITY,
                    "aspect_ratio": ILLUSTRATION_CLIP_ASPECT_RATIO,
                    "negative_prompt": ILLUSTRATION_NEGATIVE_PROMPT,
                    "seed": clip_seed},
        )

        # The deterministic push runs on the RAW model output, before the
        # disclosure caption is burned in, so the caption is drawn once, on
        # the final framing, and the push can never crop or distort it.
        # Fail-safe: on any failure this returns the ORIGINAL bytes
        # unchanged with ``applied=False`` -- the watermark burn below still
        # always runs on whatever comes back, so a failed push here can
        # never mean an unwatermarked clip and never fails the render
        # outright. See ``camera.apply_camera_push``.
        # The push is computed in the SEED's pixel space, and the seed is now
        # framed on its own content rather than at the sealed frames' fixed
        # scale, so pass the scale actually used instead of letting the camera
        # assume it. Getting this wrong would not crash: it would silently aim
        # the push at the wrong point.
        camera_result = apply_camera_push(
            illustration_raw, timeline, duration_s=ILLUSTRATION_CLIP_DURATION_S,
            seed_scale=art.seed_scale or CAMERA_SEED_SCALE)

        still_burn = burn_still_watermark(still_raw)
        self._store(result, "illustration_still", "illustration",
                    "illustration.png", still_burn.data, "image/png")
        illustration_burn = burn_clip_watermark(camera_result.data)
        self._store(result, "illustration", "illustration", "illustration.mp4",
                    illustration_burn.data, "video/mp4")

        # 6. Deterministic report.
        provenance_note = (
            f"illustration generated by provider '{self.provider.name}'"
            + (" (offline deterministic fallback)" if degraded else "")
        )
        report_text = build_report(scene, timeline, case_id=spec.case_id,
                                   extra_notes=[provenance_note])
        report_bytes = report_text.encode("utf-8")
        self._store(result, "report", "report", "report.md",
                    report_bytes, "text/markdown")

        # 7. Seal the AI→human approval receipt. The confirmed scene's hash is
        #    the one just sealed as ``scene_graph`` (so the digest self-voids if
        #    the confirmed scene ever drifts). With an AI-proposed baseline the
        #    server computes the proposed→confirmed field diff itself; without
        #    one it seals an honest ``unverified_no_baseline`` block, null hashes.
        confirmed_sha = result.artifacts["scene_graph"].sha256
        if spec.proposed_scene is not None:
            proposed_sha = sha256_bytes(
                scene_to_json(spec.proposed_scene).encode("utf-8"))
            diff = diff_scenes(spec.proposed_scene, scene)
            review = build_review(
                classification=spec.review_classification.value,
                diff=diff,
                counts=review_counts(diff),
                reviewer_id=spec.reviewer_id,
                scene_proposed_sha256=proposed_sha,
                scene_confirmed_sha256=confirmed_sha,
                prior_confidence_notes=list(spec.prior_confidence_notes),
            )
        else:
            review = build_review(
                classification=ReviewClassification.unverified_no_baseline.value,
                diff=[],
                counts=review_counts([]),
                reviewer_id=spec.reviewer_id,
                scene_proposed_sha256=None,
                scene_confirmed_sha256=None,
                prior_confidence_notes=list(spec.prior_confidence_notes),
            )

        # 8. Seal the manifest and persist it.
        manifest = build_manifest(
            case_id=spec.case_id,
            inputs=input_records,
            scene_graph_sha256=result.artifacts["scene_graph"].sha256,
            timeline_sha256=result.artifacts["timeline"].sha256,
            schematic={
                "static_svg_sha256": result.artifacts["schematic_svg"].sha256,
                "hero_png_sha256": result.artifacts["schematic_hero"].sha256,
                "frame_count": art.frame_count,
                "fps": art.fps,
                "animation_sha256": (
                    result.artifacts["schematic_animation"].sha256
                    if "schematic_animation" in result.artifacts else None
                ),
                "watermark": WATERMARK,
            },
            illustration={
                "provider": self.provider.name,
                "model": spec.illustration_model,
                "prompt": prompt,
                "sha256": result.artifacts["illustration"].sha256,
                # The seed is no longer a text-to-image generation -- see the
                # note at step 5 above -- so these three fields describe the
                # deterministic schematic raster's provenance instead of a
                # model call. ``still_model`` is honestly ``None`` (no model
                # produced this); ``still_source`` names exactly which
                # factual artifact it is; ``still_prompt`` keeps its old key
                # (readiness.py and the frontend both still read it) but now
                # holds a provenance note, not a prompt -- see
                # ``report.ILLUSTRATION_SEED_NOTE``.
                "still_model": None,
                "still_source": "schematic:impact_frame",
                "still_prompt": ILLUSTRATION_SEED_NOTE,
                "still_sha256": result.artifacts["illustration_still"].sha256,
                "degraded": degraded,
                # Structural, sealed proof that the disclosure was burned into
                # the pixels (not merely requested in the prompt above) -- see
                # watermark.py and provenance.verify_all's matching checks.
                "watermark_text": DISCLOSURE,
                "watermark_burned": illustration_burn.burned,
                "watermark_error": illustration_burn.error,
                "still_watermark_burned": still_burn.burned,
                "still_watermark_error": still_burn.error,
                # The camera's own provenance -- mirrors ``still_source`` /
                # ``still_prompt`` above: ``camera_move_source`` names exactly
                # which factual artifact the push was computed from,
                # ``camera_move_note`` is the long-form honesty note (see
                # ``report.CAMERA_PUSH_NOTE``), and ``camera_move_applied`` /
                # ``camera_move_error`` are the same honest-degrade pair every
                # other fail-safe step in this pipeline seals (never a silent
                # omission) -- see ``camera.apply_camera_push`` and
                # ``provenance.verify_all``'s matching check.
                "camera_move_source": "timeline:contact_point",
                "camera_move_note": CAMERA_PUSH_NOTE,
                "camera_move_applied": camera_result.applied,
                "camera_move_error": camera_result.error,
                # The authorship split itself, stated once as a single
                # explicit claim instead of left for a reader to infer from
                # the still_source/camera_move_source/watermark_burned
                # fields above: what this codebase computed versus what the
                # generative model actually contributed. See
                # ``provenance.AUTHORSHIP_NOTE`` for the exact sealed text
                # and ``provenance.verify_all``'s matching structural check.
                "authorship_note": AUTHORSHIP_NOTE,
                # Why the live provider was not used, when it was configured
                # and failed (see ``api._run_render`` and
                # ``claimscene.degrade``). The key is OMITTED entirely when
                # there was no live failure, so a normal run — live or a
                # deployment with no provider configured — seals the exact
                # bytes it always did. One closed-vocabulary token, never the
                # upstream error text: the manifest is a public artifact.
                **({"degrade_kind": spec.degrade_kind} if spec.degrade_kind else {}),
            },
            report_sha256=result.artifacts["report"].sha256,
            review=review,
        )
        result.manifest = manifest
        result.manifest_hash = manifest["manifest_hash"]
        manifest_bytes = canonical_json(manifest)
        self._store(result, "manifest", "manifest", "manifest.json",
                    manifest_bytes, "application/json")

        # 9. Detached, independently re-verifiable receipt — persisted as its
        #    OWN small object next to the case artifacts (content-addressed,
        #    indexed). ``verify_all`` re-runs every named check from the bytes
        #    just written (via ``result.payloads``), then the receipt distils
        #    that outcome + the illustration output digest + the review decision
        #    digest into a self-sealed attestation. Offline-safe and never 500s:
        #    the fetcher is a plain dict read and each check is fail-closed.
        verification = verify_all(manifest, lambda name: result.payloads.get(name))
        detached = build_detached_receipt(manifest, verification)
        result.receipt = detached
        self._store(result, "receipt", "receipt", "receipt.json",
                    canonical_json(detached), "application/json")
        return result
