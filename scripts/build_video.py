#!/usr/bin/env python3
"""Build the ClaimScene narrated demo — per-beat, audio-locked.

The demo is assembled BEAT BY BEAT so the spoken word and the on-screen move
never drift:

  1. Each beat (``demo/claimscene-demo.beats.json``) carries one narration line
     and a Ken Burns focus move across one committed forensic-blueprint card in
     ``demo/assets/``.
  2. The line is synthesized once with ElevenLabs (a professional narrator,
     the same voice for the whole video). There is NO silent fallback: if the
     ElevenLabs call fails, the build stops loudly.
  3. The clip is decoded to WAV, its real duration measured, and the beat's
     visual duration is snapped to a whole number of frames of that measurement
     plus a fixed tail. Audio and video for the beat are built from the SAME
     frame-quantized number, so there is zero cumulative drift.
  4. Each beat is muxed into its own mp4 (frames piped raw to ffmpeg alongside
     the padded audio). The per-beat mp4s are concatenated into the final
     H.264 video. Final duration == sum(beat durations), within one frame.

Dependencies: Pillow (already a project dependency) and an ``ffmpeg`` /
``ffprobe`` on PATH. The ElevenLabs HTTP call uses only the standard library.

Env / flags:
  ELEVENLABS_API_KEY   ElevenLabs key (User-scope env var is auto-read on
                       Windows if the process env is unset). Never printed.
  --beats / --assets / --out / --srt / --workdir   path overrides
  --voice-id / --model-id / --fps                  render overrides
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# Forensic-blueprint palette (matches the committed cards).
AMBER = (240, 168, 0)
CYAN = (86, 190, 214)
INK = (228, 234, 240)
BOX_FILL = (9, 13, 20, 216)
BOX_LINE = (40, 54, 70, 255)

MONO_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/Library/Fonts/Menlo.ttc",
)
MONO_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/consolab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
)


def run(cmd: list[str], *, stdin: bytes | None = None) -> bytes:
    """Run a subprocess, raising with captured stderr on a non-zero exit."""
    proc = subprocess.run(cmd, input=stdin, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write("CMD FAILED: " + " ".join(map(str, cmd)) + "\n")
        sys.stderr.write(proc.stderr.decode(errors="replace") + "\n")
        raise SystemExit(1)
    return proc.stdout


def probe_duration(path: str) -> float:
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path])
    return float(out.decode().strip())


def load_key() -> str:
    """Read the ElevenLabs key from the env, falling back to the Windows
    User-scope variable. The value is never printed."""
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key and os.name == "nt" and shutil.which("powershell.exe"):
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('ELEVENLABS_API_KEY','User')"],
            capture_output=True, text=True)
        key = out.stdout.strip()
    if not key:
        raise SystemExit(
            "ELEVENLABS_API_KEY is not set. Export it (or set the Windows "
            "User-scope variable) and retry. This build has no TTS fallback.")
    return key


def synth_elevenlabs(text: str, out_mp3: str, key: str, voice_id: str,
                     model_id: str, retries: int = 3) -> None:
    """Synthesize one narration line. Raises (no fallback) on failure."""
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
           "?output_format=mp3_44100_128")
    body = json.dumps({
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                           "style": 0.0, "use_speaker_boost": True},
    }).encode("utf-8")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "xi-api-key": key, "Content-Type": "application/json",
                "Accept": "audio/mpeg"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if len(data) < 3000:
                raise RuntimeError(f"suspiciously tiny audio ({len(data)} bytes)")
            with open(out_mp3, "wb") as handle:
                handle.write(data)
            return
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            raise SystemExit(
                f"ElevenLabs HTTP {exc.code} for beat audio: {detail}\n"
                "STOP: the key errored — no edge-tts fallback is used.") from exc
        except Exception as exc:  # noqa: BLE001 — transient transport, retry
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"ElevenLabs failed after {retries} attempts: {last}")


def load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_to_width(draw: ImageDraw.ImageDraw, text: str,
                  font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def caption_overlay(beat: dict, size: tuple[int, int], fonts: dict) -> Image.Image:
    """Pre-render the static caption card for a beat (kicker + text bar)."""
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    left, right = 64, width - 64
    box_h, margin = 172, 66
    bottom = height - margin
    top = bottom - box_h
    draw.rounded_rectangle([left, top, right, bottom], radius=14,
                           fill=BOX_FILL, outline=BOX_LINE, width=1)
    draw.rectangle([left + 2, top + 12, left + 7, bottom - 12], fill=AMBER)

    text_x = left + 30
    draw.text((text_x, top + 22), beat.get("kicker", ""), font=fonts["kicker"],
              fill=CYAN)
    lines = wrap_to_width(draw, beat["caption"], fonts["caption"],
                          right - text_x - 24)
    line_y = top + 60
    for line in lines[:2]:
        draw.text((text_x, line_y), line, font=fonts["caption"], fill=INK)
        line_y += 46
    return overlay


def lerp_rect(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[i] + (b[i] - a[i]) * t for i in range(4)]


def contain(card: Image.Image, box: tuple[float, float, float, float],
            size: tuple[int, int], bg: tuple[int, int, int],
            overlay: Image.Image) -> Image.Image:
    """Fit the source rect ``box`` inside ``size`` (letter/pillarbox), paste the
    caption.

    ``box`` is in source pixels and is deliberately NOT rounded to whole ones.
    Pillow's ``resize(..., box=...)`` takes a float source rect and resamples at
    exactly that sub-pixel phase, which is what makes a slow Ken Burns move
    glide. Cropping to integers first (the old path) quantized the move: a beat
    that pans 24 source pixels over 454 frames held one crop for 2-5 frames and
    then snapped a whole pixel, and because the left and right edges rounded
    independently the width oscillated by one pixel too, so the picture visibly
    trembled instead of drifting.
    """
    width, height = size
    x0, y0, x1, y1 = box
    src_w, src_h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    scale = min(width / src_w, height / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = card.resize((new_w, new_h), Image.LANCZOS, box=(x0, y0, x1, y1))
    frame = Image.new("RGB", size, bg)
    frame.paste(resized, ((width - new_w) // 2, (height - new_h) // 2))
    frame.paste(overlay, (0, 0), overlay)
    return frame


def render_beat(card: Image.Image, beat: dict, duration: float, fps: int,
                size: tuple[int, int], bg: tuple[int, int, int],
                overlay: Image.Image, out_mp4: str,
                t0: float, total: float) -> None:
    """Render one beat's frames (Ken Burns) piped into ffmpeg as a video-only
    H.264 clip. Audio is concatenated once, later, to avoid per-segment AAC
    priming drift, so this clip carries no audio stream."""
    width, height = size
    frames = max(1, round(duration * fps))
    start, end = beat["focus_start"], beat["focus_end"]
    cw, ch = card.width, card.height

    # veryfast keeps the encoder in step with the Python frame feeder so
    # ffmpeg never buffers gigabytes of raw input; capping x264 threads leaves
    # CPU for the (single-threaded) Pillow resize instead of starving it; CRF
    # 20 keeps the flat blueprint cards visually lossless.
    cmd = [FFMPEG, "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
           "-video_size", f"{width}x{height}", "-framerate", str(fps),
           "-thread_queue_size", "64", "-i", "pipe:0", "-an", "-map", "0:v:0",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-threads", "2", "-pix_fmt", "yuv420p", out_mp4]
    # ffmpeg's stderr is drained to a file (never a PIPE): if it were an
    # un-read PIPE, ffmpeg would block once its progress output filled the OS
    # buffer, stop reading stdin, and deadlock against our frame writes.
    err_log = out_mp4 + ".ffmpeg.log"
    with open(err_log, "wb") as err_handle:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=err_handle)
        assert proc.stdin is not None
        try:
            for f_idx in range(frames):
                t = f_idx / max(1, frames - 1)
                fx, fy, fw, fh = lerp_rect(start, end, t)
                # Kept in floats end to end (see contain): rounding here is what
                # made the move judder.
                box = (max(0.0, fx * cw), max(0.0, fy * ch),
                       min(float(cw), (fx + fw) * cw),
                       min(float(ch), (fy + fh) * ch))
                frame = contain(card, box, size, bg, overlay)
                prog = (t0 + (f_idx / fps)) / total
                draw = ImageDraw.Draw(frame)
                draw.rectangle([0, height - 4, width, height], fill=(24, 32, 42))
                draw.rectangle([0, height - 4, round(width * prog), height], fill=AMBER)
                proc.stdin.write(frame.tobytes())
        finally:
            proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        with open(err_log, encoding="utf-8", errors="replace") as fh:
            sys.stderr.write(fh.read())
        raise SystemExit(f"ffmpeg failed rendering beat {beat['id']}")


def concat_copy(paths: list[str], list_path: str, out: str) -> None:
    """Stream-concatenate same-codec parts with ``-c copy`` (no re-encode)."""
    with open(list_path, "w", encoding="utf-8") as handle:
        for path in paths:
            handle.write(f"file '{path.replace(os.sep, '/')}'\n")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", out])


def srt_ts(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(beats: list[dict], durations: list[float], path: str) -> None:
    cues, start = [], 0.0
    for idx, (beat, dur) in enumerate(zip(beats, durations, strict=True), start=1):
        end = start + dur
        # break_on_hyphens=False keeps "top-down" / "SHA-256" whole, so the
        # subtitle text re-joins to the exact narration (check_video asserts it).
        text = "\n".join(textwrap.wrap(" ".join(beat["narration"].split()),
                                       width=64, break_long_words=False,
                                       break_on_hyphens=False))
        cues.append(f"{idx}\n{srt_ts(start)} --> {srt_ts(end)}\n{text}\n")
        start = end
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(cues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beats", default=os.path.join(HERE, "demo",
                                                        "claimscene-demo.beats.json"))
    parser.add_argument("--assets", default=os.path.join(HERE, "demo", "assets"))
    parser.add_argument("--out", default=os.path.join(HERE, "demo",
                                                      "claimscene-demo.mp4"))
    parser.add_argument("--srt", default=os.path.join(HERE, "demo",
                                                      "claimscene-demo.en.srt"))
    parser.add_argument("--windows", default=os.path.join(HERE, "demo",
                                                          "claimscene-demo.windows.json"))
    parser.add_argument("--workdir", default=os.path.join(HERE, ".artifacts",
                                                          "video-build"))
    parser.add_argument("--voice-id", default=None)
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--fps", type=int, default=None)
    args = parser.parse_args()

    with open(args.beats, encoding="utf-8") as handle:
        spec = json.load(handle)
    beats = spec["beats"]
    render = spec.get("render", {})
    fps = args.fps or int(render.get("fps", 30))
    width = int(render.get("width", 1920))
    height = int(render.get("height", 1080))
    tail = float(render.get("tail_seconds", 0.35))
    size = (width, height)
    voice = spec.get("voice", {})
    voice_id = args.voice_id or voice.get("voice_id")
    model_id = args.model_id or voice.get("model_id", "eleven_multilingual_v2")

    key = load_key()
    os.makedirs(args.workdir, exist_ok=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    fonts = {
        "kicker": load_font(MONO_BOLD_CANDIDATES, 21),
        "caption": load_font(MONO_CANDIDATES, 34),
    }
    cards: dict[str, Image.Image] = {}
    backgrounds: dict[str, tuple[int, int, int]] = {}
    for beat in beats:
        name = beat["card"]
        if name not in cards:
            img = Image.open(os.path.join(args.assets, name)).convert("RGB")
            cards[name] = img
            backgrounds[name] = img.getpixel((3, 3))

    # 1. Synthesize + measure every beat (cached by narration hash) → durations.
    durations: list[float] = []
    pads: list[str] = []
    for beat in beats:
        digest = hashlib.sha256(
            (beat["narration"] + voice_id + model_id).encode()).hexdigest()[:16]
        mp3 = os.path.join(args.workdir, f"{beat['id']}.{digest}.mp3")
        if not (os.path.exists(mp3) and os.path.getsize(mp3) > 3000):
            synth_elevenlabs(beat["narration"], mp3, key, voice_id, model_id)
            print(f"[tts] {beat['id']:<14} synthesized")
        else:
            print(f"[tts] {beat['id']:<14} cached")
        wav = os.path.join(args.workdir, f"{beat['id']}.wav")
        run([FFMPEG, "-y", "-i", mp3, "-ac", "1", "-ar", "44100", "-f", "wav", wav])
        measured = probe_duration(wav)
        n_frames = max(1, round((measured + tail) * fps))
        dur = n_frames / fps
        pad = os.path.join(args.workdir, f"{beat['id']}.pad.wav")
        run([FFMPEG, "-y", "-i", wav, "-af", "apad", "-t", f"{dur:.6f}",
             "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", "-f", "wav", pad])
        durations.append(dur)
        pads.append(pad)
        print(f"[audio] {beat['id']:<14} narration={measured:6.3f}s scene={dur:6.3f}s")

    total = sum(durations)

    # 2. Render each beat as a video-only H.264 clip (no per-beat audio).
    beat_mp4s: list[str] = []
    t0 = 0.0
    for beat, dur in zip(beats, durations, strict=True):
        overlay = caption_overlay(beat, size, fonts)
        out_mp4 = os.path.join(args.workdir, f"{beat['id']}.mp4")
        render_beat(cards[beat["card"]], beat, dur, fps, size,
                    backgrounds[beat["card"]], overlay, out_mp4, t0, total)
        beat_mp4s.append(out_mp4)
        t0 += dur
        print(f"[video] {beat['id']:<14} {dur:6.3f}s")

    # 3. Concat the video (copy) and the PCM audio (copy) separately, then mux
    #    once — AAC is encoded a single time over the whole track, so there is
    #    no per-segment priming drift between audio and video.
    scenes = os.path.join(args.workdir, "scenes.mp4")
    voice_wav = os.path.join(args.workdir, "voiceover.wav")
    concat_copy(beat_mp4s, os.path.join(args.workdir, "video_concat.txt"), scenes)
    concat_copy(pads, os.path.join(args.workdir, "audio_concat.txt"), voice_wav)
    run([FFMPEG, "-y", "-i", scenes, "-i", voice_wav, "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         args.out])

    write_srt(beats, durations, args.srt)
    with open(args.windows, "w", encoding="utf-8", newline="\n") as handle:
        pos, windows = 0.0, []
        for beat, dur in zip(beats, durations, strict=True):
            windows.append({"id": beat["id"], "section": beat["section"],
                            "start": round(pos, 3), "end": round(pos + dur, 3),
                            "dur": round(dur, 3)})
            pos += dur
        json.dump({"fps": fps, "total": round(total, 3), "windows": windows},
                  handle, indent=2)
        handle.write("\n")

    # 4. Global sync guard: video/audio/sum agree within one frame; under 3:00.
    vdur = probe_duration(args.out)
    frame = 1.0 / fps
    print(f"[guard] beats={len(beats)} sum={total:.3f}s final={vdur:.3f}s")
    if abs(vdur - total) > frame + 0.10:
        raise SystemExit(f"final {vdur:.3f}s != sum {total:.3f}s")
    if vdur >= 180.0:
        raise SystemExit(f"final {vdur:.3f}s exceeds the 180s (3:00) hard limit")
    chars = sum(len(b["narration"]) for b in beats)
    print(f"[ok] {args.out}  {vdur:.3f}s  {len(beats)} beats  "
          f"{chars} narration chars  voice={voice_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
