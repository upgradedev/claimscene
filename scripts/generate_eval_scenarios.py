#!/usr/bin/env python3
"""Generate the synthetic eval set: 7 staged scenarios x 3 consistent views.

PAID, RUN-ONCE tooling (not part of CI). For each scenario we author BOTH the
diorama generation prompts AND the ground-truth SceneGraph, so the set is
self-consistent by construction: the photos depict exactly the scene the
truth describes. Images are toy-diorama seedream renders (source:
synthetic_generated) — no real accident imagery, people, or plates.

Per scenario:
  1. master view  — seedream-5.0-lite text→image (slightly elevated 3/4 view)
  2. view B       — seedream reference-image call (opposite side), passing the
                    master's GMI-hosted URL via ``params={"image":[url]}``
  3. view C       — seedream reference-image close-up of the primary damage

All generation goes through the real :class:`GenblazeMediaProvider` adapter
(the same code path the case pipeline uses live). Images are recompressed to
JPEG under 300 KB before landing in ``eval/scenarios/<id>/view_*.jpg``, and
``eval/scenarios/manifest.json`` seals ids, truths, prompts, context notes,
attribution, and estimated spend.

Cost: 21 x $0.035 = ~$0.74 for the full set (seedream-5.0-lite pricing).
Already-generated scenarios are skipped (resume-safe); use ``--only <id>``
to (re)generate one scenario, ``--dry-run`` to validate truths only.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

SCENARIOS_DIR = _REPO_ROOT / "eval" / "scenarios"
STILL_MODEL = "seedream-5.0-lite"
COST_PER_IMAGE_USD = 0.035
MAX_JPEG_BYTES = 300 * 1024

_DIORAMA_SUFFIX = (
    " Miniature diecast toy car diorama on a printed road play mat, tabletop "
    "scale-model scene, clean studio product photography, softbox lighting, "
    "no people, no injuries, collectible toy scale models."
)


def _scenario(sid, context, truth, master, view_b, view_c):
    return {"id": sid, "context": context, "truth": truth,
            "prompts": {"master": master + _DIORAMA_SUFFIX,
                        "view_b": view_b, "view_c": view_c}}


def _truth(layout, lanes, signal, vehicles, movements, impacts, sequence):
    return {
        "schema": "claimscene/scene/v1",
        "road": {"layout": layout, "lanes_per_direction": lanes,
                 "signal": signal},
        "vehicles": vehicles, "movements": movements, "impacts": impacts,
        "sequence": sequence,
    }


def _veh(vid, kind, color, clock, severity):
    return {"id": vid, "kind": kind, "color": color,
            "damage": [{"clock_position": clock, "severity": severity}]}


def _mov(vid, approach, maneuver, speed):
    return {"vehicle_id": vid, "approach": approach, "maneuver": maneuver,
            "speed_band": speed}


SCENARIOS = [
    _scenario(
        "s01_rear_end",
        "Both vehicles were heading south (so they approached from the north) "
        "on a straight avenue with one lane per direction and a traffic "
        "light. The blue car was stopped at the light; the red car behind it "
        "was doing normal city speed and failed to stop.",
        _truth("straight", 1, "traffic_light",
               [_veh("veh_a", "car", "blue", 6, "crush"),
                _veh("veh_b", "car", "red", 12, "dent")],
               [_mov("veh_a", "N", "straight", "stopped"),
                _mov("veh_b", "N", "straight", "moderate")],
               [{"vehicle_id": "veh_a", "clock_position": 6},
                {"vehicle_id": "veh_b", "clock_position": 12}],
               ["vehicles_approach", "braking", "impact",
                "vehicles_come_to_rest"]),
        "A blue toy sedan and a red toy hatchback in a rear-end collision on "
        "a straight two-way road with a miniature traffic light: the red "
        "car's front bumper pressed into the blue car's crushed rear bumper "
        "and trunk, both cars in the same lane facing the same direction, "
        "dented rear on the blue car, small dent on the red car's front.",
        "Same miniature toy car diorama scene as the reference image: the "
        "exact same blue toy sedan and red toy hatchback in the exact same "
        "rear-end collision positions on the same road play mat. Now "
        "photographed from the opposite side of the scene, from ahead of the "
        "blue sedan looking back toward the red hatchback. Same studio "
        "product photography and lighting.",
        "Close-up macro shot of the same miniature toy car diorama as the "
        "reference image: tight framing on the contact point where the red "
        "hatchback's dented front bumper meets the blue sedan's crushed rear "
        "bumper and trunk. Same cars, same positions, same play mat, same "
        "studio lighting."),
    _scenario(
        "s02_left_cross",
        "At a four-way crossroads controlled by a traffic light. The silver "
        "car arrived from the north and was turning left slowly; the green "
        "van arrived from the south going straight at normal speed and "
        "struck the turning car's right-front area.",
        _truth("x_intersection", 1, "traffic_light",
               [_veh("veh_a", "car", "silver", 2, "crush"),
                _veh("veh_b", "van", "green", 12, "dent")],
               [_mov("veh_a", "N", "left_turn", "low"),
                _mov("veh_b", "S", "straight", "moderate")],
               [{"vehicle_id": "veh_a", "clock_position": 2},
                {"vehicle_id": "veh_b", "clock_position": 12}],
               ["vehicles_approach", "evasive_steering", "impact",
                "vehicles_come_to_rest"]),
        "A silver toy sedan and a green toy delivery van collided at a "
        "four-way crossroads printed on the play mat, with a miniature "
        "traffic light: the silver sedan is angled mid-left-turn in the "
        "middle of the junction, the green van's dented front bumper is "
        "pressed against the sedan's crushed right-front fender and wheel "
        "arch, the two vehicles came from opposite directions.",
        "Same miniature toy car diorama scene as the reference image: the "
        "exact same silver toy sedan and green toy van in the exact same "
        "junction collision positions on the same crossroads play mat. Now "
        "photographed from the opposite side of the junction, from behind "
        "the green van looking toward the silver sedan. Same studio product "
        "photography and lighting.",
        "Close-up macro shot of the same miniature toy car diorama as the "
        "reference image: tight framing on the silver sedan's crushed "
        "right-front fender where the green van's front bumper touches it. "
        "Same vehicles, same positions, same play mat, same studio "
        "lighting."),
    _scenario(
        "s03_parking_reverse",
        "In a parking lot with marked bays, no traffic signs. The white van "
        "was slowly reversing out of a bay (it had pulled in nose-first from "
        "the north side) and backed into a black car parked behind it, "
        "scraping the parked car's left side. The black car was parked and "
        "empty.",
        _truth("parking_lot", 1, "none",
               [_veh("veh_a", "van", "white", 6, "dent"),
                _veh("veh_b", "car", "black", 9, "scratch")],
               [_mov("veh_a", "N", "reversing", "low"),
                _mov("veh_b", "N", "parked", "stopped")],
               [{"vehicle_id": "veh_b", "clock_position": 9},
                {"vehicle_id": "veh_a", "clock_position": 6}],
               ["impact", "vehicles_come_to_rest"]),
        "A white toy delivery van and a black toy sedan in a parking-lot "
        "fender-bender on a play mat printed with white parking bays: the "
        "van sits half out of a bay at a reversing angle, its dented rear "
        "door panel touching the parked black sedan's scratched left-side "
        "doors, the black sedan neatly parked in its row.",
        "Same miniature toy car diorama scene as the reference image: the "
        "exact same white toy van and black toy sedan in the exact same "
        "parking-lot contact positions on the same parking play mat. Now "
        "photographed from the opposite side, from beyond the black sedan "
        "looking back at the van's rear. Same studio product photography and "
        "lighting.",
        "Close-up macro shot of the same miniature toy car diorama as the "
        "reference image: tight framing on the white van's dented rear "
        "corner where it touches the black sedan's scratched left-side "
        "doors. Same vehicles, same positions, same play mat, same studio "
        "lighting."),
    _scenario(
        "s04_roundabout_sideswipe",
        "At a small roundabout with yield markings. The yellow car entered "
        "slowly from the north; the black motorcycle entered slowly from the "
        "west. They sideswiped inside the roundabout: the car's right side "
        "against the motorcycle's left side. Everyone stayed upright.",
        _truth("roundabout", 1, "yield",
               [_veh("veh_a", "car", "yellow", 3, "scratch"),
                _veh("veh_b", "motorcycle", "black", 9, "scratch")],
               [_mov("veh_a", "N", "straight", "low"),
                _mov("veh_b", "W", "straight", "low")],
               [{"vehicle_id": "veh_a", "clock_position": 3},
                {"vehicle_id": "veh_b", "clock_position": 9}],
               ["vehicles_approach", "impact", "vehicles_come_to_rest"]),
        "A yellow toy sedan and a black toy motorcycle side by side inside a "
        "miniature roundabout printed on the play mat with yield triangles: "
        "the motorcycle leans slightly against the sedan's right-side doors, "
        "a light scratch mark along the sedan's right side and along the "
        "motorcycle's left flank, both pointed around the roundabout.",
        "Same miniature toy diorama scene as the reference image: the exact "
        "same yellow toy sedan and black toy motorcycle in the exact same "
        "sideswipe positions inside the same roundabout play mat. Now "
        "photographed from the opposite side of the roundabout, looking at "
        "the sedan's left side with the motorcycle beyond it. Same studio "
        "product photography and lighting.",
        "Close-up macro shot of the same miniature toy diorama as the "
        "reference image: tight framing on the yellow sedan's scratched "
        "right-side doors where the black motorcycle leans against them. "
        "Same vehicles, same positions, same play mat, same studio "
        "lighting."),
    _scenario(
        "s05_t_intersection",
        "At a T-junction with a stop sign on the side road. The red truck "
        "pulled out slowly from the side road (which joins from the south) "
        "turning right; the white car came along the main road from the west "
        "at normal speed. The truck's front struck the car's right side.",
        _truth("t_intersection", 1, "stop_sign",
               [_veh("veh_a", "truck", "red", 12, "dent"),
                _veh("veh_b", "car", "white", 3, "crush")],
               [_mov("veh_a", "S", "right_turn", "low"),
                _mov("veh_b", "W", "straight", "moderate")],
               [{"vehicle_id": "veh_a", "clock_position": 12},
                {"vehicle_id": "veh_b", "clock_position": 3}],
               ["vehicles_approach", "braking", "impact",
                "vehicles_come_to_rest"]),
        "A red toy flatbed truck and a white toy sedan collided at a "
        "T-junction printed on the play mat with a miniature stop sign on "
        "the side road: the truck is angled emerging from the side road, its "
        "dented front bumper pressed into the white sedan's crushed "
        "right-side doors as the sedan passes along the main road.",
        "Same miniature toy diorama scene as the reference image: the exact "
        "same red toy truck and white toy sedan in the exact same T-junction "
        "collision positions on the same play mat. Now photographed from the "
        "opposite side, from beyond the white sedan's left side looking "
        "toward the truck. Same studio product photography and lighting.",
        "Close-up macro shot of the same miniature toy diorama as the "
        "reference image: tight framing on the white sedan's crushed "
        "right-side doors where the red truck's dented front bumper meets "
        "them. Same vehicles, same positions, same play mat, same studio "
        "lighting."),
    _scenario(
        "s06_lane_change",
        "On a straight road with two lanes in each direction, no signals. "
        "Both vehicles were heading south (approaching from the north) at "
        "normal speed. The gray car changed lanes to the right into the blue "
        "van: the car's right side scraped the van's left side.",
        _truth("straight", 2, "none",
               [_veh("veh_a", "car", "gray", 3, "scratch"),
                _veh("veh_b", "van", "blue", 9, "dent")],
               [_mov("veh_a", "N", "lane_change", "moderate"),
                _mov("veh_b", "N", "straight", "moderate")],
               [{"vehicle_id": "veh_a", "clock_position": 3},
                {"vehicle_id": "veh_b", "clock_position": 9}],
               ["vehicles_approach", "evasive_steering", "impact",
                "vehicles_come_to_rest"]),
        "A gray toy sedan and a blue toy delivery van side by side on a "
        "straight play-mat road with two marked lanes per direction: the "
        "sedan is angled mid-lane-change into the van's lane, its scratched "
        "right-side doors against the van's dented left-side panel, both "
        "vehicles facing the same direction in adjacent lanes.",
        "Same miniature toy diorama scene as the reference image: the exact "
        "same gray toy sedan and blue toy van in the exact same sideswipe "
        "positions on the same two-lane play mat. Now photographed from the "
        "opposite side of the road, from beyond the blue van looking across "
        "at the gray sedan. Same studio product photography and lighting.",
        "Close-up macro shot of the same miniature toy diorama as the "
        "reference image: tight framing on the contact line where the gray "
        "sedan's scratched right side touches the blue van's dented left "
        "panel. Same vehicles, same positions, same play mat, same studio "
        "lighting."),
    _scenario(
        "s07_intersection_truck",
        "At a four-way intersection with two lanes per direction and stop "
        "signs. The green truck came from the west going straight at normal "
        "speed; the white car came slowly from the north going straight. "
        "The truck's front struck the car's left side in the middle of the "
        "junction.",
        _truth("x_intersection", 2, "stop_sign",
               [_veh("veh_a", "truck", "green", 12, "dent"),
                _veh("veh_b", "car", "white", 9, "crush")],
               [_mov("veh_a", "W", "straight", "moderate"),
                _mov("veh_b", "N", "straight", "low")],
               [{"vehicle_id": "veh_a", "clock_position": 12},
                {"vehicle_id": "veh_b", "clock_position": 9}],
               ["vehicles_approach", "braking", "impact",
                "vehicles_come_to_rest"]),
        "A green toy box truck and a white toy sedan collided in the middle "
        "of a four-way intersection printed on the play mat with miniature "
        "stop signs: the truck's dented front bumper is pressed into the "
        "white sedan's crushed left-side doors, the two vehicles at right "
        "angles to each other in the junction center.",
        "Same miniature toy diorama scene as the reference image: the exact "
        "same green toy truck and white toy sedan in the exact same "
        "right-angle collision positions on the same intersection play mat. "
        "Now photographed from the opposite corner of the intersection, from "
        "beyond the sedan's right side. Same studio product photography and "
        "lighting.",
        "Close-up macro shot of the same miniature toy diorama as the "
        "reference image: tight framing on the white sedan's crushed "
        "left-side doors where the green truck's dented front bumper meets "
        "them. Same vehicles, same positions, same play mat, same studio "
        "lighting."),
]

VIEW_FILES = {"master": "view_a.jpg", "view_b": "view_b.jpg",
              "view_c": "view_c.jpg"}


def validate_truths() -> None:
    from claimscene.scene import SceneGraph

    for spec in SCENARIOS:
        SceneGraph.model_validate(spec["truth"])
    ids = [s["id"] for s in SCENARIOS]
    assert len(set(ids)) == len(ids), "duplicate scenario ids"
    print(f"OK: {len(SCENARIOS)} scenario truths validate against SceneGraph v1")


def compress_to_jpeg(png_bytes: bytes, *, max_dim: int = 1024,
                     max_bytes: int = MAX_JPEG_BYTES) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim))
    for quality in (85, 78, 70, 60, 50):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
    return buf.getvalue()  # smallest attempt; caller asserts the cap


def generate_scenario(adapter, spec: dict, out_dir: Path) -> dict:
    """Three consistent views via the real Genblaze adapter. Returns stats."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"id": spec["id"], "images": 0, "spend_usd": 0.0}

    print(f"[{spec['id']}] master (t2i)...", flush=True)
    t0 = time.time()
    master_png = adapter.generate(
        model=STILL_MODEL, prompt=spec["prompts"]["master"], modality="image",
        params={"size": "2K", "output_format": "png", "max_images": 1,
                "watermark": False})
    master_url = adapter.last_asset_url
    print(f"  {len(master_png)} B in {time.time() - t0:.1f}s", flush=True)
    if not master_url:
        raise RuntimeError("master still has no hosted URL to reference")
    views = {"master": master_png}
    stats["images"] += 1
    stats["spend_usd"] += COST_PER_IMAGE_USD

    for view in ("view_b", "view_c"):
        print(f"[{spec['id']}] {view} (reference-image)...", flush=True)
        t0 = time.time()
        views[view] = adapter.generate(
            model=STILL_MODEL, prompt=spec["prompts"][view], modality="image",
            params={"size": "2K", "output_format": "png", "max_images": 1,
                    "watermark": False, "image": [master_url]})
        print(f"  {len(views[view])} B in {time.time() - t0:.1f}s", flush=True)
        stats["images"] += 1
        stats["spend_usd"] += COST_PER_IMAGE_USD

    for view, filename in VIEW_FILES.items():
        jpeg = compress_to_jpeg(views[view])
        assert len(jpeg) <= MAX_JPEG_BYTES, (
            f"{spec['id']}/{filename} is {len(jpeg)} B (> {MAX_JPEG_BYTES})")
        (out_dir / filename).write_bytes(jpeg)
        print(f"  wrote {filename} ({len(jpeg)} B)", flush=True)
    return stats


