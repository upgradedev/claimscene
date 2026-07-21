"""Sealed, self-disclosing provenance — ClaimScene's product thesis in code.

Every artifact is content-addressed by SHA-256. A case manifest records the
hash of every input photo *with its source attribution and license*, the
constrained scene graph, the deterministic timeline + schematic (the factual
layer), the generative illustration (provider/model/prompt, explicitly
flagged), and the report. The manifest is sealed with a canonical SHA-256
(``manifest_hash``) computed over its sorted-key JSON, so tampering with any
recorded field — including an input's claimed source — is detectable via
:func:`verify_manifest`.

Shared foundation with our other entry, Cinemory (MIT): canonical-JSON
sealing and hash chaining follow the same approach.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .case import CasePhoto
from .layout import TIMELINE_SCHEMA
from .scene import SCENE_SCHEMA

MANIFEST_SCHEMA = "claimscene/manifest/v1"
DISCLOSURE = "AI-generated illustration — not evidence"
WATERMARK = "ILLUSTRATION — NOT EVIDENCE"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def input_record(photo: CasePhoto) -> dict:
    """Provenance row for one input photo: hash + role + source attribution."""
    return {
        "filename": photo.filename,
        "sha256": sha256_bytes(photo.data),
        "media_type": photo.media_type,
        "role": photo.role.value,
        "source": photo.source.value,
        "attribution": photo.attribution,
        "license": photo.license,
    }


def build_manifest(
    *,
    case_id: str,
    inputs: list[dict],
    scene_graph_sha256: str,
    timeline_sha256: str,
    schematic: dict,
    illustration: dict,
    report_sha256: str,
    created_at: str | None = None,
) -> dict:
    """Assemble the case manifest and seal it with a canonical hash."""
    body = {
        "schema": MANIFEST_SCHEMA,
        "case_id": case_id,
        "created_at": created_at or utc_now_iso(),
        "disclosure": DISCLOSURE,
        "inputs": inputs,
        "scene_graph": {"schema": SCENE_SCHEMA, "sha256": scene_graph_sha256},
        "timeline": {"schema": TIMELINE_SCHEMA, "sha256": timeline_sha256},
        "schematic": schematic,
        "illustration": illustration,
        "report": {"media_type": "text/markdown", "sha256": report_sha256},
    }
    body["manifest_hash"] = sha256_bytes(canonical_json(body))
    return body


def verify_manifest(manifest: dict) -> bool:
    """Recompute the canonical hash and confirm the manifest is intact."""
    claimed = manifest.get("manifest_hash")
    if not claimed:
        return False
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    return sha256_bytes(canonical_json(body)) == claimed


# Dotted paths into the manifest for per-artifact hash checks.
_ARTIFACT_PATHS = {
    "scene_graph": ("scene_graph", "sha256"),
    "timeline": ("timeline", "sha256"),
    "report": ("report", "sha256"),
    "illustration": ("illustration", "sha256"),
    "schematic_svg": ("schematic", "static_svg_sha256"),
    "schematic_hero": ("schematic", "hero_png_sha256"),
    "schematic_animation": ("schematic", "animation_sha256"),
}


def recorded_sha256(manifest: dict, artifact: str) -> str | None:
    section, field = _ARTIFACT_PATHS[artifact]
    value = manifest.get(section, {}).get(field)
    return value if isinstance(value, str) else None


def verify_artifact(manifest: dict, artifact: str, data: bytes) -> bool:
    """Confirm ``data`` matches the hash the manifest recorded for ``artifact``."""
    recorded = recorded_sha256(manifest, artifact)
    return recorded is not None and recorded == sha256_bytes(data)
