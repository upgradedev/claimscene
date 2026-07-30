"""Blueprint-style schematic renderer: keyframe timeline → SVG + PNG + MP4.

The factual layer. Everything drawn here is derived from the deterministic
Timeline (never from the generative model), rendered in a blueprint
aesthetic: dark steel background, survey grid, white/amber line work,
vehicle glyphs with damage-zone markers at their clock positions, and the
"ILLUSTRATION — NOT EVIDENCE" watermark on every frame.

Rendering strategy (Windows + Linux CI clean): the static diagram is exported
as a hand-built deterministic SVG string; animation frames are drawn directly
with Pillow primitives (no cairo/native DLLs); the MP4 is encoded by an
ffmpeg subprocess when ffmpeg is on PATH, else skipped (``animation = None``).
"""
from __future__ import annotations

import io
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .layout import TimedPose, Timeline, clock_point_world
from .provenance import WATERMARK
from .scene import RoadLayout, Signal, VehicleColor

# ── palette (blueprint aesthetic) ────────────────────────────────────────────
BG = "#0e1a22"
GRID = "#17303d"
ROAD_FILL = "#16242e"
ROAD_EDGE = "#4a6b80"
LANE_DASH = "#3a5a70"
CENTER_LINE = "#d9a441"
TEXT = "#cfe3ee"
TEXT_DIM = "#5f7d8f"
WATERMARK_COLOR = "#33505f"
CONTACT = "#ffcc00"
TRAIL_OPACITY = 0.45

VEHICLE_FILL: dict[VehicleColor, str] = {
    VehicleColor.white: "#e8e8e8",
    VehicleColor.black: "#33343a",
    VehicleColor.silver: "#b8bcc2",
    VehicleColor.gray: "#7c8288",
    VehicleColor.red: "#d1495b",
    VehicleColor.blue: "#3d7dd8",
    VehicleColor.green: "#4c9f70",
    VehicleColor.yellow: "#e3b23c",
}
SEVERITY_FILL = {"scratch": "#ffd166", "dent": "#f4845f", "crush": "#ef476f"}

_ROAD_EXTENT = 60.0  # how far the road bands extend from the origin (m)


