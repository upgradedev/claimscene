#!/usr/bin/env python3
"""Regenerate ``eval/golden-digests.json`` — the external reproducibility anchor.

Pins the SHA-256 of every committed eval input asset (the synthetic scenario
photos + the truth-bearing ``manifest.json``) so CI can prove they have not
drifted. The single enumeration + hashing rule lives in
:func:`claimscene.scenarios.eval_input_digests`, shared with the CI test and the
readiness gate, so the pin set and the checks can never diverge.

Run after any *intentional* change to ``eval/scenarios/``::

    python scripts/gen_golden_digests.py

Until the committed file matches the live-computed digests again, the CI test
(``tests/integration/test_eval_golden_digests.py``) and the readiness gate
(``reproducibility.eval_digests_anchored``) fail by design.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from claimscene.scenarios import eval_input_digests  # noqa: E402

ANCHOR_SCHEMA = "claimscene/golden-digests/v1"
_OUT = _REPO_ROOT / "eval" / "golden-digests.json"
_SCENARIOS = _REPO_ROOT / "eval" / "scenarios"


def build_anchor() -> dict:
    return {
        "schema": ANCHOR_SCHEMA,
        "algorithm": "sha256",
        "description": (
            "External reproducibility anchor: SHA-256 of every committed "
            "eval/scenarios input asset (synthetic photos + the truth-bearing "
            "manifest.json). CI asserts the live-computed digests still match, "
            "proving the eval inputs and the hashing stay reproducible outside "
            "the sealed case artifacts. Regenerate with scripts/gen_golden_digests.py."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assets": eval_input_digests(_SCENARIOS),
    }


def main() -> int:
    anchor = build_anchor()
    _OUT.write_text(json.dumps(anchor, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)} ({len(anchor['assets'])} assets pinned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
