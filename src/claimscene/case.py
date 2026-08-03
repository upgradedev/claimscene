"""Case-level input models: photos with per-input source attribution.

Every input photo carries its provenance *at ingest time* — where it came
from (``source``), and optional ``attribution`` / ``license`` strings — so
the sealed manifest can record honest per-input origins, not just hashes.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .scene import SceneGraph


class PhotoRole(str, Enum):
    scene_photo = "scene_photo"
    damage_photo = "damage_photo"
    road_photo = "road_photo"


class ReviewClassification(str, Enum):
    """Closed honesty taxonomy for a sealed AI→human approval receipt.

    A public demo has no authentication, so a demo click is sealed
    ``interactive_demo`` and can NEVER be labelled ``authenticated_human`` —
    the server downgrades it absent a genuinely authenticated principal.
    ``unverified_no_baseline`` is *server-set only* (never client-selectable):
    it seals honestly when no AI-proposed baseline was supplied to diff
    against, rather than fabricating an approval delta.
    """

    interactive_demo = "interactive_demo"
    authenticated_human = "authenticated_human"
    unverified_no_baseline = "unverified_no_baseline"


#: The subset a client may request; ``unverified_no_baseline`` is server-only.
CLIENT_REVIEW_CLASSIFICATIONS = frozenset(
    {ReviewClassification.interactive_demo, ReviewClassification.authenticated_human}
)


class PhotoSource(str, Enum):
    user_upload = "user_upload"
    staged_demo = "staged_demo"
    public_domain = "public_domain"
    licensed = "licensed"
    synthetic_generated = "synthetic_generated"


class CasePhoto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    data: bytes
    media_type: str = "image/png"
    role: PhotoRole = PhotoRole.scene_photo
    source: PhotoSource
    attribution: str | None = None
    license: str | None = None


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=64)
    photos: list[CasePhoto] = Field(min_length=1)
    context: str | None = None
    # The illustration clip's seed image is the case's own deterministic
    # schematic raster (see pipeline.py step 5), not a generated still, so
    # there is no still-image model to configure here any more. pixverse is
    # the clip's budget default; Kling-Image2Video-V2.1-Master is the
    # premium option (both on GMI Cloud via Genblaze).
    illustration_model: str = "pixverse-v6-i2v"
    # ── sealed AI→human approval receipt (computed by the pipeline) ──────────
    # The AI-proposed scene the human reviewed against. When present, the
    # pipeline seals the server-computed proposed→confirmed field diff; when
    # ``None`` it seals an honest ``unverified_no_baseline`` review block.
    proposed_scene: SceneGraph | None = None
    reviewer_id: str | None = None
    review_classification: ReviewClassification = ReviewClassification.interactive_demo
    # The confirmed scene's own notes *before* the human-in-the-loop reset,
    # carried into the review receipt so the AI's real notes are not lost.
    prior_confidence_notes: list[str] = Field(default_factory=list)
    # Why the live illustration provider could not be used for THIS seal — one
    # of ``claimscene.degrade.KINDS``, set only on the honest-degrade re-run in
    # ``api._run_render`` (the live provider was configured and failed). Left
    # ``None`` on a normal run AND on a deployment that has no live provider
    # configured at all: there, ``illustration.provider`` already says
    # ``fake-media`` and no live attempt was made, so claiming a failure kind
    # would be fiction. Sealed into the manifest's illustration block only when
    # set, which also keeps manifests byte-identical for every existing path.
    degrade_kind: str | None = None
