"""Burned-in OSD info bar — the classic trailcam footer, applied server-side.

Every image (thumbnail AND full) gets the camera name, temperature, battery,
timestamp (and an IR chip when the frame is infrared) stamped into its bottom
edge at ingest. Deliberately NOT done in leaf firmware: LoRa bytes are the
scarcest resource (the bar band is entropy we'd pay to radio), leaf clocks
drift (ingest corrects the timestamp — firmware would burn the wrong one into
pixels forever), and one server-side implementation restyles every camera
hardware revision at once.

The layout is adaptive: fonts never shrink below legibility, and fields drop
out by measurement when the frame is narrow — brand first, then seconds, then
temp/battery — so a 320 px detector-crop thumbnail (sometimes the ONLY thing
a deep node ever delivers) gets a clean compact bar instead of a smudge.

Fulls keep their pristine bytes at a `.raw` sibling S3 key (see
`trailcam.s3.raw_variant`) — visible only via `?size=raw`, because the
stamped frame is what people actually want when they share a photo.
Thumbnails are stamped in place (no twin: the full IS their original).
"""

import logging
from datetime import datetime
from functools import lru_cache
from importlib import resources
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

logger = logging.getLogger(__name__)

# Geometry as fractions of frame width — the full-frame look, normalized from
# bar 96 px / font 44 px / small 34 px / pad 36 px at QXGA 2048.
_BAR_FRAC = 96 / 2048
_FONT_FRAC = 44 / 2048
_SMALL_FRAC = 34 / 2048
_PAD_FRAC = 36 / 2048
_FONT_IDEAL_MIN = 12  # target legibility floor…
_FONT_HARD_MIN = 9  # …shrink this far only to make a narrow frame fit
_MIN_WIDTH = 200  # below this no layout is readable — return unstamped

_FONT_RES = resources.files("trailcam") / "fonts" / "DejaVuSansMono-Bold.ttf"


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    with resources.as_file(_FONT_RES) as path:
        return ImageFont.truetype(path, size)


def battery_pct(battery_v: float) -> int:
    """Single Li-ion cell under load: ~3.0 V empty, ~3.5 V full-ish."""
    return max(5, min(100, round((battery_v - 3.0) / 0.5 * 100)))


def looks_ir(im: Image.Image) -> bool:
    """IR frames are grayscale JPEGs (or RGB with equal channels). Sampled on a
    thumbnail so it's cheap at any resolution."""
    if im.mode == "L":
        return True
    if im.mode != "RGB":
        return False
    r, g, b = im.resize((32, 32)).split()
    spread = max(
        ImageStat.Stat(ImageChops.difference(r, g)).mean[0],
        ImageStat.Stat(ImageChops.difference(g, b)).mean[0],
        ImageStat.Stat(ImageChops.difference(r, b)).mean[0],
    )
    return spread < 4


def _layout(
    w: int, pad: int, camera_label: str, captured_at: datetime
) -> tuple[int, str, str]:
    """Pick (font_size, left_text, right_text): the richest combination that
    fits, preferring dropping fields over shrinking below the ideal floor."""
    label = camera_label.upper()
    lefts = [f"MESHCAM  •  {label}", label]
    rights = [
        captured_at.strftime("%m/%d/%Y  %I:%M:%S %p"),
        captured_at.strftime("%m/%d/%Y %I:%M %p"),
        captured_at.strftime("%m/%d %I:%M %p"),
    ]
    avail = w - 2 * pad
    size = max(_FONT_IDEAL_MIN, round(w * _FONT_FRAC))
    while True:
        font = _font(size)
        gap = size
        for left in lefts:
            for right in rights:
                if font.getlength(left) + gap + font.getlength(right) <= avail:
                    return size, left, right
        if size <= _FONT_HARD_MIN:
            return size, lefts[-1], rights[-1]  # pathological; shortest of each
        size -= 1


def stamp(
    jpeg: bytes,
    *,
    camera_label: str,
    captured_at: datetime,
    temp_c: float | None = None,
    battery_v: float | None = None,
    tz: str = "",
) -> bytes:
    """Return `jpeg` with the info bar burned into the bottom edge.

    Raises on undecodable input — the caller stores the original unstamped
    rather than losing an image over a watermark.
    """
    im = Image.open(BytesIO(jpeg))
    im.load()
    if im.width < _MIN_WIDTH:
        return jpeg
    ir = looks_ir(im)
    keep_tables = im.format == "JPEG" and im.mode in ("RGB", "L")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    if tz:
        try:
            captured_at = captured_at.astimezone(ZoneInfo(tz))
        except (KeyError, ValueError):
            logger.warning("osd: unknown timezone %r — stamping UTC", tz)

    w, h = im.size
    pad = max(8, round(w * _PAD_FRAC))
    size, left, right = _layout(w, pad, camera_label, captured_at)
    font = _font(size)
    small = _font(max(10, min(size - 2, round(w * _SMALL_FRAC))))
    bar_h = max(round(2.2 * size), round(w * _BAR_FRAC))
    baseline = h - bar_h // 2

    # All bar colors are grayscale so one code path draws on RGB and L frames.
    def c(gray: int) -> int | tuple[int, int, int]:
        return gray if im.mode == "L" else (gray, gray, gray)

    mid_parts = []
    if temp_c is not None:
        mid_parts.append(f"{temp_c:.0f}°C / {temp_c * 9 / 5 + 32:.0f}°F")
    if battery_v is not None:
        mid_parts.append(f"BATT {battery_pct(battery_v)}%")
    mid = "   ".join(mid_parts)
    if mid:
        # centered — keep only if clear of both ends (narrow frames drop it)
        half = small.getlength(mid) / 2
        left_end = pad + font.getlength(left) + size
        right_start = w - pad - font.getlength(right) - size
        if w / 2 - half < left_end or w / 2 + half > right_start:
            mid = ""

    draw = ImageDraw.Draw(im)
    draw.rectangle((0, h - bar_h, w, h), fill=c(8))
    draw.text((pad, baseline), left, font=font, fill=c(235), anchor="lm")
    if mid:
        draw.text((w // 2, baseline), mid, font=small, fill=c(200), anchor="mm")
    draw.text((w - pad, baseline), right, font=font, fill=c(235), anchor="rm")
    if ir:
        draw.text((pad, h - bar_h - round(bar_h * 0.3)), "IR", font=small, fill=c(220), anchor="lm")

    out = BytesIO()
    if keep_tables:
        # Reuse the source's quantization tables: no generational quality loss
        # beyond the bar band, and the file stays close to the size that
        # actually crossed the radio.
        im.save(out, "JPEG", quality="keep")
    else:
        im.save(out, "JPEG", quality=85)
    return out.getvalue()
