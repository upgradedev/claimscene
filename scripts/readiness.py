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

Phase 2: the live adapters landed (VLM ladder, Genblaze provider, measured
eval scoreboard, committed live-illustration evidence), so CI now runs this
GATING at ``--min 95``. Remaining user-gated items (live deploy, entitled B2
bucket) are listed for the user and excluded from the automatable score.

Phase 3: the web app landed (FastAPI review-adjust API + blueprint React
client). The **Web Application** criterion drives the real API in-process via
FastAPI's TestClient — the extract→preview→render→get→playback chain, the
review-adjust live-update, and the in-browser-verify golden fixture — as real
evidence. The frontend's own typecheck + Vitest + production build are gated
separately by the ``frontend`` CI job (real node evidence, not re-run here).

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
           "GMI_SERVING_BASE_URL", "NEBIUS_INFERENCE_API_KEY",
           "NEBIUS_INFERENCE_BASE_URL")

_EVAL_SCENARIOS_DIR = _REPO_ROOT / "eval" / "scenarios"
_EVAL_RESULTS_DIR = _REPO_ROOT / "eval" / "results"
_EVIDENCE_DIR = _REPO_ROOT / "eval" / "evidence" / "live_illustration"
#: Minimum committed extraction accuracy the gate accepts (measured, honest).
_EVAL_FLOOR_PCT = 60.0
_MAX_EVAL_IMAGE_BYTES = 300 * 1024


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


def check_utility_vlm_adapter_constrained() -> tuple[bool, str]:
    """The live VLM adapter enforces the vocabulary: an invalid reply gets
    exactly one repair round-trip and the result validates as a SceneGraph.
    Driven through the real adapter with a canned transport (no network)."""
    import json as _json
    from types import SimpleNamespace

    from claimscene.adapters.vlm_extractor import VlmExtractor, VlmRung
    from claimscene.case import CasePhoto, PhotoSource
    from claimscene.scene import SceneGraph

    valid = {
        "schema": "claimscene/scene/v1",
        "road": {"layout": "straight", "lanes_per_direction": 1,
                 "signal": "none"},
        "vehicles": [{"id": "veh_a", "kind": "car", "color": "red",
                      "damage": [{"clock_position": 12, "severity": "dent"}]}],
        "movements": [{"vehicle_id": "veh_a", "approach": "N",
                       "maneuver": "straight", "speed_band": "low"}],
        "impacts": [{"vehicle_id": "veh_a", "clock_position": 12}],
        "sequence": ["impact"],
    }
    invalid = dict(valid, gps=[38.0, 23.7])  # hallucinated field
    replies = [_json.dumps(invalid), _json.dumps(valid)]
    calls: list[dict] = []

    class _Client:
        class chat:  # noqa: N801 - mirrors the OpenAI client shape
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=replies.pop(0)))])

    rung = VlmRung(name="stub", base_url="https://stub.test", api_key="k",
                   model="stub-model")
    extractor = VlmExtractor([rung], client_factory=lambda b, k: _Client())
    scene = extractor.extract([CasePhoto(filename="p.png", data=_PNG_1x1,
                                         source=PhotoSource.staged_demo)])
    if not isinstance(scene, SceneGraph):
        return False, "adapter did not return a SceneGraph"
    if len(calls) != 2:
        return False, f"expected 1 repair round-trip, saw {len(calls)} calls"
    if "gps" not in str(calls[1]["messages"][-1]):
        return False, "repair feedback did not carry the validator errors"
    if not any("vlm" in n.lower() for n in scene.confidence_notes):
        return False, "extraction provenance note missing"
    return True, ("hallucinated field rejected → one repair round-trip → "
                  "validated SceneGraph (real adapter, canned transport)")


