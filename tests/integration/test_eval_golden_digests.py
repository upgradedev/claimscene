"""Reproducibility anchor: the committed eval inputs must not drift.

ClaimScene's provenance ``verify`` is self-consistency — a forger who recomputes
a hash passes. This test anchors the eval inputs *outside* the sealed artifacts:
the committed ``eval/golden-digests.json`` pins the SHA-256 of every synthetic
scenario photo plus the truth-bearing ``manifest.json``, and CI asserts the
live-computed digests (via the product's own hashing) still match — an external
anchor proving the eval inputs and the hashing stay reproducible. Fully offline,
no credentials; runs in the standard ``python`` CI job.
"""
from __future__ import annotations

import json
from pathlib import Path

from claimscene.scenarios import eval_input_digests, scenarios_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANCHOR = _REPO_ROOT / "eval" / "golden-digests.json"


def _anchor() -> dict:
    return json.loads(_ANCHOR.read_text("utf-8"))


def test_anchor_file_is_well_formed():
    anchor = _anchor()
    assert anchor["schema"] == "claimscene/golden-digests/v1"
    assert anchor["algorithm"] == "sha256"
    assert isinstance(anchor["assets"], dict) and anchor["assets"]
    assert all(len(v) == 64 for v in anchor["assets"].values())


def test_live_digests_match_the_committed_anchor():
    """The heart of the anchor: every committed eval input still hashes to its
    pinned digest, and no asset was added to or removed from disk."""
    live = eval_input_digests()
    assert live == _anchor()["assets"], (
        "eval inputs drifted from eval/golden-digests.json — if the change to "
        "eval/scenarios/ was intentional, regenerate with "
        "`python scripts/gen_golden_digests.py`")


def test_anchor_covers_every_manifest_declared_image():
    """Independent completeness cross-check (derived from the eval manifest's own
    declarations, not from :func:`eval_input_digests`): every image the manifest
    declares is pinned, nothing extra is, and the truth manifest itself is too."""
    anchor = _anchor()
    pinned_images = set(anchor["assets"]) - {"manifest.json"}
    manifest = json.loads((scenarios_dir() / "manifest.json").read_text("utf-8"))
    declared = {f"{spec['id']}/{name}"
                for spec in manifest["scenarios"] for name in spec["images"]}
    assert pinned_images == declared
    assert "manifest.json" in anchor["assets"]
