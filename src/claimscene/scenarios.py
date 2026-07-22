"""Committed synthetic eval scenarios, exposed so judges can run the whole
app WITHOUT supplying their own accident photos.

The scenarios live under ``eval/scenarios`` (the same set the extraction
scoreboard is measured against). Each ships three synthetic toy-diorama views
plus a hand-authored ground-truth :class:`SceneGraph`. This module loads the
manifest, summarises each scenario for the UI, resolves scenario images with
strict path-sanitisation, and materialises a scenario's views as
``CasePhoto`` inputs (sealed honestly as ``synthetic_generated`` with the
manifest's attribution + licence) for the render pipeline.

Directory resolution: ``CLAIMSCENE_SCENARIOS_DIR`` wins; otherwise the repo's
``eval/scenarios`` (works for source checkouts + CI). The container image sets
the env var to the copied ``/app/eval/scenarios``.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from .case import CasePhoto, PhotoRole, PhotoSource
from .keys import safe_component
from .scene import SceneGraph, scene_to_json

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DIR = _REPO_ROOT / "eval" / "scenarios"

_EXT_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp"}


class ScenarioError(Exception):
    """A scenario id / image is unknown or resolves outside the scenarios dir."""


def scenarios_dir() -> Path:
    override = os.environ.get("CLAIMSCENE_SCENARIOS_DIR")
    return Path(override).expanduser() if override else _DEFAULT_DIR


@lru_cache(maxsize=8)
def _load_manifest(dir_str: str) -> dict:
    path = Path(dir_str) / "manifest.json"
    if not path.exists():
        return {"scenarios": []}
    return json.loads(path.read_text("utf-8"))


def load_manifest() -> dict:
    return _load_manifest(str(scenarios_dir()))


def _title(scenario_id: str) -> str:
    """``s01_rear_end`` → ``Rear End`` (drop a leading ``sNN_`` index)."""
    stem = scenario_id
    head, _, tail = scenario_id.partition("_")
    if head[:1] == "s" and head[1:].isdigit() and tail:
        stem = tail
    return stem.replace("_", " ").strip().title() or scenario_id


def _summary(truth: dict) -> str:
    road = truth.get("road", {})
    vehicles = truth.get("vehicles", [])
    kinds = ", ".join(f"{v.get('color', '')} {v.get('kind', '')}".strip()
                      for v in vehicles)
    layout = str(road.get("layout", "")).replace("_", " ")
    return f"{len(vehicles)} vehicles ({kinds}) · {layout}".strip(" ·")


def list_scenarios() -> list[dict]:
    """UI-facing scenario cards: id, title, context, summary + image refs.

    Image refs are the *api-relative* thumbnail routes
    (``/scenarios/{id}/images/{name}``), so the client never needs to know
    where the bytes live.
    """
    manifest = load_manifest()
    cards: list[dict] = []
    for spec in manifest.get("scenarios", []):
        sid = spec["id"]
        images = list(spec.get("images", []))
        cards.append({
            "id": sid,
            "title": _title(sid),
            "context": spec.get("context", ""),
            "summary": _summary(spec.get("truth", {})),
            "images": [f"/scenarios/{sid}/images/{name}" for name in images],
            "thumbnail": (f"/scenarios/{sid}/images/{images[0]}"
                          if images else None),
        })
    return cards


def _find(scenario_id: str) -> dict | None:
    for spec in load_manifest().get("scenarios", []):
        if spec["id"] == scenario_id:
            return spec
    return None


def get_scenario(scenario_id: str) -> dict | None:
    return _find(scenario_id)


def scenario_truth_json(scenario_id: str) -> str:
    """The scenario's ground-truth SceneGraph, validated + re-serialised.

    Round-tripping through :class:`SceneGraph` guarantees the committed truth
    still satisfies the constrained vocabulary (raises otherwise).
    """
    spec = _find(scenario_id)
    if spec is None:
        raise ScenarioError(f"unknown scenario {scenario_id!r}")
    return scene_to_json(SceneGraph.model_validate(spec["truth"]))


def resolve_image_path(scenario_id: str, name: str) -> Path:
    """Resolve one scenario image to a real path, safely.

    Both segments are reduced with :func:`keys.safe_component` (kills
    traversal), the name is whitelisted against the scenario's declared
    images, and the final resolved path is asserted to stay inside the
    scenarios directory — belt-and-suspenders against traversal.
    """
    spec = _find(safe_component(scenario_id))
    if spec is None:
        raise ScenarioError(f"unknown scenario {scenario_id!r}")
    safe_name = safe_component(name)
    if safe_name not in set(spec.get("images", [])):
        raise ScenarioError(f"unknown image {name!r} for scenario {scenario_id!r}")
    base = scenarios_dir().resolve()
    candidate = (base / spec["id"] / safe_name).resolve()
    if base not in candidate.parents:
        raise ScenarioError("resolved image path escapes the scenarios directory")
    if not candidate.is_file():
        raise ScenarioError(f"missing image {safe_name!r} for scenario {spec['id']!r}")
    return candidate


def scenario_photos(scenario_id: str) -> list[CasePhoto]:
    """Materialise a scenario's views as sealed ``CasePhoto`` inputs.

    Sealed honestly: ``source=synthetic_generated`` with the manifest's
    attribution + licence, so the sealed case manifest never claims a
    demo scenario was a real user upload.
    """
    spec = _find(safe_component(scenario_id))
    if spec is None:
        raise ScenarioError(f"unknown scenario {scenario_id!r}")
    manifest = load_manifest()
    attribution = manifest.get("attribution")
    license_ = manifest.get("license")
    roles = [PhotoRole.scene_photo, PhotoRole.road_photo, PhotoRole.damage_photo]
    photos: list[CasePhoto] = []
    for i, name in enumerate(spec.get("images", [])):
        path = resolve_image_path(spec["id"], name)
        media = _EXT_MEDIA.get(path.suffix.lower(), "image/jpeg")
        photos.append(CasePhoto(
            filename=f"{spec['id']}_{name}", data=path.read_bytes(),
            media_type=media, role=roles[i % len(roles)],
            source=PhotoSource.synthetic_generated,
            attribution=attribution, license=license_,
        ))
    return photos
