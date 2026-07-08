"""Make a stock wildlife photo look like it came off a MeshCam leaf node.

The leaf is a Xiao ESP32-S3 with a QXGA (2048x1536) camera module: small
sensor, fixed focus, IR at night — and its real pipeline is
`detect -> crop-to-bbox -> downscale crop -> thumbnail (few KB)` with the
full frame kept on SD for deferred pulls. This module fakes all of that:

- `process()` — the sensor frame: 4:3 crop to QXGA, cheap-sensor tone,
  luma grain; night shots (ir=True) go monochrome with an IR hotspot.
- `jpeg_tier()` — encode the frame at `standard` (downscale) or `max`
  (the QXGA sensor original). Frames stay CLEAN — the backend burns the
  OSD info bar in at ingest (`trailcam.osd`), exactly like production.
- `thumb_jpeg()` — the detector crop: bbox out of the full frame
  (the detector runs on the raw framebuffer), downscaled hard and
  compressed to the few-KB budget a single LoRa Resource can carry.

Standalone preview:

    uv run --with pillow --project backend \
        python scripts/screenshots/trailcamify.py fixtures/red-fox.jpg /tmp/preview
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter

SENSOR_W, SENSOR_H = 2048, 1536  # QXGA 4:3

# Full-frame tiers: (width, starting JPEG quality, byte budget). Leaf firmware
# encodes to a byte budget (the transfer window is fixed airtime), stepping
# quality down until the frame fits — busy foliage compresses worse, so it
# just comes out crunchier. standard ~80-120 KB (a few minutes of airtime),
# max = the QXGA sensor original, ~300-450 KB (the long, deliberate pull).
TIERS = {
    "standard": (960, 50, 120 * 1024),
    "max": (2048, 75, 450 * 1024),
}

# Detector-crop thumbnail budget: a single LoRa Resource, ~10-40 s of airtime
# (~5-10 KB — the leaf's whole unprompted transmission for a trigger).
THUMB_MAX_DIM = 320
THUMB_QUALITY = 32
THUMB_BUDGET = 11 * 1024
_QUALITY_FLOOR = 22


def _encode_to_budget(im: Image.Image, quality: int, budget: int) -> bytes:
    """JPEG-encode, stepping quality down (then dimensions) until it fits."""
    while True:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= budget or (quality <= _QUALITY_FLOOR and max(im.size) <= 160):
            return buf.getvalue()
        if quality > _QUALITY_FLOOR:
            quality -= 6
        else:
            im = im.resize((round(im.width * 0.85), round(im.height * 0.85)), Image.LANCZOS)

def _crop_4x3(im: Image.Image) -> Image.Image:
    w, h = im.size
    if w / h > 4 / 3:
        new_w = round(h * 4 / 3)
        x = (w - new_w) // 2
        return im.crop((x, 0, x + new_w, h))
    new_h = round(w * 3 / 4)
    y = (h - new_h) // 2
    return im.crop((0, y, 0 + w, y + new_h))


def _noise_layer(size: tuple[int, int], rng: random.Random, strength: int) -> Image.Image:
    """Cheap deterministic luma noise: small random field scaled up so it has
    a coarse 'sensor grain' structure instead of per-pixel white noise."""
    w, h = size[0] // 4, size[1] // 4
    small = Image.frombytes(
        "L", (w, h), bytes(rng.randint(128 - strength, 128 + strength) for _ in range(w * h))
    )
    return small.resize(size, Image.BILINEAR)


def _add_noise(im: Image.Image, rng: random.Random, strength: int) -> Image.Image:
    """Add zero-centered grain (blend() would wash contrast out)."""
    noise = _noise_layer(im.size, rng, strength)
    if im.mode != "L":
        noise = Image.merge("RGB", (noise, noise, noise))
    return ImageChops.add(im, noise, scale=1.0, offset=-128)


def _ir_falloff(size: tuple[int, int]) -> Image.Image:
    """IR illuminator map: bright center, dark corners (radial, cheap)."""
    w, h = size
    small_w, small_h = w // 16, h // 16
    px = bytearray()
    cx, cy = small_w / 2, small_h / 2
    max_d = math.hypot(cx, cy)
    for y in range(small_h):
        for x in range(small_w):
            d = math.hypot(x - cx, y - cy) / max_d
            px.append(round(255 * (1.0 - 0.55 * d * d)))
    return Image.frombytes("L", (small_w, small_h), bytes(px)).resize(size, Image.BILINEAR)


def process(im: Image.Image, *, ir: bool, seed: int = 0) -> Image.Image:
    """Return the trailcam-look sensor frame (SENSOR_W x SENSOR_H, RGB, no OSD)."""
    rng = random.Random(seed)
    frame = _crop_4x3(im.convert("RGB")).resize((SENSOR_W, SENSOR_H), Image.LANCZOS)

    if ir:
        g = frame.convert("L")
        falloff = _ir_falloff(g.size)
        g = Image.composite(g, g.point(lambda p: p * 0.45), falloff)
        g = ImageEnhance.Brightness(g).enhance(1.12)
        g = ImageEnhance.Contrast(g).enhance(0.92)
        g = g.filter(ImageFilter.GaussianBlur(1.2))  # IR optics are soft
        g = _add_noise(g, rng, 20)
        frame = g.convert("RGB")
    else:
        frame = ImageEnhance.Color(frame).enhance(0.68)  # cheap-sensor color
        frame = ImageEnhance.Contrast(frame).enhance(0.94)
        frame = ImageEnhance.Brightness(frame).enhance(1.03)
        frame = frame.filter(ImageFilter.GaussianBlur(0.6))
        # crunchy in-camera sharpening halo
        frame = frame.filter(ImageFilter.UnsharpMask(radius=3, percent=140, threshold=2))
        frame = _add_noise(frame, rng, 9)

    return frame


def jpeg_tier(frame: Image.Image, tier: str) -> bytes:
    """Encode the clean sensor frame at one of the full-frame quality tiers."""
    width, quality, budget = TIERS[tier]
    im = frame
    if im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    return _encode_to_budget(im, quality, budget)


def thumb_jpeg(frame: Image.Image, bbox: tuple[float, float, float, float]) -> bytes:
    """The detector crop: bbox (fractional l,t,r,b) out of the full frame,
    downscaled hard, compressed to the few-KB LoRa budget."""
    w, h = frame.size
    left, top, right, bottom = bbox
    crop = frame.crop((round(left * w), round(top * h), round(right * w), round(bottom * h)))
    scale = THUMB_MAX_DIM / max(crop.size)
    if scale < 1:
        crop = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
            Image.LANCZOS,
        )
    return _encode_to_budget(crop, THUMB_QUALITY, THUMB_BUDGET)


if __name__ == "__main__":
    import sys

    src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = process(Image.open(src), ir=src.name.startswith("ir-"), seed=42)
    thumb = thumb_jpeg(frame, (0.3, 0.1, 0.9, 0.9))
    (out_dir / f"{src.stem}.thumb.jpg").write_bytes(thumb)
    print(f"thumb    {len(thumb) / 1024:7.1f} KB")
    for tier in TIERS:
        data = jpeg_tier(frame, tier)
        (out_dir / f"{src.stem}.{tier}.jpg").write_bytes(data)
        print(f"{tier:8s} {len(data) / 1024:7.1f} KB")
