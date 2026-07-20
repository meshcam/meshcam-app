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

Narrow frames are upscaled (Lanczos) to _STAMP_MIN_W BEFORE the bar is drawn:
the gallery displays small thumbs blown up to fill the screen anyway, and
stamping first meant the bar text rode along through that upscale as mush.
The photo content can't gain real detail, but the text can — it's rendered
from vectors, so give it the pixels. Costs only stored bytes (roughly 3-4x
per thumb), never LoRa airtime.

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
_STAMP_MIN_W = 640  # narrower frames upscale to this before stamping (≤3x)

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
    src_is_jpeg = im.format == "JPEG"
    upscaled = im.width < _STAMP_MIN_W
    if upscaled:
        scale = min(3.0, _STAMP_MIN_W / im.width)
        im = im.resize(
            (round(im.width * scale), round(im.height * scale)), Image.Resampling.LANCZOS
        )
    keep_tables = src_is_jpeg and not upscaled and im.mode in ("RGB", "L")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    _draw_bar(
        im,
        camera_label=camera_label,
        captured_at=captured_at,
        temp_c=temp_c,
        battery_v=battery_v,
        tz=tz,
        ir=ir,
    )

    out = BytesIO()
    if keep_tables:
        # Reuse the source's quantization tables: no generational quality loss
        # beyond the bar band, and the file stays close to the size that
        # actually crossed the radio.
        im.save(out, "JPEG", quality="keep")
    else:
        im.save(out, "JPEG", quality=85)
    return out.getvalue()


def _draw_bar(
    im: Image.Image,
    *,
    camera_label: str,
    captured_at: datetime,
    temp_c: float | None,
    battery_v: float | None,
    tz: str,
    ir: bool,
    min_bar_h: int = 0,
) -> None:
    """Draw the info bar in place. `min_bar_h` lets restamp() grow the bar to
    fully cover an older burned-in band."""
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
    bar_h = max(round(2.2 * size), round(w * _BAR_FRAC), min_bar_h)
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


def _bar_band(im: Image.Image) -> int:
    """Measured height (px) of a burned-in bar at the frame's bottom: the run
    of bottom rows whose left AND right margins are near-black. The bar spans
    the full width with text only inside the padding, so its margins are pure
    fill (gray 8) — unlike text rows, which defeat any whole-row statistic.
    0 when the bottom isn't a bar."""
    gray = im if im.mode == "L" else im.convert("L")
    w, h = gray.size
    px = gray.load()
    band = 0
    for row in range(h - 1, h - 1 - h // 4, -1):
        edges = (px[0, row], px[1, row], px[2, row], px[w - 3, row], px[w - 2, row], px[w - 1, row])
        if max(edges) > 48:
            break
        band += 1
    return band


def restamp(
    jpeg: bytes,
    *,
    camera_label: str,
    captured_at: datetime,
    temp_c: float | None = None,
    battery_v: float | None = None,
    tz: str = "",
) -> bytes | None:
    """Redo an already-stamped narrow frame at _STAMP_MIN_W: upscale, then
    draw a fresh bar sized to fully cover the old burned-in band, so the
    footer text is crisp even though the photo content stays what the radio
    delivered. The pixels under the old bar are gone either way — it's a flat
    band, so covering it loses nothing.

    Returns None when there's nothing to do: already wide enough, too narrow
    to ever stamp, or the bottom edge doesn't measure like our bar (an
    unstamped-era frame, or a dark frame we must not paint over). An old IR
    chip is left alone — a second crisp chip beside the mushy one would read
    as a glitch.
    """
    im = Image.open(BytesIO(jpeg))
    im.load()
    if im.width >= _STAMP_MIN_W or im.width < _MIN_WIDTH:
        return None
    band = _bar_band(im)
    w = im.width
    pad = max(8, round(w * _PAD_FRAC))
    size, _, _ = _layout(w, pad, camera_label, captured_at)
    expected = max(round(2.2 * size), round(w * _BAR_FRAC))
    if not (expected * 0.6 <= band <= expected * 1.8):
        return None
    scale = min(3.0, _STAMP_MIN_W / w)
    im = im.resize((round(w * scale), round(im.height * scale)), Image.Resampling.LANCZOS)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    _draw_bar(
        im,
        camera_label=camera_label,
        captured_at=captured_at,
        temp_c=temp_c,
        battery_v=battery_v,
        tz=tz,
        ir=False,
        min_bar_h=round(max(band, expected) * scale) + 1,
    )
    out = BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()
