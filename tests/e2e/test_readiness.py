"""The readiness gate itself must stay green on its automatable checks."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "readiness.py"


def _load_readiness():
    if "claimscene_readiness" in sys.modules:
        return sys.modules["claimscene_readiness"]
    spec = importlib.util.spec_from_file_location("claimscene_readiness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: @dataclass resolves annotations via sys.modules.
    sys.modules["claimscene_readiness"] = module
    spec.loader.exec_module(module)
    return module


def test_all_automatable_checks_pass():
    readiness = _load_readiness()
    report = readiness.evaluate()
    failing = [c["id"] for crit in report["criteria"] for c in crit["checks"]
               if c["status"] == "fail"]
    assert failing == [], f"readiness checks failing: {failing}"
    assert report["automatable_pct"] == 100.0


def test_user_gated_items_are_surfaced_not_hidden():
    readiness = _load_readiness()
    report = readiness.evaluate()
    gated_ids = {g["id"] for g in report["user_gated"]}
    # Phase 2 converted the genblaze checks to automatable real evidence;
    # only genuinely human-held items may remain user-gated.
    assert gated_ids == {"production.live_deploy", "b2.live_objects_written"}
    for g in report["user_gated"]:
        assert g["action"].startswith("TODO"), g["id"]


def test_phase2_checks_are_automatable_real_evidence():
    readiness = _load_readiness()
    report = readiness.evaluate()
    checks = {c["id"]: c for crit in report["criteria"] for c in crit["checks"]}
    for check_id in ("utility.vlm_adapter_constrained",
                     "utility.eval_scoreboard_floor",
                     "genblaze.sdk_adapter",
                     "genblaze.live_illustration"):
        assert checks[check_id]["automatable"], check_id
        assert checks[check_id]["status"] == "pass", (
            check_id, checks[check_id]["detail"])


def test_report_schema_and_criteria_cover_the_thesis():
    readiness = _load_readiness()
    report = readiness.evaluate()
    assert report["schema"] == "claimscene/readiness/v1"
    ids = {c["id"] for c in report["criteria"]}
    assert ids == {"utility", "production", "b2", "genblaze", "security",
                   "honest_media"}
