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
)

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "schematic_static.svg"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def timeline():
    return LayoutEngine().build(_scene_left_cross())


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
    caption, see the PR that added this field)."""
    renderer = PillowSchematicRenderer(animate=False)
    art = renderer.render(timeline)
    assert art.seed_png.startswith(PNG_MAGIC)
    assert art.seed_png != art.hero_png
    assert art.seed_png == render_frame(
        timeline, impact_frame_index(timeline), annotate=False)


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