def _xml_escape(text: str) -> str:
    """Escape XML *text content* — only ``&``, ``<``, ``>`` (apostrophes and
    quotes are legal in element text and are left untouched, which keeps the
    committed golden byte-identical). This closes the one injection vector in
    the otherwise closed-vocabulary schematic: the two free-string fields the
    caller controls — the case-id title and vehicle ids — reach ``<text>``
    nodes, and the ``/cases/preview-schematic`` endpoint returns this SVG for a
    client-supplied SceneGraph.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class SchematicArtifacts:
    static_svg: str
    hero_png: bytes
    frames_png: list[bytes] = field(default_factory=list)
    animation_mp4: bytes | None = None
    fps: int = 8
    #: The impact-frame render with every drawn TEXT omitted (header,
    #: per-vehicle id labels, timestamp, phase caption, both watermark
    #: captions) -- geometry only. This is what feeds the illustration clip
    #: as its seed image (see pipeline.py step 5): the deterministic
    #: schematic raster, not a generative still, and never itself sealed as
    #: a "schematic" artifact (``hero_png`` remains the one that is, fully
    #: annotated and watermarked, unchanged). Empty bytes when there are no
    #: vehicle tracks to render (mirrors ``hero_png``'s own empty-case).
    seed_png: bytes = b""

    @property
    def frame_count(self) -> int:
        return len(self.frames_png)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# ── shared drawing primitives (world meters; consumed by SVG and PNG) ────────
def _road_primitives(timeline: Timeline) -> list[dict]:
    """Road template as primitive shapes, in world coordinates."""
    j = timeline.junction_half_extent_m
    e = _ROAD_EXTENT
    layout = timeline.road.layout
    prims: list[dict] = []

    def band_v(half: float) -> dict:
        return {"kind": "rect", "x0": -half, "y0": -e, "x1": half, "y1": e, "fill": ROAD_FILL}

    def band_h(half: float) -> dict:
        return {"kind": "rect", "x0": -e, "y0": -half, "x1": e, "y1": half, "fill": ROAD_FILL}

    def center_v() -> dict:
        return {"kind": "line", "x0": 0, "y0": -e, "x1": 0, "y1": e,
                "stroke": CENTER_LINE, "width": 1.5, "dash": (6, 4)}

    def center_h() -> dict:
        return {"kind": "line", "x0": -e, "y0": 0, "x1": e, "y1": 0,
                "stroke": CENTER_LINE, "width": 1.5, "dash": (6, 4)}

    if layout is RoadLayout.straight:
        prims += [band_v(j), center_v()]
        prims += [{"kind": "line", "x0": s * j, "y0": -e, "x1": s * j, "y1": e,
                   "stroke": ROAD_EDGE, "width": 2, "dash": None} for s in (-1, 1)]
    elif layout is RoadLayout.x_intersection:
        prims += [band_v(j), band_h(j), center_v(), center_h()]
    elif layout is RoadLayout.t_intersection:
        prims += [band_h(j), center_h()]
        prims += [{"kind": "rect", "x0": -j, "y0": -e, "x1": j, "y1": 0, "fill": ROAD_FILL}]
        prims += [{"kind": "line", "x0": 0, "y0": -e, "x1": 0, "y1": -j,
                   "stroke": CENTER_LINE, "width": 1.5, "dash": (6, 4)}]
    elif layout is RoadLayout.roundabout:
        ring_outer = j + 2 * 1.75
        prims += [band_v(1.75 * 2), band_h(1.75 * 2)]
        prims += [{"kind": "circle", "cx": 0, "cy": 0, "r": ring_outer,
                   "fill": ROAD_FILL, "stroke": ROAD_EDGE, "width": 2}]
        prims += [{"kind": "circle", "cx": 0, "cy": 0, "r": j * 0.75,
                   "fill": BG, "stroke": CENTER_LINE, "width": 1.5}]
    elif layout is RoadLayout.parking_lot:
        prims += [{"kind": "rect", "x0": -j - 12, "y0": -e * 0.6, "x1": j + 12,
                   "y1": e * 0.6, "fill": ROAD_FILL}]
        for i in range(-5, 6):
            y = i * 5.5
            for sx in (-1, 1):
                prims += [{"kind": "line", "x0": sx * j, "y0": y, "x1": sx * (j + 10),
                           "y1": y, "stroke": LANE_DASH, "width": 1, "dash": None}]

    prims += _signal_primitives(timeline, j)
    return prims


def _signal_primitives(timeline: Timeline, j: float) -> list[dict]:
    sig = timeline.road.signal
    x, y = j + 2.0, j + 2.0
    if sig is Signal.stop_sign:
        return [{"kind": "circle", "cx": x, "cy": y, "r": 1.2, "fill": "#c0392b",
                 "stroke": "#e8e8e8", "width": 1.5}]
    if sig is Signal.traffic_light:
        return [
            {"kind": "circle", "cx": x, "cy": y + 1.2, "r": 0.6, "fill": "#d1495b",
             "stroke": ROAD_EDGE, "width": 1},
            {"kind": "circle", "cx": x, "cy": y, "r": 0.6, "fill": "#e3b23c",
             "stroke": ROAD_EDGE, "width": 1},
            {"kind": "circle", "cx": x, "cy": y - 1.2, "r": 0.6, "fill": "#4c9f70",
             "stroke": ROAD_EDGE, "width": 1},
        ]
    if sig is Signal.yield_sign:
        return [{"kind": "poly", "points": [(x - 1.2, y + 1.0), (x + 1.2, y + 1.0),
                                            (x, y - 1.0)],
                 "fill": "#e3b23c"}]
    return []


def _vehicle_corners(pose: TimedPose, length_m: float, width_m: float
                     ) -> list[tuple[float, float]]:
    h = math.radians(pose.heading_deg)
    c, s = math.cos(h), math.sin(h)
    hl, hw = length_m / 2.0, width_m / 2.0
    corners = [(hl, hw), (hl, -hw), (-hl, -hw), (-hl, hw)]
    return [(pose.x + cx * c - cy * s, pose.y + cx * s + cy * c) for cx, cy in corners]


def _vehicle_nose(pose: TimedPose, length_m: float, width_m: float
                  ) -> list[tuple[float, float]]:
    h = math.radians(pose.heading_deg)
    c, s = math.cos(h), math.sin(h)
    hl, hw = length_m / 2.0, width_m / 2.0
    pts = [(hl, hw * 0.7), (hl + 0.9, 0.0), (hl, -hw * 0.7)]
    return [(pose.x + px * c - py * s, pose.y + px * s + py * c) for px, py in pts]


def _burst(cx: float, cy: float, r_out: float = 1.4, r_in: float = 0.55,
           points: int = 8) -> list[tuple[float, float]]:
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / points
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _pose_at_index(timeline: Timeline, vehicle_id: str, index: int) -> TimedPose:
    track = next(t for t in timeline.tracks if t.vehicle_id == vehicle_id)
    return track.poses[index]


def impact_frame_index(timeline: Timeline) -> int:
    times = [p.t for p in timeline.tracks[0].poses]
    return min(range(len(times)), key=lambda i: abs(times[i] - timeline.impact_time_s))


# ── SVG static diagram ───────────────────────────────────────────────────────
class _View:
    def __init__(self, width: int, height: int, scale: float) -> None:
        self.w, self.h, self.scale = width, height, scale

    def px(self, x: float, y: float) -> tuple[float, float]:
        return (self.w / 2.0 + x * self.scale, self.h / 2.0 - y * self.scale)

    def fmt(self, x: float, y: float) -> str:
        sx, sy = self.px(x, y)
        return f"{sx:.1f},{sy:.1f}"


def _svg_prim(prim: dict, view: _View) -> str:
    k = prim["kind"]
    if k == "rect":
        x0, y0 = view.px(prim["x0"], prim["y1"])
        x1, y1 = view.px(prim["x1"], prim["y0"])
        return (f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" '
                f'height="{y1 - y0:.1f}" fill="{prim["fill"]}"/>')
    if k == "line":
        x0, y0 = view.px(prim["x0"], prim["y0"])
        x1, y1 = view.px(prim["x1"], prim["y1"])
        dash = f' stroke-dasharray="{prim["dash"][0]} {prim["dash"][1]}"' if prim["dash"] else ""
        return (f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
                f'stroke="{prim["stroke"]}" stroke-width="{prim["width"]}"{dash}/>')
    if k == "circle":
        cx, cy = view.px(prim["cx"], prim["cy"])
        stroke = prim.get("stroke")
        stroke_attr = (f' stroke="{stroke}" stroke-width="{prim.get("width", 1)}"'
                       if stroke else "")
        return (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{prim["r"] * view.scale:.1f}" '
                f'fill="{prim["fill"]}"{stroke_attr}/>')
    if k == "poly":
        pts = " ".join(view.fmt(x, y) for x, y in prim["points"])
        return f'<polygon points="{pts}" fill="{prim["fill"]}"/>'
    raise AssertionError(f"unknown primitive {k}")  # pragma: no cover


def _svg_vehicle(timeline: Timeline, vid: str, index: int, view: _View,
                 opacity: float, with_label: bool) -> str:
    meta = next(v for v in timeline.vehicles if v.id == vid)
    pose = _pose_at_index(timeline, vid, index)
    fill = VEHICLE_FILL[meta.color]
    corners = " ".join(view.fmt(x, y) for x, y in
                       _vehicle_corners(pose, meta.length_m, meta.width_m))
    nose = " ".join(view.fmt(x, y) for x, y in
                    _vehicle_nose(pose, meta.length_m, meta.width_m))
    parts = [f'<g opacity="{opacity}">',
             f'<polygon points="{corners}" fill="{fill}" stroke="{BG}" stroke-width="1"/>',
             f'<polygon points="{nose}" fill="{TEXT}"/>']
    if with_label:
        lx, ly = view.px(pose.x, pose.y)
        parts.append(f'<text x="{lx:.1f}" y="{ly - 14:.1f}" fill="{TEXT}" font-size="12" '
                     f'text-anchor="middle">{_xml_escape(vid)}</text>')
        for dz in meta.damage:
            dx, dy = clock_point_world(pose, dz.clock_position, meta.length_m, meta.width_m)
            px, py = view.px(dx, dy)
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" '
                         f'fill="{SEVERITY_FILL[dz.severity.value]}" stroke="{BG}" '
                         f'stroke-width="1"/>')
    parts.append("</g>")
    return "".join(parts)


def build_static_svg(timeline: Timeline, *, title: str | None = None,
                     width: int = 960, height: int = 720, scale: float = 8.0) -> str:
    view = _View(width, height, scale)
    impact_i = impact_frame_index(timeline)
    last_i = len(timeline.tracks[0].poses) - 1

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="Menlo, Consolas, monospace">'
    )
    out.append(f'<rect width="{width}" height="{height}" fill="{BG}"/>')
    # Survey grid every 5 m.
    step = 5.0 * scale
    x = width / 2.0 % step
    while x < width:
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        x += step
    y = height / 2.0 % step
    while y < height:
        out.append(f'<line x1="0" y1="{y:.1f}" x2="{width}" y2="{y:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        y += step

    for prim in _road_primitives(timeline):
        out.append(_svg_prim(prim, view))

    # Motion trails.
    for track in timeline.tracks:
        meta = next(v for v in timeline.vehicles if v.id == track.vehicle_id)
        pts = " ".join(view.fmt(p.x, p.y) for p in track.poses)
        out.append(f'<polyline points="{pts}" fill="none" '
                   f'stroke="{VEHICLE_FILL[meta.color]}" stroke-width="1.5" '
                   f'opacity="{TRAIL_OPACITY}" stroke-dasharray="3 3"/>')

    # Vehicle poses: start ghost, impact solid (with damage), rest mid.
    for v in timeline.vehicles:
        out.append(_svg_vehicle(timeline, v.id, 0, view, 0.30, False))
        out.append(_svg_vehicle(timeline, v.id, last_i, view, 0.55, False))
        out.append(_svg_vehicle(timeline, v.id, impact_i, view, 1.0, True))

    if timeline.contact_point is not None:
        pts = " ".join(view.fmt(x, y) for x, y in _burst(*timeline.contact_point))
        out.append(f'<polygon points="{pts}" fill="{CONTACT}" opacity="0.9"/>')

    # Header, legend, events, watermark.
    heading = title or "TOP-DOWN SCHEMATIC"
    out.append(f'<text x="{width / 2:.1f}" y="28" fill="{TEXT}" font-size="16" '
               f'text-anchor="middle">CLAIMSCENE · {_xml_escape(heading)} · '
               f'FACTUAL LAYER</text>')
    road = timeline.road
    legend = [f'{road.layout.value} · {road.lanes_per_direction} lane(s)/dir · '
              f'signal: {road.signal.value}']
    for v in timeline.vehicles:
        struck = (f' · struck at {v.impact_clock} o\'clock'
                  if v.impact_clock is not None else "")
        legend.append(f'{_xml_escape(v.id)} · {v.color.value} {v.kind.value}{struck}')
    for i, line in enumerate(legend):
        out.append(f'<text x="16" y="{52 + i * 16}" fill="{TEXT_DIM}" '
                   f'font-size="12">{line}</text>')
    for i, ev in enumerate(timeline.events):
        out.append(f'<text x="16" y="{height - 70 + i * 14:.1f}" fill="{TEXT_DIM}" '
                   f'font-size="11">t={ev.t:.2f}s {_xml_escape(ev.label)}</text>')
    out.append(f'<text x="{width / 2:.1f}" y="{height / 2 + 6:.1f}" '
               f'fill="{WATERMARK_COLOR}" font-size="34" text-anchor="middle" '
               f'opacity="0.5">{WATERMARK}</text>')
    out.append(f'<text x="{width / 2:.1f}" y="{height - 16}" fill="{TEXT_DIM}" '
               f'font-size="13" text-anchor="middle">{WATERMARK}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ── PNG frames (Pillow) + MP4 (ffmpeg subprocess) ────────────────────────────
@lru_cache(maxsize=8)
def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1 fallback
        return ImageFont.load_default()


def _hex_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _draw_prim(draw: ImageDraw.ImageDraw, prim: dict, view: _View) -> None:
    k = prim["kind"]
    if k == "rect":
        x0, y0 = view.px(prim["x0"], prim["y1"])
        x1, y1 = view.px(prim["x1"], prim["y0"])
        draw.rectangle([x0, y0, x1, y1], fill=_hex_rgb(prim["fill"]))
    elif k == "line":
        x0, y0 = view.px(prim["x0"], prim["y0"])
        x1, y1 = view.px(prim["x1"], prim["y1"])
        color = _hex_rgb(prim["stroke"])
        w = max(1, int(prim["width"]))
        if prim["dash"]:
            _dashed_line(draw, (x0, y0), (x1, y1), color, w,
                         prim["dash"][0], prim["dash"][1])
        else:
            draw.line([x0, y0, x1, y1], fill=color, width=w)
    elif k == "circle":
        cx, cy = view.px(prim["cx"], prim["cy"])
        r = prim["r"] * view.scale
        outline = _hex_rgb(prim["stroke"]) if prim.get("stroke") else None
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_hex_rgb(prim["fill"]),
                     outline=outline, width=int(prim.get("width", 1)))
    elif k == "poly":
        pts = [view.px(x, y) for x, y in prim["points"]]
        draw.polygon(pts, fill=_hex_rgb(prim["fill"]))


def _dashed_line(draw, a, b, color, width, on, off) -> None:
    ax, ay = a
    bx, by = b
    dist = math.hypot(bx - ax, by - ay)
    if dist < 1e-9:
        return
    ux, uy = (bx - ax) / dist, (by - ay) / dist
    s = 0.0
    while s < dist:
        e = min(s + on, dist)
        draw.line([ax + ux * s, ay + uy * s, ax + ux * e, ay + uy * e],
                  fill=color, width=width)
        s = e + off


def render_frame(timeline: Timeline, index: int, *, title: str | None = None,
                 width: int = 960, height: int = 720, scale: float = 8.0,
                 annotate: bool = True) -> bytes:
    """Render one frame of the schematic animation (world geometry -> PNG).

    ``annotate=True`` (the default, byte-identical to every call site that
    predates this parameter) draws the full frame: the header, the
    per-vehicle id labels, the elapsed-time stamp, the phase caption, and
    both "ILLUSTRATION -- NOT EVIDENCE" watermark captions, on top of the
    geometry. ``annotate=False`` renders ONLY the geometry -- road, motion
    trail, vehicle bodies, damage-zone markers, the contact burst -- with
    every piece of drawn TEXT omitted.

    ``PillowSchematicRenderer`` uses ``annotate=False`` for exactly one
    purpose: ``SchematicArtifacts.seed_png``, the raster that seeds the
    illustration clip (see pipeline.py step 5). The clip's own prompt
    promises the model "no text, no labels, no captions, and no watermarks"
    on the seed image; a seed that itself carried text would both contradict
    that promise and risk the model garbling inherited text while animating.
    Every SEALED schematic artifact (the hero PNG, every animation frame)
    keeps ``annotate=True`` -- this parameter never changes their bytes.
    """
    view = _View(width, height, scale)
    img = Image.new("RGB", (width, height), _hex_rgb(BG))
    draw = ImageDraw.Draw(img)

    step = 5.0 * scale
    x = width / 2.0 % step
    while x < width:
        draw.line([x, 0, x, height], fill=_hex_rgb(GRID), width=1)
        x += step
    y = height / 2.0 % step
    while y < height:
        draw.line([0, y, width, y], fill=_hex_rgb(GRID), width=1)
        y += step

    for prim in _road_primitives(timeline):
        _draw_prim(draw, prim, view)

    t = timeline.tracks[0].poses[index].t
    impact_i = impact_frame_index(timeline)

    for track in timeline.tracks:
        meta = next(v for v in timeline.vehicles if v.id == track.vehicle_id)
        trail = _hex_rgb(LANE_DASH)
        pts = [view.px(p.x, p.y) for p in track.poses[: index + 1]]
        if len(pts) >= 2:
            draw.line(pts, fill=trail, width=2)
        pose = track.poses[index]
        corners = [view.px(cx, cy) for cx, cy in
                   _vehicle_corners(pose, meta.length_m, meta.width_m)]
        draw.polygon(corners, fill=_hex_rgb(VEHICLE_FILL[meta.color]),
                     outline=_hex_rgb(BG))
        nose = [view.px(nx, ny) for nx, ny in
                _vehicle_nose(pose, meta.length_m, meta.width_m)]
        draw.polygon(nose, fill=_hex_rgb(TEXT))
        if annotate:
            lx, ly = view.px(pose.x, pose.y)
            draw.text((lx, ly - 22), track.vehicle_id, fill=_hex_rgb(TEXT),
                      font=_font(13), anchor="ms")
        if index >= impact_i:
            for dz in meta.damage:
                dx, dy = clock_point_world(pose, dz.clock_position,
                                           meta.length_m, meta.width_m)
                px, py = view.px(dx, dy)
                draw.ellipse([px - 4, py - 4, px + 4, py + 4],
                             fill=_hex_rgb(SEVERITY_FILL[dz.severity.value]),
                             outline=_hex_rgb(BG))

    if timeline.contact_point is not None and index >= impact_i:
        pts = [view.px(x, y) for x, y in _burst(*timeline.contact_point)]
        draw.polygon(pts, fill=_hex_rgb(CONTACT))

    if annotate:
        heading = title or "TOP-DOWN SCHEMATIC"
        draw.text((width / 2, 22), f"CLAIMSCENE · {heading} · FACTUAL LAYER",
                  fill=_hex_rgb(TEXT), font=_font(15), anchor="mm")
        draw.text((width - 16, 22), f"t=+{t:05.2f}s", fill=_hex_rgb(TEXT),
                  font=_font(14), anchor="rm")
        active = [e.label for e in timeline.events if e.t <= t + 1e-9]
        if active:
            draw.text((16, height - 40), f"phase: {active[-1]}",
                      fill=_hex_rgb(TEXT_DIM), font=_font(13), anchor="lm")
        draw.text((width / 2, height / 2), WATERMARK, fill=_hex_rgb(WATERMARK_COLOR),
                  font=_font(30), anchor="mm")
        draw.text((width / 2, height - 16), WATERMARK, fill=_hex_rgb(TEXT_DIM),
                  font=_font(13), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def encode_mp4(frames_png: list[bytes], *, fps: int = 8) -> bytes | None:
    """Encode PNG frames into an MP4 via ffmpeg; None when ffmpeg is absent."""
    if not frames_png or not ffmpeg_available():
        return None
    with tempfile.TemporaryDirectory(prefix="claimscene-anim-") as tmp:
        tmp_path = Path(tmp)
        for i, frame in enumerate(frames_png):
            (tmp_path / f"frame_{i:04d}.png").write_bytes(frame)
        out_path = tmp_path / "schematic.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
               "-i", str(tmp_path / "frame_%04d.png"), "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return None
        return out_path.read_bytes()


class PillowSchematicRenderer:
    """Default Renderer implementation (see ports.Renderer)."""

    name = "blueprint-pillow"

    def __init__(self, *, width: int = 960, height: int = 720, scale: float = 8.0,
                 fps: int = 8, animate: bool = True) -> None:
        self.width, self.height, self.scale = width, height, scale
        self.fps = fps
        self.animate = animate

    def render(self, timeline: Timeline, *, title: str | None = None) -> SchematicArtifacts:
        svg = build_static_svg(timeline, title=title, width=self.width,
                               height=self.height, scale=self.scale)
        n = len(timeline.tracks[0].poses) if timeline.tracks else 0
        frames = [render_frame(timeline, i, title=title, width=self.width,
                               height=self.height, scale=self.scale)
                  for i in range(n)]
        if frames:
            impact_i = impact_frame_index(timeline)
            hero = frames[impact_i]
            # ``title`` is irrelevant here: annotate=False never draws it.
            seed = render_frame(timeline, impact_i, width=self.width,
                                height=self.height, scale=self.scale,
                                annotate=False)
        else:
            hero = b""
            seed = b""
        animation = encode_mp4(frames, fps=self.fps) if self.animate else None
        return SchematicArtifacts(static_svg=svg, hero_png=hero, frames_png=frames,
                                  animation_mp4=animation, fps=self.fps, seed_png=seed)