def check_utility_eval_scoreboard_floor() -> tuple[bool, str]:
    """The committed eval set + scoreboard are real, consistent and above the
    accuracy floor: >=6 scenarios, images present and lean, truths validate,
    measured overall accuracy >= floor, prompt version recorded."""
    from claimscene.scene import SceneGraph

    manifest_path = _EVAL_SCENARIOS_DIR / "manifest.json"
    if not manifest_path.exists():
        return False, "eval/scenarios/manifest.json missing"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    scenarios = manifest.get("scenarios", [])
    if len(scenarios) < 6:
        return False, f"only {len(scenarios)} scenarios (need >=6)"
    if manifest.get("image_source") != "synthetic_generated":
        return False, "eval images must be sealed as synthetic_generated"
    for spec in scenarios:
        SceneGraph.model_validate(spec["truth"])  # raises on drift
        for name in spec["images"]:
            path = _EVAL_SCENARIOS_DIR / spec["id"] / name
            if not path.exists():
                return False, f"missing eval image {spec['id']}/{name}"
            if path.stat().st_size > _MAX_EVAL_IMAGE_BYTES:
                return False, f"eval image too large: {spec['id']}/{name}"

    boards = sorted(_EVAL_RESULTS_DIR.glob("*_extraction_eval*.json"))
    if not boards:
        return False, "no committed scoreboard in eval/results/"
    board = json.loads(boards[-1].read_text("utf-8"))
    if board.get("schema") != "claimscene/extraction-eval/v1":
        return False, "scoreboard schema mismatch"
    if len(board.get("prompt_sha256", "")) != 64:
        return False, "scoreboard does not record the prompt version"
    headline = board.get("headline", {}).get("overall_pct")
    if not isinstance(headline, int | float) or headline < _EVAL_FLOOR_PCT:
        return False, f"headline accuracy {headline} below floor {_EVAL_FLOOR_PCT}"
    scored_ids = {s["id"] for rep in board["models"].values()
                  for s in rep["scenarios"]}
    manifest_ids = {s["id"] for s in scenarios}
    if scored_ids != manifest_ids:
        return False, "scoreboard scenarios do not match the committed set"
    return True, (f"{len(scenarios)} scenarios sealed + scored: headline "
                  f"{headline}% (floor {_EVAL_FLOOR_PCT}%), "
                  f"{boards[-1].name}")


def check_reproducibility_eval_digests_anchored() -> tuple[bool, str]:
    """The committed eval inputs are externally anchored + reproducible.

    ``eval/golden-digests.json`` pins the SHA-256 of every synthetic scenario
    photo and the truth-bearing ``manifest.json``; the live-computed digests
    (the product's own hashing, via :func:`claimscene.scenarios.eval_input_digests`)
    must still match. Verify today is self-consistency (recompute the seal and it
    passes); this anchors the inputs *outside* the sealed artifacts, so a silent
    drift or a swapped eval photo is caught in CI."""
    from claimscene.scenarios import eval_input_digests

    anchor_path = _REPO_ROOT / "eval" / "golden-digests.json"
    if not anchor_path.exists():
        return False, "eval/golden-digests.json missing"
    anchor = json.loads(anchor_path.read_text("utf-8"))
    if anchor.get("schema") != "claimscene/golden-digests/v1":
        return False, "golden-digests schema mismatch"
    if anchor.get("algorithm") != "sha256":
        return False, "golden-digests algorithm is not sha256"
    pinned = anchor.get("assets") or {}
    live = eval_input_digests(_EVAL_SCENARIOS_DIR)
    if live != pinned:
        drift = sorted(set(live) ^ set(pinned)) or sorted(
            k for k in live if live[k] != pinned.get(k))
        return False, (f"eval inputs drifted from the anchor: {drift[:5]} "
                       "(regen: scripts/gen_golden_digests.py)")
    return True, (f"{len(pinned)} eval input assets externally anchored; live "
                  "digests match (external reproducibility anchor)")


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


