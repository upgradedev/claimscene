#!/usr/bin/env python3
"""ClaimScene submission READINESS GATE (foundation skeleton).

A machine-checkable gate that scores this repo against six criteria — the
four Backblaze Generative Media Hackathon criteria (**Real-World Utility**,
**Production Readiness**, **B2 Storage & Orchestration**, **Use of
Genblaze**) plus ClaimScene's own two: **Application Security** and
**Honest, Self-Disclosing Media** (the provenance-aware-media thesis).

Design principle (ported from our Cinemory gate): **real evidence, not
file-existence.** Every automatable check drives the actual code path (runs
the pipeline, the CLI, the real B2 adapter against an S3 stub) and asserts
on observable behaviour. A check is one of:

  * ``pass``       — the real path was exercised and behaved correctly
  * ``fail``       — the real path was exercised and misbehaved
  * ``user-gated`` — genuinely needs a human-held credential / live deploy;
                     excluded from the automatable % and listed for the user.

Foundation phase: CI runs this NON-GATING (``--min 0``) but prints and
archives the score; the threshold is hardened in a later phase.

Run:
    python scripts/readiness.py                 # human report + readiness.json
    python scripts/readiness.py --json out.json --min 95
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# A tiny, valid 1x1 PNG so ingest paths see genuine image bytes.
_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc``\x00\x00\x00\x04"
    b"\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)

_B2_ENV = ("B2_BUCKET_NAME", "B2_S3_ENDPOINT", "B2_ENDPOINT_URL",
           "B2_APPLICATION_KEY_ID", "B2_KEY_ID", "B2_APPLICATION_KEY",
           "B2_APP_KEY", "B2_KEY_PREFIX", "B2_PREFIX", "GMI_API_KEY",
           "NEBIUS_INFERENCE_API_KEY")


# ── check + criterion model ──────────────────────────────────────────────────
@dataclass
class CheckResult:
    id: str
    label: str
    status: str  # "pass" | "fail" | "user-gated"
    weight: int
    automatable: bool
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass
class Check:
    id: str
    label: str
    weight: int
    run: Callable[[], tuple[bool, str]] | None = None
    user_gated: bool = False
    gate_detail: str = ""

    def evaluate(self) -> CheckResult:
        if self.user_gated:
            return CheckResult(self.id, self.label, "user-gated", self.weight,
                               automatable=False, detail=self.gate_detail)
        assert self.run is not None
        try:
            ok, detail = self.run()
        except Exception as exc:  # a raising check is a failing check
            return CheckResult(self.id, self.label, "fail", self.weight,
                               automatable=True, detail=f"{type(exc).__name__}: {exc}")
        return CheckResult(self.id, self.label, "pass" if ok else "fail", self.weight,
                           automatable=True, detail=detail)


@dataclass
class Criterion:
    id: str
    label: str
    checks: list[Check] = field(default_factory=list)


# ── shared evidence: one standard offline case run ───────────────────────────
@lru_cache(maxsize=1)
def _standard_run():
    from claimscene.adapters.fakes import (
        FakeMediaProvider,
        FakeVisionExtractor,
        InMemoryStorage,
    )
    from claimscene.case import CasePhoto, CaseSpec, PhotoRole, PhotoSource
    from claimscene.pipeline import CasePipeline
    from claimscene.schematic import PillowSchematicRenderer

    storage = InMemoryStorage(bucket="readiness")
    pipeline = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                            renderer=PillowSchematicRenderer(animate=False))
    photos = [
        CasePhoto(filename="scene.png", data=_PNG_1x1 + b"s",
                  role=PhotoRole.scene_photo, source=PhotoSource.staged_demo),
        CasePhoto(filename="damage.png", data=_PNG_1x1 + b"d",
                  role=PhotoRole.damage_photo, source=PhotoSource.licensed,
                  attribution="StagedShots Ltd", license="Commercial license #42"),
        CasePhoto(filename="road.png", data=_PNG_1x1 + b"r",
                  role=PhotoRole.road_photo, source=PhotoSource.public_domain,
                  attribution="Municipal archive", license="CC0-1.0"),
    ]
    result = pipeline.run(CaseSpec(case_id="readiness", photos=photos))
    return storage, result


# ── evidence runners ─────────────────────────────────────────────────────────
def check_utility_pipeline_e2e() -> tuple[bool, str]:
    """Photos → scene → schematic → illustration → report → sealed manifest."""
    from claimscene.provenance import verify_artifact, verify_manifest

    storage, result = _standard_run()
    if not verify_manifest(result.manifest):
        return False, "sealed manifest failed verification"
    for name in ("scene_graph", "timeline", "report", "illustration",
                 "schematic_svg", "schematic_hero"):
        if not verify_artifact(result.manifest, name,
                               storage.get(result.artifacts[name].key)):
            return False, f"stored {name} does not match its sealed hash"
    return True, "full offline case run: every artifact sealed + verified from storage"


def check_utility_cli_offline_proof() -> tuple[bool, str]:
    """The judge-runnable CLI produces artifacts + VERIFY PASS in a temp dir."""
    from claimscene.cli import main

    with tempfile.TemporaryDirectory(prefix="claimscene-readiness-") as tmp:
        photos = Path(tmp) / "photos"
        photos.mkdir()
        (photos / "scene.png").write_bytes(_PNG_1x1)
        out = Path(tmp) / "out"
        buf = io.StringIO()
        stdout = sys.stdout
        sys.stdout = buf
        try:
            rc = main(["--case", "gate", "--photos", str(photos), "--out", str(out)])
        finally:
            sys.stdout = stdout
        if rc != 0:
            return False, f"CLI exited {rc}"
        if "VERIFY: PASS" not in buf.getvalue():
            return False, "CLI did not print VERIFY: PASS"
        produced = {p.name for p in (out / "gate").iterdir()}
        needed = {"scene.json", "timeline.json", "schematic.svg", "schematic.png",
                  "illustration.mp4", "report.md", "manifest.json", "index.jsonl"}
        missing = needed - produced
        if missing:
            return False, f"CLI missing outputs: {missing}"
    return True, "CLI run → all artifacts on disk + VERIFY: PASS"


def check_utility_constrained_vocabulary() -> tuple[bool, str]:
    """The anti-hallucination gate bites: free-form output cannot pass."""
    from pydantic import ValidationError

    from claimscene.scene import Impact, Road, SceneGraph, Vehicle

    road = Road(layout="x_intersection", lanes_per_direction=1)
    vehicle = Vehicle(id="v", kind="car", color="red")
    attempts = [
        lambda: SceneGraph(road=road, vehicles=[vehicle], gps=[38.0, 23.7]),
        lambda: Vehicle(id="v", kind="hovercraft", color="red"),
        lambda: Impact(vehicle_id="v", clock_position=13),
        lambda: SceneGraph(road=road, vehicles=[vehicle],
                           impacts=[Impact(vehicle_id="ghost", clock_position=6)]),
    ]
    for i, attempt in enumerate(attempts):
        try:
            attempt()
            return False, f"hallucinated payload #{i} was accepted"
        except ValidationError:
            continue
    return True, "extra fields, unknown enums, bad clocks, ghost refs all rejected"


def check_prod_live_no_creds_degrades() -> tuple[bool, str]:
    """CLAIMSCENE_MODE=live with no creds still seals a full case (no crash)."""
    saved = {name: os.environ.pop(name, None) for name in _B2_ENV}
    saved["CLAIMSCENE_MODE"] = os.environ.get("CLAIMSCENE_MODE")
    os.environ["CLAIMSCENE_MODE"] = "live"
    try:
        from claimscene.case import CasePhoto, CaseSpec, PhotoSource
        from claimscene.config import build_extractor, build_provider, build_storage
        from claimscene.pipeline import CasePipeline
        from claimscene.provenance import verify_manifest
        from claimscene.schematic import PillowSchematicRenderer

        pipeline = CasePipeline(build_extractor(), build_provider(), build_storage(),
                                renderer=PillowSchematicRenderer(animate=False))
        result = pipeline.run(CaseSpec(case_id="live-degrade", photos=[
            CasePhoto(filename="p.png", data=_PNG_1x1,
                      source=PhotoSource.staged_demo)]))
        if not verify_manifest(result.manifest):
            return False, "degraded run did not seal a verifiable manifest"
        if result.manifest["illustration"]["degraded"] is not True:
            return False, "degraded flag not honest in live-no-creds mode"
        return True, "live+no-creds → sealed case; degraded flag honestly True"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def check_prod_deterministic_factual_layer() -> tuple[bool, str]:
    """Same scene → identical timeline and identical static schematic."""
    from claimscene.adapters.fakes import _scene_left_cross
    from claimscene.layout import LayoutEngine, timeline_to_json
    from claimscene.schematic import build_static_svg

    t1 = LayoutEngine().build(_scene_left_cross())
    t2 = LayoutEngine().build(_scene_left_cross())
    if timeline_to_json(t1) != timeline_to_json(t2):
        return False, "layout is not deterministic"
    if build_static_svg(t1) != build_static_svg(t2):
        return False, "static schematic is not deterministic"
    return True, "layout + static schematic byte-identical across runs"


def check_b2_content_addressed_keys() -> tuple[bool, str]:
    storage, _result = _standard_run()
    if not storage.index:
        return False, "no assets stored"
    for row in storage.index:
        parts = row["key"].split("/")
        if len(parts) != 5 or not any(len(p) == 64 for p in parts):
            return False, f"non-content-addressed key: {row['key']}"
    return True, f"{len(storage.index)} assets under <case>/<kind>/<shard>/<sha256>/<name>"


def check_b2_durable_index_roundtrip() -> tuple[bool, str]:
    """Real B2 adapter: durable index.jsonl round-trips across instances."""
    saved = {name: os.environ.pop(name, None) for name in _B2_ENV}
    os.environ["B2_BUCKET_NAME"] = "readiness-live"
    os.environ["B2_S3_ENDPOINT"] = "https://s3.eu-central-003.backblazeb2.com"
    os.environ["B2_KEY_PREFIX"] = "cs"
    try:
        from claimscene.adapters.b2_storage import B2Storage

        class _Body:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        class _FakeS3:
            def __init__(self) -> None:
                self.store: dict[tuple[str, str], bytes] = {}

            def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
                self.store[(Bucket, Key)] = Body
                return {}

            def get_object(self, *, Bucket, Key):  # noqa: N803
                if (Bucket, Key) not in self.store:
                    raise KeyError(Key)
                return {"Body": _Body(self.store[(Bucket, Key)])}

        s3 = _FakeS3()
        key = "case-r/manifests/ab/abcd/manifest.json"
        writer = B2Storage(client=s3)
        writer.put(key, b'{"manifest_hash":"cafe"}', content_type="application/json")
        if ("readiness-live", f"cs/{key}") not in s3.store:
            return False, "object not written under the key prefix"
        if ("readiness-live", "cs/index.jsonl") not in s3.store:
            return False, "index.jsonl not persisted durably"
        reader = B2Storage(client=s3)
        row = next((r for r in reader.index if r["key"] == key), None)
        if row is None or reader.get(key) != b'{"manifest_hash":"cafe"}':
            return False, "fresh instance could not resolve the durable catalogue"
        return True, "durable index.jsonl round-trips across instances (real adapter)"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def check_genblaze_illustration_port_sealed() -> tuple[bool, str]:
    """The illustration step is wired through the MediaProvider port and its
    full provenance (provider/model/prompt/degraded) is sealed."""
    _storage, result = _standard_run()
    ill = result.manifest["illustration"]
    for field_name in ("provider", "model", "prompt", "sha256", "degraded"):
        if field_name not in ill:
            return False, f"illustration provenance missing {field_name!r}"
    if ill["degraded"] is not True:
        return False, "offline run must seal degraded=True"
    if "non-photorealistic" not in ill["prompt"]:
        return False, "illustration prompt is not self-disclosing"
    return True, "illustration sealed with provider/model/prompt + honest degraded flag"


def check_security_traversal_safe_keys() -> tuple[bool, str]:
    from claimscene.adapters.fakes import (
        FakeMediaProvider,
        FakeVisionExtractor,
        InMemoryStorage,
    )
    from claimscene.case import CasePhoto, CaseSpec, PhotoSource
    from claimscene.pipeline import CasePipeline
    from claimscene.schematic import PillowSchematicRenderer

    storage = InMemoryStorage(bucket="readiness-sec")
    pipeline = CasePipeline(FakeVisionExtractor(), FakeMediaProvider(), storage,
                            renderer=PillowSchematicRenderer(animate=False))
    pipeline.run(CaseSpec(case_id="../../evil", photos=[
        CasePhoto(filename="../../../etc/passwd", data=_PNG_1x1,
                  source=PhotoSource.staged_demo)]))
    for row in storage.index:
        key = row["key"]
        segments = key.split("/")
        if ".." in segments or any(s.startswith(".") for s in segments):
            return False, f"traversal segment in key: {key}"
        if "\x00" in key or "\\" in key or key.startswith("/"):
            return False, f"unsafe character in key: {key!r}"
        if not any(len(s) == 64 for s in segments):
            return False, f"key lost its SHA-256 anchor: {key}"
    return True, f"{len(storage.index)} keys sanitised + content-addressed under hostile input"


def check_security_no_credential_leakage() -> tuple[bool, str]:
    _storage, result = _standard_run()
    blob = json.dumps(result.manifest).lower()
    report = result.payloads["report"].decode("utf-8").lower()
    for banned in ("b2_application_key", "b2_app_key", "gmi_api_key", "aws_secret",
                   "password", "secret"):
        if banned in blob or banned in report:
            return False, f"credential-shaped material {banned!r} leaked"
    return True, "manifest + report carry no credential material"


def check_honest_watermark_every_layer() -> tuple[bool, str]:
    from claimscene.provenance import DISCLOSURE, WATERMARK

    _storage, result = _standard_run()
    svg = result.payloads["schematic_svg"].decode("utf-8")
    if svg.count(WATERMARK) < 2:
        return False, "static schematic missing the watermark"
    if result.manifest["schematic"].get("watermark") != WATERMARK:
        return False, "watermark not sealed in the manifest"
    if result.manifest.get("disclosure") != DISCLOSURE:
        return False, "manifest disclosure line missing/altered"
    report = result.payloads["report"].decode("utf-8")
    if DISCLOSURE not in report:
        return False, "report missing the disclosure line"
    if result.manifest["schematic"]["frame_count"] < 2:
        return False, "schematic animation frames missing"
    return True, "watermark + disclosure present in schematic, manifest and report"


def check_honest_source_attribution_sealed() -> tuple[bool, str]:
    from claimscene.provenance import verify_manifest

    _storage, result = _standard_run()
    rows = result.manifest["inputs"]
    licensed = next((r for r in rows if r["source"] == "licensed"), None)
    if licensed is None or not licensed["attribution"] or not licensed["license"]:
        return False, "licensed input lost its attribution/license"
    if not all(r.get("source") for r in rows):
        return False, "an input row is missing its source"
    tampered = json.loads(json.dumps(result.manifest))
    tampered["inputs"][0]["source"] = "user_upload"
    if verify_manifest(tampered):
        return False, "re-badging an input source was NOT detected"
    return True, "per-input source/attribution sealed; re-badging breaks the seal"


# ── criteria wiring ──────────────────────────────────────────────────────────
def build_criteria() -> list[Criterion]:
    return [
        Criterion("utility", "Real-World Utility", [
            Check("utility.pipeline_e2e",
                  "Offline case E2E: photos → sealed, verifiable artifact chain",
                  3, run=check_utility_pipeline_e2e),
            Check("utility.cli_offline_proof",
                  "Judge-runnable CLI produces artifacts + VERIFY PASS",
                  2, run=check_utility_cli_offline_proof),
            Check("utility.constrained_vocabulary",
                  "Constrained vocabulary rejects hallucinated/free-form output",
                  2, run=check_utility_constrained_vocabulary),
        ]),
        Criterion("production", "Production Readiness", [
            Check("production.live_no_creds_degrades",
                  "live mode with no creds degrades to fakes and still seals",
                  3, run=check_prod_live_no_creds_degrades),
            Check("production.deterministic_factual_layer",
                  "Factual layer (layout + schematic) is byte-deterministic",
                  2, run=check_prod_deterministic_factual_layer),
            Check("production.live_deploy",
                  "Demo box deployed in live mode with entitled creds",
                  2, user_gated=True,
                  gate_detail="TODO (next phase): deploy the demo service in live "
                  "mode and verify a health surface reports the real backends."),
        ]),
        Criterion("b2", "B2 Storage & Orchestration", [
            Check("b2.content_addressed_keys",
                  "Every asset stored under a content-addressed SHA-256 key",
                  2, run=check_b2_content_addressed_keys),
            Check("b2.durable_index_roundtrip",
                  "Durable index.jsonl round-trips across instances (real adapter)",
                  2, run=check_b2_durable_index_roundtrip),
            Check("b2.live_objects_written",
                  "Real case objects written to the live B2 bucket",
                  2, user_gated=True,
                  gate_detail="TODO (next phase): create the claimscene B2 bucket, "
                  "run one live case with a write-entitled application key, and "
                  "confirm the objects + index.jsonl in the bucket."),
        ]),
        Criterion("genblaze", "Use of Genblaze", [
            Check("genblaze.illustration_port_sealed",
                  "Illustration wired through the provider port; provenance sealed",
                  2, run=check_genblaze_illustration_port_sealed),
            Check("genblaze.sdk_adapter",
                  "Real Genblaze SDK adapter drives the illustration step",
                  2, user_gated=True,
                  gate_detail="TODO (next phase): port the GenblazeMediaProvider "
                  "adapter (genblaze_core Pipeline + per-asset SHA-256 manifest) "
                  "from the shared Cinemory foundation and contract-test it."),
            Check("genblaze.live_illustration",
                  "A real illustration clip generated live (degraded=false)",
                  2, user_gated=True,
                  gate_detail="TODO (user): top up the GMI Cloud balance, then run "
                  "one live case and confirm illustration.degraded=false."),
        ]),
        Criterion("security", "Application Security", [
            Check("security.traversal_safe_keys",
                  "Hostile case id/filename cannot inject into storage keys",
                  2, run=check_security_traversal_safe_keys),
            Check("security.no_credential_leakage",
                  "No credential material leaks into manifest or report",
                  1, run=check_security_no_credential_leakage),
        ]),
        Criterion("honest_media", "Honest, Self-Disclosing Media", [
            Check("honest_media.watermark_every_layer",
                  "Watermark + disclosure on schematic, manifest and report",
                  2, run=check_honest_watermark_every_layer),
            Check("honest_media.source_attribution_sealed",
                  "Per-input source/attribution sealed; re-badging detected",
                  2, run=check_honest_source_attribution_sealed),
        ]),
    ]


# ── scoring ──────────────────────────────────────────────────────────────────
def evaluate() -> dict:
    criteria = build_criteria()
    crit_reports: list[dict] = []
    user_gated: list[dict] = []
    auto_frac_sum = 0.0
    full_frac_sum = 0.0

    for crit in criteria:
        results = [c.evaluate() for c in crit.checks]
        auto = [r for r in results if r.automatable]
        auto_total = sum(r.weight for r in auto) or 1
        auto_pass = sum(r.weight for r in auto if r.passed)
        full_total = sum(r.weight for r in results) or 1
        auto_frac_sum += auto_pass / auto_total
        full_frac_sum += auto_pass / full_total

        for r in results:
            if r.status == "user-gated":
                user_gated.append({"id": r.id, "criterion": crit.id,
                                   "label": r.label, "action": r.detail})
        crit_reports.append({
            "id": crit.id,
            "label": crit.label,
            "automatable_pct": round(100.0 * auto_pass / auto_total, 1),
            "full_pct_user_gated_pending": round(100.0 * auto_pass / full_total, 1),
            "checks": [{"id": r.id, "label": r.label, "status": r.status,
                        "weight": r.weight, "automatable": r.automatable,
                        "detail": r.detail} for r in results],
        })

    n = len(criteria)
    return {
        "schema": "claimscene/readiness/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "challenge": "Backblaze Generative Media Hackathon",
        "automatable_pct": round(100.0 * auto_frac_sum / n, 1),
        "full_pct_user_gated_pending": round(100.0 * full_frac_sum / n, 1),
        "criteria": crit_reports,
        "user_gated": user_gated,
    }


# ── rendering + CLI ──────────────────────────────────────────────────────────
_ICON = {"pass": "PASS", "fail": "FAIL", "user-gated": "GATE"}


def render(report: dict, threshold: float) -> str:
    lines: list[str] = []
    lines.append("=" * 74)
    lines.append(" CLAIMSCENE READINESS GATE — " + report["challenge"])
    lines.append("=" * 74)
    for crit in report["criteria"]:
        lines.append("")
        lines.append(f"[{crit['label']}]  automatable {crit['automatable_pct']}%  "
                     f"(full {crit['full_pct_user_gated_pending']}%)")
        for c in crit["checks"]:
            lines.append(f"  {_ICON[c['status']]}  {c['id']}")
            lines.append(f"        {c['detail']}")
    lines.append("")
    lines.append("-" * 74)
    lines.append(f" Automatable completeness : {report['automatable_pct']}%   "
                 f"(gate threshold {threshold}%)")
    lines.append(f" Full (user-gated pending): {report['full_pct_user_gated_pending']}%")
    if report["user_gated"]:
        lines.append("")
        lines.append(" USER-GATED (needs a human-held credential / live deploy):")
        for g in report["user_gated"]:
            lines.append(f"   * [{g['criterion']}] {g['label']}")
            lines.append(f"       {g['action']}")
    lines.append("-" * 74)
    passed = report["automatable_pct"] >= threshold
    lines.append(f" GATE: {'PASS' if passed else 'FAIL'} "
                 f"(automatable {report['automatable_pct']}% "
                 f"{'>=' if passed else '<'} {threshold}%)")
    lines.append("=" * 74)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClaimScene submission readiness gate")
    parser.add_argument("--json", default=str(_REPO_ROOT / "readiness.json"),
                        help="path to write readiness.json (default: repo root)")
    parser.add_argument("--min", type=float, default=95.0,
                        help="minimum automatable completeness %% to pass (default: 95)")
    parser.add_argument("--quiet", action="store_true",
                        help="only print the final gate line")
    args = parser.parse_args(argv)

    try:  # keep the human report readable regardless of the console code page
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

    report = evaluate()
    Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    rendered = render(report, args.min)
    if args.quiet:
        print(rendered.splitlines()[-2])
    else:
        print(rendered)

    return 0 if report["automatable_pct"] >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
