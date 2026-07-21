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