def check_genblaze_sdk_contract() -> tuple[bool, str]:
    """The real Genblaze adapter drives a REAL genblaze_core Pipeline (the
    SDK's own mock provider) and the input-attachment path holds: the step
    the provider receives carries the input Asset, hosted content-addressed
    through the same backend, and the sealed run manifest verifies."""
    import hashlib as _hashlib

    try:
        from genblaze_core.models.asset import Asset as GbAsset
        from genblaze_core.storage.base import StorageBackend
        from genblaze_core.testing import MockProvider
    except ImportError:
        return False, ("genblaze-core not installed (it is in "
                       "requirements-dev.txt; CI installs it)")

    from claimscene.adapters.genblaze_provider import GenblazeMediaProvider

    class MemBackend(StorageBackend):
        _PREFIX = "memory://bucket/"

        def __init__(self) -> None:
            self.store: dict[str, bytes] = {}

        def put(self, key, data, *, content_type=None, metadata=None,
                extra_args=None):
            self.store[key] = (bytes(data)
                               if isinstance(data, bytes | bytearray)
                               else data.read())
            return key

        def get(self, key):
            return self.store[key]

        def exists(self, key):
            return key in self.store

        def delete(self, key):
            self.store.pop(key, None)

        def get_url(self, key, *, expires_in=3600):
            return self.get_durable_url(key)

        def get_durable_url(self, key):
            return f"{self._PREFIX}{key}"

        def key_from_url(self, url):
            return url[len(self._PREFIX):] if url.startswith(self._PREFIX) else None

    clip = b"READINESS-CLIP" + b"\x05\x06" * 300
    backend = MemBackend()
    backend.put("assets/clip.mp4", clip)
    provider = MockProvider(name="mock-media", assets=[GbAsset(
        url=backend.get_durable_url("assets/clip.mp4"), media_type="video/mp4",
        sha256=_hashlib.sha256(clip).hexdigest(), size_bytes=len(clip))])
    adapter = GenblazeMediaProvider(
        video_provider_obj=provider, backend=backend,
        downloader=lambda _u: (_ for _ in ()).throw(
            AssertionError("must read back through the backend")))

    photo = _PNG_1x1 + b"readiness-input"
    out = adapter.generate(model="pixverse-v6-i2v", prompt="orbit",
                           inputs=[photo])
    if out != clip:
        return False, "bytes did not round-trip through the real Pipeline"
    step = provider.received_steps[-1]
    if len(step.inputs) != 1:
        return False, "input Asset was dropped before reaching the provider"
    if step.inputs[0].sha256 != _hashlib.sha256(photo).hexdigest():
        return False, "input Asset lost its content hash"
    if backend.key_from_url(step.inputs[0].url) is None:
        return False, "input was not hosted through the storage backend"
    if adapter.last_manifest is None or not adapter.last_manifest.verify_hash():
        return False, "Genblaze run manifest missing or unverifiable"
    return True, ("real Pipeline + ObjectStorageSink round-trip; input Asset "
                  "attached, content-addressed + sha-sealed; run manifest "
                  "verifies")


def check_genblaze_live_illustration_evidence() -> tuple[bool, str]:
    """A real illustration (still + clip) was generated live and committed
    with its sealed case manifest; the committed bytes must re-hash to the
    sealed values, degraded must be False, and the scene must carry the live
    VLM provenance note (not the offline-fixture note)."""
    from claimscene.provenance import verify_artifact, verify_manifest
    from claimscene.scene import scene_from_json

    manifest_path = _EVIDENCE_DIR / "manifest.json"
    if not manifest_path.exists():
        return False, "eval/evidence/live_illustration/manifest.json missing"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if not verify_manifest(manifest):
        return False, "evidence manifest seal is broken"
    ill = manifest.get("illustration", {})
    if ill.get("degraded") is not False:
        return False, "evidence illustration is not degraded=False"
    if ill.get("provider") != "genblaze":
        return False, f"evidence provider is {ill.get('provider')!r}"
    for artifact, filename in (("illustration", "illustration.mp4"),
                               ("illustration_still", "illustration.png"),
                               ("scene_graph", "scene.json")):
        path = _EVIDENCE_DIR / filename
        if not path.exists():
            return False, f"evidence file {filename} missing"
        if not verify_artifact(manifest, artifact, path.read_bytes()):
            return False, f"{filename} does not re-hash to its sealed value"
    scene = scene_from_json((_EVIDENCE_DIR / "scene.json").read_bytes())
    notes = " ".join(scene.confidence_notes).lower()
    if "no vlm was called" in notes:
        return False, "evidence scene came from the offline fixture extractor"
    if "live vlm" not in notes:
        return False, "evidence scene lacks the live-VLM provenance note"
    return True, ("committed still+clip re-hash to the sealed manifest; "
                  f"degraded=False via provider 'genblaze', models "
                  f"{ill.get('still_model')} + {ill.get('model')}; scene "
                  "extracted by the live VLM ladder")


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


