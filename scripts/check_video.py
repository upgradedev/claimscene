#!/usr/bin/env python3
"""CI gate for the ClaimScene narrated demo.

Re-verifies the committed ``demo/claimscene-demo.mp4`` against its beat script
and subtitle sidecar so a desynced, malformed, over-length, or re-ordered demo
fails the build instead of shipping. Dependency-light: standard library plus an
``ffprobe`` on PATH (no Python packages).

Checks:
  1. Container duration < 180s (the hard 3:00 submission limit).
  2. Exactly one video stream and one audio stream.
  3. Audio and video stream durations agree within ~1 frame (A/V sync).
  4. Video is H.264 / yuv420p at 1920x1080 and ~30 fps.
  5. The SRT has one cue per beat, cues are ordered and start at 00:00:00,000,
     and each cue's text is the matching beat's narration (order == beats).
  6. The SRT's final cue ends at the video duration (within tolerance).
  7. The beat sections cover 1..7 in non-decreasing order (the required arc).

Exit code is non-zero on the first failing check.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
MAX_SECONDS = 180.0
EXPECTED_W, EXPECTED_H = 1920, 1080
EXPECTED_FPS = 30.0


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


def probe(path: str) -> dict:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_format", "-show_streams",
             "-of", "json", path],
            capture_output=True, check=True).stdout
    except FileNotFoundError:
        fail(f"ffprobe not found on PATH (set FFPROBE); cannot verify {path}")
    except subprocess.CalledProcessError as exc:
        fail(f"ffprobe failed on {path}: {exc.stderr.decode(errors='replace')[:200]}")
    return json.loads(out)


def parse_fps(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(value)


def parse_srt(text: str) -> list[dict]:
    stamp = re.compile(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        match = stamp.search(lines[1])
        if not match:
            continue
        g = [int(x) for x in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append({"start": start, "end": end,
                     "text": " ".join(lines[2:])})
    return cues


def norm(text: str) -> str:
    return " ".join(text.split())


def check_video(mp4: str, fps: float) -> tuple[float, float]:
    meta = probe(mp4)
    duration = float(meta.get("format", {}).get("duration", 0.0))
    if not (0 < duration < MAX_SECONDS):
        fail(f"duration {duration:.3f}s must be >0 and < {MAX_SECONDS:.0f}s")
    ok(f"duration {duration:.3f}s < {MAX_SECONDS:.0f}s")

    streams = meta.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        fail(f"expected exactly 1 video + 1 audio stream; got "
             f"{len(videos)} video / {len(audios)} audio")
    ok("exactly one video + one audio stream")

    video, audio = videos[0], audios[0]
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        fail(f"video must be h264/yuv420p; got "
             f"{video.get('codec_name')}/{video.get('pix_fmt')}")
    if (video.get("width"), video.get("height")) != (EXPECTED_W, EXPECTED_H):
        fail(f"video must be {EXPECTED_W}x{EXPECTED_H}; got "
             f"{video.get('width')}x{video.get('height')}")
    real_fps = parse_fps(video.get("avg_frame_rate", "0/0")) or \
        parse_fps(video.get("r_frame_rate", "0/0"))
    if abs(real_fps - EXPECTED_FPS) > 0.5:
        fail(f"video fps must be ~{EXPECTED_FPS}; got {real_fps:.3f}")
    ok(f"video h264/yuv420p {EXPECTED_W}x{EXPECTED_H} @ {real_fps:.2f} fps")

    v_dur = float(video.get("duration") or duration)
    a_dur = float(audio.get("duration") or duration)
    tol = (1.0 / fps) + 0.10
    if abs(v_dur - a_dur) > tol:
        fail(f"A/V desync: video {v_dur:.3f}s vs audio {a_dur:.3f}s "
             f"(tolerance {tol:.3f}s)")
    ok(f"A/V in sync: video {v_dur:.3f}s ~= audio {a_dur:.3f}s")
    return duration, fps


def check_srt(srt_path: str, beats: list[dict], duration: float,
              fps: float) -> None:
    with open(srt_path, encoding="utf-8") as handle:
        cues = parse_srt(handle.read())
    if len(cues) != len(beats):
        fail(f"SRT has {len(cues)} cues; expected {len(beats)} (one per beat)")
    ok(f"SRT cue count == beat count ({len(beats)})")

    if abs(cues[0]["start"]) > 0.001:
        fail(f"first SRT cue must start at 00:00:00,000; got {cues[0]['start']:.3f}s")
    prev = -1.0
    for idx, (cue, beat) in enumerate(zip(cues, beats, strict=True), start=1):
        if cue["start"] < prev - 0.001 or cue["end"] < cue["start"] - 0.001:
            fail(f"SRT cue {idx} is out of order")
        if norm(cue["text"]) != norm(beat["narration"]):
            fail(f"SRT cue {idx} text does not match beat '{beat['id']}' narration "
                 "(caption order drifted from the beat script)")
        prev = cue["start"]
    ok("SRT cues ordered and text matches each beat's narration in order")

    tol = (1.0 / fps) + 0.20
    if abs(cues[-1]["end"] - duration) > tol:
        fail(f"final SRT cue ends at {cues[-1]['end']:.3f}s; video is "
             f"{duration:.3f}s (tolerance {tol:.3f}s)")
    ok(f"final SRT cue ends at the video duration ({duration:.3f}s)")


def check_sections(beats: list[dict], declared: dict) -> None:
    sections = [int(b["section"]) for b in beats]
    for num in sections:
        if str(num) not in declared:
            fail(f"beat section {num} is not declared in the beats file")
    if sections != sorted(sections):
        fail(f"beat sections are not in non-decreasing order: {sections}")
    if sorted(set(sections)) != list(range(1, 8)):
        fail(f"beats must cover the 7 script sections 1..7; got {sorted(set(sections))}")
    ok(f"beat sections cover the 7-part arc in order: {sections}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mp4", default=os.path.join(HERE, "demo",
                                                      "claimscene-demo.mp4"))
    parser.add_argument("--beats", default=os.path.join(HERE, "demo",
                                                        "claimscene-demo.beats.json"))
    parser.add_argument("--srt", default=os.path.join(HERE, "demo",
                                                      "claimscene-demo.en.srt"))
    args = parser.parse_args()

    for path in (args.mp4, args.beats, args.srt):
        if not os.path.exists(path):
            fail(f"required artifact missing: {os.path.relpath(path, HERE)}")

    with open(args.beats, encoding="utf-8") as handle:
        spec = json.load(handle)
    beats = spec["beats"]
    fps = float(spec.get("render", {}).get("fps", EXPECTED_FPS))

    print(f"Verifying {os.path.relpath(args.mp4, HERE)} against "
          f"{len(beats)} beats ...")
    duration, fps = check_video(args.mp4, fps)
    check_srt(args.srt, beats, duration, fps)
    check_sections(beats, spec.get("sections", {}))
    print(f"\nPASS  demo video verified: {duration:.3f}s, {len(beats)} beats, "
          "A/V synced, captions ordered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
