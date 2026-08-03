"""Schematic renderer: golden static SVG + deterministic watermarked frames."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from claimscene.adapters.fakes import (
    _scene_left_cross,
    _scene_parking_reverse,
    _scene_rear_end,
    _scene_roundabout_sideswipe,
)
from claimscene.layout import LayoutEngine
from claimscene.provenance import WATERMARK
from claimscene.schematic import (
    PillowSchematicRenderer,
    build_static_svg,
    impact_frame_index,
    render_frame,
    seed_scale_for,
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
        seed_style=True, scale=art.seed_scale)


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
    for builder in (_scene_rear_end, _scene_left_cross, _scene_parking_reverse,
                    _scene_roundabout_sideswipe):
        timeline = LayoutEngine().build(builder())
        svg = build_static_svg(timeline)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        frame = render_frame(timeline, 0)
        assert frame.startswith(PNG_MAGIC)
        # The seed style derives its kerb from whatever the layout's own
        # drivable surface happens to be, so every template has to survive
        # it -- including the roundabout, whose surface has a hole in it,
        # and the parking lot, whose "junction" is a single slab.
        seed = render_frame(timeline, 0, annotate=False, seed_style=True)
        assert seed.startswith(PNG_MAGIC)
        assert seed != frame


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


# ── seed road legibility: the road has to constrain where a vehicle can be ───
def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance, so the assertions below are in the same unit
    the defect was measured in."""
    def channel(v: int) -> float:
        f = v / 255.0
        return f / 12.92 if f <= 0.04045 else ((f + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _open_frame(png: bytes):
    import io

    from PIL import Image
    return Image.open(io.BytesIO(png)).convert("RGB")


def _patch_median(png: bytes, world_xy: tuple[float, float], scale: float
                  ) -> tuple[int, int, int]:
    """Median colour of a small patch around a WORLD point in the render.

    A median over a patch rather than one pixel on purpose: the blueprint
    render this is compared against draws a survey grid, and a single sample
    that happened to land on a grid line would measure the grid instead of
    the surface under it.
    """
    img = _open_frame(png)
    px, py = world_to_pixel(*world_xy, scale=scale)
    xs = range(int(px) - 4, int(px) + 5)
    ys = range(int(py) - 4, int(py) + 5)
    samples = [img.getpixel((x, y)) for x in xs for y in ys]
    return tuple(sorted(c[i] for c in samples)[len(samples) // 2] for i in range(3))


#: A point on the main carriageway, and one out in the verge, both on the
#: same side of the T-junction and both well clear of the vehicles, the
#: contact burst and the centre line. In metres, world frame.
_ON_CARRIAGEWAY = (-12.0, -2.0)
_OFF_CARRIAGEWAY = (-12.0, 9.0)


def test_seed_makes_the_drivable_surface_dominant_and_high_contrast():
    """The measured root cause of vehicles rendering off the carriageway.

    In the blueprint palette the road is ``#16242e`` on a ``#0e1a22``
    background: a 1.11:1 contrast ratio, with the survey grid BRIGHTER than
    the carriageway. Rendered, the seed contains no visible road at all, so
    an image-to-video model handed it has no surface to keep vehicles on and
    the whole frame reads as open ground. Asking the model in words not to
    drive off the road was already tried and failed (handover 4b, attempt
    #25); making the road unmistakable in the seed itself is the lever that
    is actually ours.
    """
    tl = _t_junction_timeline()
    scale = seed_scale_for(tl)
    i = impact_frame_index(tl)

    seed = render_frame(tl, i, scale=scale, annotate=False, seed_style=True)
    road = _patch_median(seed, _ON_CARRIAGEWAY, scale)
    verge = _patch_median(seed, _OFF_CARRIAGEWAY, scale)
    assert _contrast_ratio(road, verge) > 2.5, (
        f"carriageway {road} vs verge {verge} is only "
        f"{_contrast_ratio(road, verge):.2f}:1 -- the road is not visible "
        "enough in the seed to constrain anything")
    # The carriageway must be the BRIGHTER of the two: the drivable surface
    # is what the eye (and the model) should land on first.
    assert _relative_luminance(road) > _relative_luminance(verge)

    # And the same two points in the palette this replaced, which is the
    # measurement that justifies the change existing at all.
    blueprint = render_frame(tl, i, scale=scale, annotate=False)
    assert _contrast_ratio(_patch_median(blueprint, _ON_CARRIAGEWAY, scale),
                           _patch_median(blueprint, _OFF_CARRIAGEWAY, scale)) < 1.5


def test_seed_draws_a_bright_kerb_where_the_carriageway_ends():
    """A high-contrast surface still needs its boundary drawn, so "off the
    carriageway" is a hard visual edge rather than a gradient.

    The kerb is derived from the drivable surface's own outline rather than
    enumerated per layout (see ``_draw_seed_kerbs``), which is why it can
    wrap a T-junction's corners without drawing a wall across its mouth.
    """
    tl = _t_junction_timeline()
    scale = seed_scale_for(tl)
    i = impact_frame_index(tl)
    seed = _open_frame(render_frame(tl, i, scale=scale, annotate=False,
                                    seed_style=True))
    blueprint = _open_frame(render_frame(tl, i, scale=scale, annotate=False))

    # Scan the column through _ON_CARRIAGEWAY, across the road's north edge.
    col_x = int(world_to_pixel(*_ON_CARRIAGEWAY, scale=scale)[0])
    edge_y = int(world_to_pixel(0.0, tl.junction_half_extent_m, scale=scale)[1])
    band = range(edge_y - 6, edge_y + 7)
    assert max(_relative_luminance(seed.getpixel((col_x, y))) for y in band) > 0.6
    # Nothing like it in the palette this replaced.
    assert max(_relative_luminance(blueprint.getpixel((col_x, y))) for y in band) < 0.6


def test_seed_style_is_opt_in_and_never_reaches_a_sealed_artifact():
    """``seed_style`` defaults off, and the sealed artifacts never ask for it.

    ``hero_png``, the animation frames and the SVG are what a case seals and
    re-verifies against. A change made for the generative layer's benefit
    must not move a single byte of them.
    """
    tl = _t_junction_timeline()
    i = impact_frame_index(tl)
    assert render_frame(tl, i, title="t") == render_frame(tl, i, title="t",
                                                          seed_style=False)
    assert render_frame(tl, i, annotate=False) != render_frame(
        tl, i, annotate=False, seed_style=True)

    art = PillowSchematicRenderer(animate=False).render(tl, title="t")
    assert art.hero_png == render_frame(tl, i, title="t", scale=8.0)
    assert all(f == render_frame(tl, n, title="t", scale=8.0)
               for n, f in enumerate(art.frames_png))
    assert art.static_svg == build_static_svg(tl, title="t", scale=8.0)
    # The seed, and only the seed, is the styled one.
    assert art.seed_png == render_frame(tl, i, scale=art.seed_scale,
                                        annotate=False, seed_style=True)
