#!/usr/bin/env python3
"""Measure extraction accuracy: the live VLM ladder vs the committed eval set.

Runs each requested ladder model over every scenario in ``eval/scenarios/``
(3 photos + the claimant context note — the product's real input surface),
validates replies through the constrained SceneGraph gate, scores them
field-by-field against the authored ground truth
(:mod:`claimscene.evaluation`), and writes a dated scoreboard:

    eval/results/<date>_extraction_eval.json   (machine-readable)
    eval/results/<date>_extraction_eval.md     (the table the README cites)

The system prompt's SHA-256 is recorded so every scoreboard is traceable to
the exact prompt that produced it. Costs a few cents of VLM tokens per full
run (token usage is captured per call and reported).

    python scripts/eval_extraction.py --live              # all GMI rungs
    python scripts/eval_extraction.py --live --models google/gemma-4-31b-it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

SCENARIOS_DIR = _REPO_ROOT / "eval" / "scenarios"
RESULTS_DIR = _REPO_ROOT / "eval" / "results"


def load_scenarios(scenarios_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((scenarios_dir / "manifest.json").read_text("utf-8"))
    rows = []
    for spec in manifest["scenarios"]:
        sdir = scenarios_dir / spec["id"]
        images = [(name, (sdir / name).read_bytes()) for name in spec["images"]]
        rows.append({"id": spec["id"], "context": spec["context"],
                     "truth": spec["truth"], "images": images})
    return manifest, rows


def run_model(rung, scenarios: list[dict], manifest: dict) -> dict:
    from claimscene.adapters.vlm_extractor import VlmExtractor
    from claimscene.case import CasePhoto, PhotoRole, PhotoSource
    from claimscene.evaluation import aggregate, score_scene
    from claimscene.scene import SceneGraph

    extractor = VlmExtractor([rung])
    scores, latencies, failures = [], [], []
    for row in scenarios:
        photos = [
            CasePhoto(filename=name, data=data, media_type="image/jpeg",
                      role=PhotoRole.scene_photo,
                      source=PhotoSource.synthetic_generated,
                      attribution=manifest["attribution"],
                      license=manifest.get("license"))
            for name, data in row["images"]
        ]
        truth = SceneGraph.model_validate(row["truth"])
        t0 = time.time()
        try:
            pred = extractor.extract(photos, context=row["context"])
        except Exception as exc:
            pred = None
            failures.append({"id": row["id"], "error": str(exc)[:300]})
            print(f"    {row['id']}: EXTRACTION FAILED — {str(exc)[:120]}")
        latencies.append(round(time.time() - t0, 1))
        score = score_scene(row["id"], truth, pred)
        scores.append(score)
        print(f"    {row['id']}: {score.pct}%  ({latencies[-1]}s)", flush=True)

    report = aggregate(scores)
    report["latency_s"] = latencies
    report["failures"] = failures
    report["usage"] = {
        "calls": len(extractor.usage_log),
        "prompt_tokens": sum(u["prompt_tokens"] or 0
                             for u in extractor.usage_log),
        "completion_tokens": sum(u["completion_tokens"] or 0
                                 for u in extractor.usage_log),
    }
    return report


def render_markdown(payload: dict) -> str:
    lines = ["# ClaimScene extraction-accuracy scoreboard", ""]
    lines.append(f"- Date: {payload['generated_at']}")
    lines.append(f"- Scenarios: {payload['scenario_count']} "
                 "(synthetic toy-diorama photo sets, 3 views + context note "
                 "each; ground truth authored with the generation prompts — "
                 "self-consistent by construction)")
    lines.append(f"- Prompt version: `sha256:{payload['prompt_sha256'][:16]}`")
    lines.append(f"- Headline: **{payload['headline']['model']} — "
                 f"{payload['headline']['overall_pct']}% weighted field "
                 "accuracy**")
    lines.append("")
    lines.append("## Per model")
    lines.append("")
    field_names = sorted(next(iter(payload["models"].values()))["per_field"])
    header = "| model | overall |" + "".join(f" {f} |" for f in field_names)
    sep = "|---" * (len(field_names) + 2) + "|"
    lines += [header, sep]
    for model, rep in payload["models"].items():
        cells = "".join(
            f" {rep['per_field'][f]['hits']}/{rep['per_field'][f]['total']} |"
            for f in field_names)
        lines.append(f"| `{model}` | **{rep['overall_pct']}%** |{cells}")
    lines.append("")
    lines.append("## Per scenario (headline model)")
    lines.append("")
    lines += ["| scenario | score | misses |", "|---|---|---|"]
    headline_rep = payload["models"][payload["headline"]["model"]]
    for s in headline_rep["scenarios"]:
        misses = ", ".join(n.removeprefix("miss: ")
                           for n in s["notes"]) or "—"
        lines.append(f"| {s['id']} | {s['pct']}% | {misses} |")
    lines.append("")
    lines.append("## Token usage")
    lines.append("")
    lines += ["| model | calls | prompt tokens | completion tokens |",
              "|---|---|---|---|"]
    for model, rep in payload["models"].items():
        u = rep["usage"]
        lines.append(f"| `{model}` | {u['calls']} | {u['prompt_tokens']} | "
                     f"{u['completion_tokens']} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true",
                        help="required: this run calls paid endpoints")
    parser.add_argument("--models", default="all",
                        help="comma-separated ladder model ids, or 'all'")
    parser.add_argument("--scenarios-dir", default=str(SCENARIOS_DIR))
    parser.add_argument("--out-dir", default=str(RESULTS_DIR))
    parser.add_argument("--tag", default="",
                        help="suffix for the result filenames (e.g. iter2)")
    args = parser.parse_args(argv)

    if not args.live:
        print("refusing to run without --live (this calls paid endpoints)",
              file=sys.stderr)
        return 2

    from claimscene.adapters.vlm_extractor import (
        build_system_prompt,
        default_ladder,
    )

    ladder = default_ladder()
    if not ladder:
        print("no ladder rungs configured (set GMI_API_KEY)", file=sys.stderr)
        return 2
    wanted = ([m.strip() for m in args.models.split(",")]
              if args.models != "all" else [r.model for r in ladder])
    rungs = [r for r in ladder if r.model in wanted]
    if not rungs:
        print(f"no configured rung matches {wanted}", file=sys.stderr)
        return 2

    manifest, scenarios = load_scenarios(Path(args.scenarios_dir))
    prompt_sha = hashlib.sha256(
        build_system_prompt().encode("utf-8")).hexdigest()

    models_report: dict[str, dict] = {}
    for rung in rungs:
        print(f"== {rung.model}", flush=True)
        models_report[rung.model] = run_model(rung, scenarios, manifest)

    primary = rungs[0].model
    payload = {
        "schema": "claimscene/extraction-eval/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": len(scenarios),
        "scenarios_manifest_generated_at": manifest["generated_at"],
        "prompt_sha256": prompt_sha,
        "headline": {"model": primary,
                     "overall_pct": models_report[primary]["overall_pct"]},
        "models": models_report,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "_extraction_eval"
    if args.tag:
        stem += f"_{args.tag}"
    (out_dir / f"{stem}.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(render_markdown(payload),
                                        encoding="utf-8")
    print(f"\nwrote {out_dir / (stem + '.json')}")
    for model, rep in models_report.items():
        print(f"  {model}: {rep['overall_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