def write_manifest(spend_rows: list[dict]) -> None:
    manifest = {
        "schema": "claimscene/eval-scenarios/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/generate_eval_scenarios.py",
        "still_model": STILL_MODEL,
        "provider": "genblaze (GMI Cloud request queue)",
        "image_source": "synthetic_generated",
        "attribution": ("seedream-5.0-lite toy-diorama renders generated by "
                        "the ClaimScene team via the GenblazeMediaProvider "
                        "adapter; reference-image chaining for view "
                        "consistency"),
        "license": "CC0-1.0 (synthetic, no real scenes/people/plates)",
        "methodology": ("self-consistent by construction: the generation "
                        "prompts and the ground-truth SceneGraph were "
                        "authored together, so the photos depict exactly the "
                        "scene the truth describes"),
        "cost_per_image_usd": COST_PER_IMAGE_USD,
        "spend": spend_rows,
        "scenarios": [
            {"id": s["id"], "context": s["context"],
             "images": list(VIEW_FILES.values()), "truth": s["truth"],
             "prompts": s["prompts"]}
            for s in SCENARIOS
        ],
    }
    path = SCENARIOS_DIR / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=None, help="generate one scenario id")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate truths + prompts, no generation")
    parser.add_argument("--manifest-only", action="store_true",
                        help="rewrite manifest.json without generating")
    args = parser.parse_args(argv)

    validate_truths()
    if args.dry_run:
        return 0

    spend_path = SCENARIOS_DIR / "_spend.json"
    spend_rows: list[dict] = (
        json.loads(spend_path.read_text()) if spend_path.exists() else [])

    if not args.manifest_only:
        import os

        if not os.environ.get("GMI_API_KEY"):
            print("FATAL: GMI_API_KEY not set", file=sys.stderr)
            return 2
        from claimscene.adapters.genblaze_provider import GenblazeMediaProvider

        # No storage backend: the reference chaining rides on GMI-hosted
        # output URLs (probe-verified), so eval generation needs no B2.
        adapter = GenblazeMediaProvider(backend=None, bucket=None)
        for spec in SCENARIOS:
            if args.only and spec["id"] != args.only:
                continue
            out_dir = SCENARIOS_DIR / spec["id"]
            done = all((out_dir / f).exists() for f in VIEW_FILES.values())
            if done and not args.only:
                print(f"[{spec['id']}] already generated — skipping")
                continue
            spend_rows.append(generate_scenario(adapter, spec, out_dir))
            spend_path.parent.mkdir(parents=True, exist_ok=True)
            spend_path.write_text(json.dumps(spend_rows, indent=2))

    write_manifest(spend_rows)
    total = sum(r["spend_usd"] for r in spend_rows)
    print(f"TOTAL generation spend: ${total:.2f} "
          f"({sum(r['images'] for r in spend_rows)} images)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
