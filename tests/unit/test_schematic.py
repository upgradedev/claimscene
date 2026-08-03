"""Schematic renderer: golden static SVG + deterministic watermarked frames."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from claimscene.adapters.fakes import _scene_left_cross, _scene_roundabout_sideswipe
from claimscene.layout import LayoutEngine
from claimscene.provenance import WATERMARK
from claimscene.schematic import (
    PillowSchematicRenderer,
    build_static_svg,
    impact_frame_index,
    render_frame,
    world_to_pixel,
)

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "schematic_static.svg"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def timeline():
    return LayoutEngine().build(_scene_left_cross())


# ── world_to_pixel: the projection claimscene.camera reuses ─────────────────
def test_world_to_pixel_maps_the_origin_to_the_canvas_center():
    assert world_to_pixel(0.0, 0.0) == (480.0, 360.0)  # default 960x720x8.0


def test_world_to_pixel_matches_the_documented_formula():
    # East (+x) moves right; north (+y) moves UP the canvas (pixel y
    # decreases) -- the same world-frame convention layout.py documents.
    assert world_to_pixel(10.0, -5.0) == (480.0 + 80.0, 360.0 + 40.0)
    assert world_to_pixel(-3.0, 4.0, width=200, height=100, scale=2.0) == (94.0, 42.0)


def test_static_svg_matches_golden(timeline):
    svg = build_static_svg(timeline, title="golden")
    if os.environ.get("CLAIMSCENE_UPDATE_GOLDEN") == "1":  # explicit dev affordance
        GOLDEN.write_text(svg, encoding="utf-8", newline="\n")
    expected = GOLDEN.read_text(encoding="utf-8")
    assert svg.replace("\r\n", "\n") == expected.replace("\r\n", "\n")


def test_svg_contains_watermark_and_labels(timeline):
    svg = build_static_svg(timeline)
    assert svg.count(WATERMARK) >= 2  # big diagonal-style + footer
    assert "veh_a" in svg and "veh_b" in svg
    assert "x_intersection" in svg
    assert "struck at 2 o&apos;clock" in svg or "struck at 2 o'clock" in svg


def test_svg_shows_damage_severity_markers(timeline):
    svg = build_static_svg(timeline)
    assert "#ef476f" in svg  # crush marker (veh_a right front quarter)
    assert "#f4845f" in svg  # dent marker (veh_b front)


def test_frames_are_valid_png_and_full_grid(timeline):
    renderer = PillowSchematicRenderer(animate=False)
    art = renderer.render(timeline)
    assert art.frame_count == len(timeline.tracks[0].poses) == 25
    assert all(f.startswith(PNG_MAGIC) for f in art.frames_png)
    assert art.hero_png == art.frames_png[impact_frame_index(timeline)]
    assert art.animation_mp4 is None  # animate=False never encodes


def test_seed_png_is_a_distinct_unannotated_impact_frame(timeline):
    """``SchematicArtifacts.seed_png`` is what pipeline.py hands the
    illustration clip step as its seed image (see pipeline.py step 5) -- it
    must be a real, valid PNG at the impact frame, and it must differ from
    the sealed hero frame (which carries the schematic's own watermark and
    labels burned in already: feeding that forward would double the on-clip
    caption, see the PR that added this field).

    It is also rendered at its OWN scale, framed on the impact rather than at
    the sealed frames' fixed 8 px/m, because the seed is an image-to-video
    prompt and a model given two 4.5 m cars in a 120 m field recomposes onto
    the subject. ``seed_scale`` reports the scale actually used."""
    renderer = PillowSchematicRenderer(animate=False)
    art = renderer.render(timeline)
    assert art.seed_png.startswith(PNG_MAGIC)
    assert art.seed_png != art.hero_png
    assert art.seed_scale > 0
    assert art.seed_png == render_frame(
        timeline, impact_frame_index(timeline), annotate=False,
        scale=art.seed_scale)


def test_render_frame_annotate_false_is_opt_in_and_differs_from_default(timeline):
    """``annotate=True`` stays the default, byte-identical to every call site
    that predates this parameter (the sealed schematic artifacts never pass
    it explicitly). ``annotate=False`` is a distinct, purpose-built rendering
    used only for the illustration seed (see
    ``PillowSchematicRenderer.render``)."""
    i = impact_frame_index(timeline)
    default = render_frame(timeline, i, title="t")
    explicit_true = render_frame(timeline, i, title="t", annotate=True)
    bare = render_frame(timeline, i, title="t", annotate=False)
    assert default == explicit_true
    assert default != bare


def test_frames_deterministic_within_environment(timeline):
    a = render_frame(timeline, 5)
    b = render_frame(timeline, 5)
    assert a == b


def test_frames_vary_over_time(timeline):
    early = render_frame(timeline, 0)
    late = render_frame(timeline, 20)
    assert early != late


def test_every_layout_template_renders():
    for builder in (_scene_left_cross, _scene_roundabout_sideswipe):
        timeline = LayoutEngine().build(builder())
        svg = build_static_svg(timeline)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        frame = render_frame(timeline, 0)
        assert frame.startswith(PNG_MAGIC)


# ── seed framing ────────────────────────────────────────────────────────────
def _t_junction_timeline():
    from claimscene.layout import LayoutEngine
    from claimscene.scenarios import get_scenario
    from claimscene.scene import SceneGraph
    return LayoutEngine().build(
        SceneGraph.model_validate(get_scenario("s05_t_intersection")["truth"]))


def test_seed_is_framed_on_the_impact_not_on_the_whole_approach():
    """The seed is an image-to-video prompt, and a model composes.

    At the sealed frames' fixed 8 px/m the visible world is 120 m wide and a
    4.5 m car is 3.8% of the frame, so the model reframes onto the subject.
    That is how a top-down T-junction came back as a close-up of two bumpers
    reading as a head-on. Framing the seed on the impact removes the reason
    to recompose.
    """
    from claimscene.schematic import SEED_SCALE_MAX, SEED_SCALE_MIN, seed_scale_for

    tl = _t_junction_timeline()
    scale = seed_scale_for(tl)
    assert SEED_SCALE_MIN <= scale <= SEED_SCALE_MAX
    # Strictly tighter than the sealed view, and by a wide margin.
    assert scale > SEED_SCALE_MIN * 2
    car_frac = 4.5 / (960 / scale)
    assert car_frac > 0.10, f"a car still fills only {car_frac:.1%} of the seed"


def test_seed_scale_is_deterministic():
    from claimscene.schematic import seed_scale_for
    tl = _t_junction_timeline()
    assert seed_scale_for(tl) == seed_scale_for(tl)


def test_seed_framing_never_changes_the_sealed_artifacts():
    """The seed gets its own scale; every SEALED artifact keeps the fixed one.

    This is the load-bearing guarantee: hero_png, the animation frames and
    the SVG are what a case seals and re-verifies against, so a change made
    for the generative layer's benefit must not move their bytes.
    """
    from claimscene.schematic import PillowSchematicRenderer, render_frame

    tl = _t_junction_timeline()
    art = PillowSchematicRenderer(animate=False).render(tl, title="t")
    from claimscene.schematic import impact_frame_index
    i = impact_frame_index(tl)
    assert art.hero_png == render_frame(tl, i, title="t", width=960, height=720,
                                        scale=8.0)
    assert art.seed_scale > 8.0
    assert art.seed_png != render_frame(tl, i, width=960, height=720, scale=8.0,
                                        annotate=False)
