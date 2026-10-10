# -*- coding: utf-8 -*-
"""Builds the finished 9:16 video: nature clip + Persian text + brand + song."""
import math
import random
import re
import subprocess
from pathlib import Path

import arabic_reshaper

try:  # python-bidi 0.4.x
    from bidi.algorithm import get_display
except ImportError:  # newer python-bidi
    from bidi import get_display

from PIL import Image, ImageDraw, ImageFont

import config

W, H = 1080, 1920


class TooShort(Exception):
    """The song is too short to cut a clip from."""


# ---------------------------------------------------------------- text image
def find_font() -> Path:
    cands = sorted(
        list(config.FONT_DIR.glob("*.ttf")) + list(config.FONT_DIR.glob("*.otf"))
    )
    if not cands:
        raise RuntimeError(
            "فونت پیدا نشد. فایل Vazirmatn (.ttf) رو توی پوشه‌ی fonts بذار."
        )
    for key in ("medium", "regular"):
        for c in cands:
            if key in c.name.lower():
                return c
    return cands[0]


def shape(text: str) -> str:
    """Make Persian text drawable: join letters and fix right-to-left order."""
    return get_display(arabic_reshaper.reshape(text))


def _font(path, size):
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)


def wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if not cur or draw.textlength(shape(trial), font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def render_overlay(sentence: str, brand: str, out_path: Path):
    fp = find_font()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    max_w = int(W * 0.82)
    size = 72
    for size in range(72, 40, -4):
        font = _font(fp, size)
        lines = wrap(d, sentence, font, max_w)
        if len(lines) <= 4:
            break
    lh = int(size * 1.75)
    block_h = lh * len(lines)
    top = int(H * 0.60 - block_h / 2)

    # soft dark band behind the text so it stays readable on bright scenes
    y0, y1 = top - 160, top + block_h + 200
    for i in range(y1 - y0):
        a = int(150 * math.sin(math.pi * i / (y1 - y0)))
        d.line([(0, y0 + i), (W, y0 + i)], fill=(0, 0, 0, a))

    for i, line in enumerate(lines):
        s = shape(line)
        w = d.textlength(s, font=font)
        x, y = (W - w) / 2, top + i * lh
        d.text((x + 3, y + 3), s, font=font, fill=(0, 0, 0, 160))  # shadow
        d.text((x, y), s, font=font, fill=(255, 255, 255, 255))

    # brand (channel/page name) at the bottom with a thin line above it
    bf = _font(fp, 42)
    bw = d.textlength(brand, font=bf)
    by = H - 300
    d.line([(W / 2 - 70, by - 28), (W / 2 + 70, by - 28)], fill=(255, 255, 255, 170), width=3)
    d.text(((W - bw) / 2, by), brand, font=bf, fill=(255, 255, 255, 215))

    img.save(out_path)


# ---------------------------------------------------------------- audio helper
def first_sound_seconds(path) -> float:
    """If the file starts with silence, returns where the sound begins."""
    p = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-t", "60", "-i", str(path),
            "-af", "silencedetect=noise=-38dB:d=0.8", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=120,
    )
    start = re.search(r"silence_start: (-?[\d.]+)", p.stderr)
    end = re.search(r"silence_end: ([\d.]+)", p.stderr)
    if start and end and float(start.group(1)) <= 0.3:
        return float(end.group(1))
    return 0.0


# ---------------------------------------------------------------- main build
def render(video_path, audio_path, work: Path, sentence, vdur, adur, is_voice):
    """Returns (output_path, duration_seconds)."""
    snd = 0.0 if is_voice else first_sound_seconds(audio_path)
    astart = snd if is_voice else max(snd, config.AUDIO_START_DEFAULT)
    if adur - astart < config.MIN_OUT_SECONDS:
        astart = snd
    avail = adur - astart
    if avail < config.MIN_OUT_SECONDS:
        raise TooShort()

    dur = min(config.OUT_SECONDS, avail - 0.3, vdur - 0.5)
    dur = math.floor(dur * 10) / 10
    vstart = random.uniform(0, max(0.0, vdur - dur - 0.5))

    overlay = work / "overlay.png"
    render_overlay(sentence, config.BRAND, overlay)
    out = work / "out.mp4"

    fc = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,fps=30[bg];"
        f"[1:v]format=rgba,fade=t=in:st=0.6:d=1.2:alpha=1,"
        f"fade=t=out:st={max(dur - 1.2, 1):.2f}:d=1.0:alpha=1[ov];"
        "[bg][ov]overlay=0:0:format=auto,format=yuv420p[v];"
        f"[2:a]afade=t=in:st=0:d=1,afade=t=out:st={max(dur - 1.5, 1):.2f}:d=1.5[a]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{vstart:.2f}", "-t", f"{dur:.2f}", "-i", str(video_path),
        "-loop", "1", "-framerate", "30", "-t", f"{dur:.2f}", "-i", str(overlay),
        "-ss", f"{astart:.2f}", "-t", f"{dur:.2f}", "-i", str(audio_path),
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-maxrate", "6M", "-bufsize", "12M",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-t", f"{dur:.2f}", str(out),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + p.stderr[-600:])
    return out, dur