def check_security_upload_guard_rejects_dangerous() -> tuple[bool, str]:
    """The upload guard bites at the API boundary: a disguised executable, an
    over-count upload, and an oversized file are each a clean 4xx (never 5xx),
    while a normal small photo upload is still accepted (the guard is not
    over-broad). Driven through the real ``/cases/extract`` route in-process."""
    from fastapi.testclient import TestClient

    import claimscene.api as capi
    from claimscene.ingest import MAX_PHOTO_BYTES, MAX_PHOTOS

    client = TestClient(capi.app)

    exe = client.post("/cases/extract",
                      files=[("files", ("evil.png", b"MZ\x90\x00 evil", "image/png"))])
    if not 400 <= exe.status_code < 500:
        return False, f"disguised executable not rejected 4xx (got {exe.status_code})"

    many = [("files", (f"p{i}.png", _PNG_1x1, "image/png"))
            for i in range(MAX_PHOTOS + 1)]
    over = client.post("/cases/extract", files=many)
    if not 400 <= over.status_code < 500:
        return False, f"over-count upload not rejected 4xx (got {over.status_code})"

    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_PHOTO_BYTES + 1)
    huge = client.post("/cases/extract",
                       files=[("files", ("big.png", big, "image/png"))])
    if not 400 <= huge.status_code < 500:
        return False, f"oversized upload not rejected 4xx (got {huge.status_code})"

    ok = client.post("/cases/extract",
                     files=[("files", ("p.png", _PNG_1x1, "image/png"))])
    if ok.status_code != 200:
        return False, f"a valid small upload was rejected (got {ok.status_code})"

    return True, (f"disguised-exe/over-count(>{MAX_PHOTOS})/oversized"
                  f"(>{MAX_PHOTO_BYTES // (1024 * 1024)}MB) all 4xx; valid "
                  "upload 200")


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


def check_honest_sealed_approval_receipt() -> tuple[bool, str]:
    """The AI→human approval receipt is real evidence: the server-computed
    proposed→confirmed diff seals into the manifest, its decision digest
    recomputes, the aggregate verify_all receipt passes from stored bytes, and
    drifting the confirmed scene self-voids the approval (self-invalidating)."""
    from claimscene.adapters.fakes import FakeMediaProvider, InMemoryStorage
    from claimscene.case import (
        CasePhoto,
        CaseSpec,
        PhotoSource,
        ReviewClassification,
    )
    from claimscene.pipeline import CasePipeline
    from claimscene.provenance import decision_digest, verify_all
    from claimscene.scene import Movement, Road, SceneGraph, Vehicle, VehicleColor
    from claimscene.schematic import PillowSchematicRenderer

    class _Fixed:
        name = "fixed"

        def __init__(self, scene):
            self._scene = scene

        def extract(self, photos, *, context=None):
            return self._scene.model_copy(deep=True)

    proposed = SceneGraph(
        road=Road(layout="x_intersection", lanes_per_direction=1, signal="traffic_light"),
        vehicles=[Vehicle(id="veh_a", kind="car", color="silver"),
                  Vehicle(id="veh_b", kind="van", color="green")],
        movements=[Movement(vehicle_id="veh_a", approach="N", maneuver="left_turn",
                            speed_band="low")])
    confirmed = proposed.model_copy(deep=True)
    confirmed.vehicles[0].color = VehicleColor.red  # a human correction

    storage = InMemoryStorage(bucket="readiness-review")
    result = CasePipeline(_Fixed(confirmed), FakeMediaProvider(), storage,
                          renderer=PillowSchematicRenderer(animate=False)).run(
        CaseSpec(case_id="review",
                 photos=[CasePhoto(filename="p.png", data=_PNG_1x1,
                                   source=PhotoSource.staged_demo)],
                 proposed_scene=proposed, reviewer_id="gate",
                 review_classification=ReviewClassification.interactive_demo))

    review = result.manifest.get("review")
    if not review:
        return False, "no review receipt sealed into the manifest"
    if review["scene_confirmed_sha256"] != result.manifest["scene_graph"]["sha256"]:
        return False, "review not bound to the sealed scene_graph hash"
    if decision_digest(
            classification=review["classification"], diff=review["diff"],
            reviewer_id=review["reviewer_id"],
            scene_confirmed_sha256=review["scene_confirmed_sha256"],
            scene_proposed_sha256=review["scene_proposed_sha256"],
    ) != review["decision_digest"]:
        return False, "decision_digest does not recompute from the sealed fields"
    if not any(r["path"].endswith(".color") and r["changed"] for r in review["diff"]):
        return False, "the human colour correction is missing from the sealed diff"

    def fetch(name):
        ref = result.artifacts.get(name)
        return storage.get(ref.key) if ref is not None else None

    receipt = verify_all(result.manifest, fetch)
    if not receipt.success:
        failing = [c["id"] for c in receipt.checks if not c["passed"]]
        return False, f"verify_all failed on a good case: {failing}"

    drifted = json.loads(json.dumps(result.manifest))
    drifted["scene_graph"]["sha256"] = "0" * 64
    review_check = next(c for c in verify_all(drifted, fetch).checks
                        if c["id"] == "review.decision_digest")
    if review_check["passed"]:
        return False, "drifting the confirmed scene did NOT void the approval"
    return True, ("server-computed review diff + recomputable digest; verify_all "
                  f"{len(receipt.checks)} checks pass; approval self-voids on drift")


