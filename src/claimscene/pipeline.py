"""Case orchestration: photos → scene → schematic → illustration → report,
sealed end-to-end with content-addressed provenance.

The orchestrator depends only on ports (``VisionExtractor``,
``MediaProvider``, ``StorageBackend``, ``Renderer``), so the exact same code
path runs against the real adapters or the offline fakes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .case import CaseSpec, ReviewClassification
from .evaluation import diff_scenes, review_counts
from .keys import KeyStrategy, make_key
from .layout import LayoutEngine, Timeline, timeline_to_json
from .ports import MediaProvider, Renderer, StorageBackend, VisionExtractor
from .provenance import (
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
from .report import build_report, illustration_prompt, illustration_still_prompt
from .scene import SceneGraph, scene_to_json, semantic_warnings
from .schematic import PillowSchematicRenderer
from .watermark import burn_clip_watermark, burn_still_watermark


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

        # 5. Illustration (generative, explicitly sealed as such), two steps:
        #    an establish-shot still, then an image-to-video clip chained from
        #    that still (the provider reuses the still's hosted URL live).
        #    Both prompts are built from ``scene`` AND ``timeline`` -- the
        #    Timeline's already-computed poses at the impact frame give the
        #    prompt the real relative position/orientation between impact
        #    participants, so the illustration matches the schematic instead
        #    of the model inventing its own layout. The prompts also now ask
        #    for no on-image text -- the disclosure is guaranteed instead by
        #    burning it into the actual pixels here, deterministically, after
        #    generation (see watermark.py; a live render proved a prompt
        #    request alone is not a guarantee, the model can simply ignore
        #    it). The RAW (unwatermarked) still bytes are what feed the video
        #    step, so the Genblaze provider's chained-output optimisation
        #    (same sha256 -> same hosted URL, see
        #    GenblazeMediaProvider._hosted_by_sha) still matches live; only
        #    the STORED/SEALED copies are watermarked.
        still_prompt = illustration_still_prompt(scene, timeline)
        still_raw = self.provider.generate(
            model=spec.illustration_still_model, prompt=still_prompt,
            modality="image",
            params={"size": "2K", "output_format": "png", "max_images": 1,
                    "watermark": False},
        )
        prompt = illustration_prompt(scene, timeline)
        illustration_raw = self.provider.generate(
            model=spec.illustration_model, prompt=prompt, modality="video",
            inputs=[still_raw],
            params={"duration": 5, "quality": "360p"},
        )

        still_burn = burn_still_watermark(still_raw)
        self._store(result, "illustration_still", "illustration",
                    "illustration.png", still_burn.data, "image/png")
        illustration_burn = burn_clip_watermark(illustration_raw)
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
                "still_model": spec.illustration_still_model,
                "still_prompt": still_prompt,
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