# ── web application (real TestClient evidence over the API) ───────────────────
@lru_cache(maxsize=1)
def _api_client():
    from fastapi.testclient import TestClient

    import claimscene.api as capi

    return TestClient(capi.app)


def check_web_health_and_scenarios() -> tuple[bool, str]:
    """Health reports the effective backends; the zero-photo scenario set is
    served; scenario thumbnails serve; the image route blocks traversal."""
    client = _api_client()
    health = client.get("/health").json()
    if health.get("service") != "claimscene-api" or "provider" not in health:
        return False, "health surface is not the honest claimscene-api shape"
    scenarios = client.get("/scenarios").json().get("scenarios", [])
    if len(scenarios) < 6:
        return False, f"only {len(scenarios)} scenarios served (need >=6)"
    if client.get(scenarios[0]["thumbnail"]).status_code != 200:
        return False, "scenario thumbnail did not serve"
    traversal = client.get("/scenarios/s01_rear_end/images/..%2f..%2fmanifest.json")
    if traversal.status_code != 404:
        return False, "scenario image route did not block a traversal attempt"
    return True, (f"health honest; {len(scenarios)} zero-photo scenarios served; "
                  "thumbnails serve; image traversal blocked (404)")


def check_web_review_adjust_preview() -> tuple[bool, str]:
    """The review-adjust centrepiece: extract proposes a scene, an edit
    live-updates the static schematic, and the vocabulary gate still bites at
    the API boundary."""
    client = _api_client()
    scene = client.post("/cases/extract",
                        data={"scenario_id": "s01_rear_end"}).json()["scene"]
    first = client.post("/cases/preview-schematic", json=scene)
    if first.status_code != 200 or not first.json()["svg"].startswith("<svg"):
        return False, "preview did not return a static schematic"
    edited = json.loads(json.dumps(scene))
    edited["road"]["signal"] = "stop_sign"
    edited["vehicles"][0]["color"] = "red"
    second = client.post("/cases/preview-schematic", json=edited).json()["svg"]
    if second == first.json()["svg"]:
        return False, "an edit did not propagate into the schematic"
    edited["vehicles"] = [{"id": "v", "kind": "hovercraft", "color": "red"}]
    if client.post("/cases/preview-schematic", json=edited).status_code != 422:
        return False, "hallucinated vocabulary was accepted by the preview API"
    return True, ("extract→preview live-updates the factual schematic on edit; "
                  "hallucinated vocabulary rejected (422)")


def check_web_render_seal_and_playback() -> tuple[bool, str]:
    """Render seals a verifiable case over the API, resets client notes, and
    the schematic + illustration playback routes stream offline."""
    from claimscene.provenance import verify_manifest

    client = _api_client()
    scene = client.post("/cases/extract",
                        data={"scenario_id": "s02_left_cross"}).json()["scene"]
    scene["confidence_notes"] = ["INJECTED CLIENT NOTE"]
    body = client.post("/cases/render",
                       data={"scene": json.dumps(scene),
                             "scenario_id": "s02_left_cross"}).json()
    raw = client.get(body["manifest_url"]).content
    if not verify_manifest(json.loads(raw)):
        return False, "sealed case manifest failed verification over the API"
    if any("INJECTED" in w for w in body["warnings"]):
        return False, "client-supplied notes leaked into the sealed scene"
    if not any("human-in-the-loop" in w for w in body["warnings"]):
        return False, "server-authored human-in-the-loop note missing"
    schematic = client.get(body["schematic_url"])
    illustration = client.get(body["illustration_url"])
    if schematic.status_code != 200 or illustration.status_code != 200:
        return False, "a playback route did not stream (non-200)"
    if schematic.headers["content-type"] not in ("video/mp4", "image/png"):
        return False, f"unexpected schematic media {schematic.headers['content-type']}"
    return True, ("render seals a verifiable case; client notes reset; "
                  "schematic + illustration playback stream (200)")


def check_web_inbrowser_verify_fixture() -> tuple[bool, str]:
    """The committed golden manifest that the frontend's in-browser Verify test
    pins is a REAL sealed backend manifest: it re-hashes to its own seal, the
    .expected.json hash is current, the bytes are the canonical served form,
    and it exercises ensure_ascii escaping (so the browser canonicalizer is
    tested against non-ASCII)."""
    from claimscene.provenance import canonical_json, sha256_bytes

    fixtures = _REPO_ROOT / "frontend" / "src" / "test" / "fixtures"
    raw = (fixtures / "golden-manifest.json").read_bytes()
    expected = json.loads((fixtures / "golden-manifest.expected.json").read_text("utf-8"))
    manifest = json.loads(raw)
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    if sha256_bytes(canonical_json(body)) != manifest["manifest_hash"]:
        return False, "golden manifest does not re-hash to its own seal"
    if manifest["manifest_hash"] != expected["manifest_hash"]:
        return False, "golden .expected.json hash is stale"
    if raw != canonical_json(manifest):
        return False, "golden fixture is not the canonical served bytes"
    if b"\\u2014" not in raw:
        return False, "golden fixture does not exercise ensure_ascii escaping"
    return True, ("committed golden manifest re-hashes to its seal; canonical "
                  "bytes; exercises ensure_ascii — the in-browser verify is pinned")


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
            Check("utility.vlm_adapter_constrained",
                  "Live VLM adapter: repair loop + vocabulary gate (canned transport)",
                  2, run=check_utility_vlm_adapter_constrained),
            Check("utility.eval_scoreboard_floor",
                  "Committed eval set + measured accuracy above the floor",
                  2, run=check_utility_eval_scoreboard_floor),
        ]),
        Criterion("reproducibility", "Reproducibility", [
            Check("reproducibility.eval_digests_anchored",
                  "Committed eval inputs externally anchored; live digests match",
                  2, run=check_reproducibility_eval_digests_anchored),
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
                  gate_detail="TODO (user): create the claimscene B2 bucket + an "
                  "application key with PutObject entitlement (the current env "
                  "key failed the Genblaze sink transfer on 2026-07-21), run one "
                  "live case, confirm objects + index.jsonl in the bucket."),
        ]),
        Criterion("genblaze", "Use of Genblaze", [
            Check("genblaze.illustration_port_sealed",
                  "Illustration wired through the provider port; provenance sealed",
                  2, run=check_genblaze_illustration_port_sealed),
            Check("genblaze.sdk_adapter",
                  "Real Genblaze SDK adapter drives a real Pipeline + attaches inputs",
                  2, run=check_genblaze_sdk_contract),
            Check("genblaze.live_illustration",
                  "Committed live still+clip evidence re-hashes to its sealed manifest",
                  2, run=check_genblaze_live_illustration_evidence),
        ]),
        Criterion("security", "Application Security", [
            Check("security.traversal_safe_keys",
                  "Hostile case id/filename cannot inject into storage keys",
                  2, run=check_security_traversal_safe_keys),
            Check("security.upload_guard_rejects_dangerous",
                  "Upload guard: disguised-exe / oversized / over-count → 4xx; valid upload 200",
                  2, run=check_security_upload_guard_rejects_dangerous),
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
            Check("honest_media.sealed_approval_receipt",
                  "Sealed AI→human approval receipt: server diff, recomputable "
                  "digest, verify_all passes, self-voids on drift",
                  3, run=check_honest_sealed_approval_receipt),
        ]),
        Criterion("web", "Web Application (review-adjust UI + API)", [
            Check("web.health_and_scenarios",
                  "API health honest + zero-photo scenarios served + image traversal blocked",
                  2, run=check_web_health_and_scenarios),
            Check("web.review_adjust_preview",
                  "Extract→preview live-updates the schematic; vocabulary gate holds",
                  3, run=check_web_review_adjust_preview),
            Check("web.render_seal_and_playback",
                  "Render seals a verifiable case; playback routes stream offline",
                  3, run=check_web_render_seal_and_playback),
            Check("web.inbrowser_verify_fixture",
                  "Committed golden manifest re-hashes to its seal (verify pinned)",
                  2, run=check_web_inbrowser_verify_fixture),
            Check("web.live_deploy_reachable",
                  "Deployed web app reachable (health=200) with the built client",
                  2, user_gated=True,
                  gate_detail="TODO (user/main loop): deploy the container to "
                  "Cloud Run (bash deploy/deploy-cloudrun.sh) and confirm "
                  "GET /health is 200 and GET / serves the built React client."),
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
