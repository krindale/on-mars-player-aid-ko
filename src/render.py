"""On Mars Player Reference - 한글 번역 렌더러.

원본 PDF 페이지를 300 DPI 이미지로 렌더링한 뒤, 영문 텍스트 영역을 흰색으로
마스킹하고 SUIT 폰트로 한글을 오버레이한 다음 PDF 로 내보낸다.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
OUT_PDF = ROOT / "out" / "OnMars-PlayerReference-v07_KO.pdf"
DATA = ROOT / "data" / "paragraphs_ko.json"
FONT_MAP = ROOT / "data" / "font_map.json"
PATHS_CLASSIFIED = ROOT / "out" / "verify" / "paths_classified.json"
OCR_AUDIT_DIR = ROOT / "out" / "verify" / "ocr_audit"

DPI = 300
WHITE_THRESH = 235
GLYPH_INFLATE = 2  # px expand around each glyph bbox before excluding from restore

EMOJI_REPLACEMENTS = {
    # The blueprint rows use unicode emoji as visual hints in source data,
    # but SUIT fonts don't ship emoji glyphs. Replace with simple bracketed
    # markers so they still convey the icon's meaning.
    "⚡": "[획득]",
    "⛏": "[광산]",
    "🏭": "[발전기]",
    "💧": "[수자원]",
    "🌱": "[그린]",
    "🫧": "[산소응축]",
    "🛏": "[셸터]",
    "👤": "[지질학자]",
    "🔬": "[R&D엔지니어]",
    "👨‍🔬": "[수경재배]",
    "👨🔬": "[수경재배]",
    "🧪": "[생화학자]",
    "⚗️": "[지화학자]",
    "⚗": "[지화학자]",
    "🛠": "[엔지니어]",
}


def sanitize(text: str) -> str:
    for k, v in EMOJI_REPLACEMENTS.items():
        text = text.replace(k, v)
    return text


@dataclass
class Style:
    font_path: Path
    size_px: int
    color: tuple
    first_line: "Style | None" = None  # optional override for the first line

    @classmethod
    def load(cls, key: str, font_map: dict) -> "Style":
        s = font_map["styles"][key]
        font_path = ROOT / font_map["fonts"][s["font"]]
        first_line = None
        if "first_line_style" in s:
            fls = s["first_line_style"]
            first_line = cls(
                font_path=ROOT / font_map["fonts"][fls["font"]],
                size_px=int(fls["size_px"]),
                color=tuple(fls["color"]),
            )
        return cls(font_path=font_path, size_px=int(s["size_px"]), color=tuple(s["color"]), first_line=first_line)


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def measure(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap Korean/English text into lines fitting max_width."""
    out_lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            out_lines.append("")
            continue
        # Korean often lacks spaces — split on spaces first, fall back to char-by-char.
        words = paragraph.split(" ")
        line = ""
        for word in words:
            candidate = (line + " " + word).strip() if line else word
            w, _ = measure(candidate, font)
            if w <= max_width or not line:
                if w > max_width and not line:
                    # Single word longer than max_width — char-wrap it.
                    buf = ""
                    for ch in word:
                        cand = buf + ch
                        cw, _ = measure(cand, font)
                        if cw > max_width and buf:
                            out_lines.append(buf)
                            buf = ch
                        else:
                            buf = cand
                    line = buf
                else:
                    line = candidate
            else:
                out_lines.append(line)
                line = word
        if line:
            out_lines.append(line)
    return out_lines


def fit_text(text: str, style: Style, w: int, h: int) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Use declared size; wrap to width only. Tight line spacing (0.9).
    Caller is responsible for expanding the box vertically if needed."""
    size = style.size_px
    font = load_font(style.font_path, size)
    lines = wrap(text, font, w)
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 0.9)
    return font, lines, line_h


def is_zero_bbox(bbox) -> bool:
    return bbox == [0, 0, 0, 0] or bbox == (0, 0, 0, 0)


def render_region(canvas: Image.Image, draw: ImageDraw.ImageDraw, region: dict, font_map: dict,
                  glyphs_in_region: list[dict] | None = None,
                  text_bbox_override: list[int] | None = None) -> None:
    # Mask is now applied at the page level (pixel-perfect). Region just draws Korean.
    text_bbox = text_bbox_override if text_bbox_override is not None else region.get("text_bbox", [0, 0, 0, 0])
    if is_zero_bbox(text_bbox) or not region.get("text"):
        return

    style = Style.load(region["style"], font_map)
    text = sanitize(region["text"])
    align = region.get("align", "left")
    valign = region.get("valign", "center")

    tx0, ty0, tx1, ty1 = text_bbox
    box_w = tx1 - tx0
    box_h = ty1 - ty0

    if (style.first_line and "\n" in text
            and style.first_line.size_px != style.size_px):
        # Render the first line in the heading style, the rest in the body style.
        # Skip when first_line is only used as inline-bold style (same size as body).
        first, rest = text.split("\n", 1)
        first_font = load_font(style.first_line.font_path, style.first_line.size_px)
        fl_w, _ = measure(first, first_font)
        fl_a, fl_d = first_font.getmetrics()
        fl_h = int((fl_a + fl_d) * 1.1)
        if align == "center":
            fx = tx0 + (box_w - fl_w) // 2
        elif align == "right":
            fx = tx1 - fl_w
        else:
            fx = tx0
        draw.text((fx, ty0), first, font=first_font, fill=style.first_line.color)
        ty0_body = ty0 + fl_h + 4
        rest_box_h = ty1 - ty0_body
        if rest_box_h > 12:
            font, lines, line_h = fit_text(rest, style, box_w, rest_box_h)
            y = ty0_body
            for line in lines:
                if not line:
                    y += line_h
                    continue
                w, _ = measure(line, font)
                x = (tx0 + (box_w - w) // 2 if align == "center" else
                     tx1 - w if align == "right" else tx0)
                draw.text((x, y), line, font=font, fill=style.color)
                y += line_h
        return

    font, lines, line_h = fit_text(text, style, box_w, box_h)

    # Compute total visual height; if it exceeds box, expand the box DOWN
    # and paint background to cover any EN ink in the expansion area.
    total_h_needed = line_h * len(lines)
    if total_h_needed > box_h:
        new_y1 = ty0 + total_h_needed + 4
        # Sample bg color from row above the original box
        try:
            base_arr = np.array(canvas)
            samples = []
            if ty0 - 4 >= 0:
                samples.append(base_arr[ty0-4:ty0, tx0:tx1].reshape(-1, 3))
            if samples:
                allpx = np.concatenate(samples, axis=0)
                lum = allpx.sum(axis=1)
                light = allpx[lum > 600]
                bg = np.median(light if len(light) > 30 else allpx, axis=0).astype(np.uint8)
            else:
                bg = np.array([250, 246, 242], dtype=np.uint8)
            base_arr[ty1:new_y1, tx0:tx1] = bg
            canvas.paste(Image.fromarray(base_arr))
            draw = ImageDraw.Draw(canvas)
        except Exception:
            pass
        ty1 = new_y1
        box_h = ty1 - ty0

    bold_font = (load_font(style.first_line.font_path, style.first_line.size_px)
                 if style.first_line else None)
    BOLD_KEYWORDS = ["고급 건물 액션", "부스트마다", "비용", "획득:", "용도:", "그 외:"]
    nonempty_lines = [ln for ln in lines if ln]
    if nonempty_lines:
        sample_bbox = draw.textbbox((0, 0), nonempty_lines[0], font=font)
        visual_top_offset = sample_bbox[1]
        visual_h_one = sample_bbox[3] - sample_bbox[1]
    else:
        visual_top_offset = 0; visual_h_one = line_h
    total_visual_h = (len(lines) - 1) * line_h + visual_h_one
    if valign == "top":
        y = ty0 - visual_top_offset
    else:
        y = ty0 + (box_h - total_visual_h) // 2 - visual_top_offset
    for li, line in enumerate(lines):
        if not line:
            y += line_h
            continue
        if bold_font and any(kw in line for kw in BOLD_KEYWORDS):
            # Split line into alternating (regular, bold) segments by keyword
            segments = []
            cur = line
            while cur:
                best = None
                for kw in BOLD_KEYWORDS:
                    idx = cur.find(kw)
                    if idx >= 0 and (best is None or idx < best[0]):
                        best = (idx, kw)
                if best is None:
                    segments.append(('r', cur)); break
                idx, kw = best
                if idx > 0: segments.append(('r', cur[:idx]))
                segments.append(('b', kw))
                cur = cur[idx+len(kw):]
            full_w = 0
            for kind, seg in segments:
                f = bold_font if kind == 'b' else font
                full_w += measure(seg, f)[0]
            if align == "center":
                x = tx0 + (box_w - full_w) // 2
            elif align == "right":
                x = tx1 - full_w
            else:
                x = tx0
            for kind, seg in segments:
                f = bold_font if kind == 'b' else font
                col = style.first_line.color if kind == 'b' else style.color
                draw.text((x, y), seg, font=f, fill=col)
                x += measure(seg, f)[0]
        else:
            w, _ = measure(line, font)
            if align == "center":
                x = tx0 + (box_w - w) // 2
            elif align == "right":
                x = tx1 - w
            else:
                x = tx0
            draw.text((x, y), line, font=font, fill=style.color)
        y += line_h


def build_glyph_mask(shape: tuple[int, int], paths: list[dict]) -> np.ndarray:
    """Boolean mask: True where a real text-glyph path bbox lies."""
    H, W = shape
    mask = np.zeros((H, W), dtype=bool)
    for p in paths:
        if not is_real_glyph(p):
            continue
        x0, y0, x1, y1 = (int(v) for v in p["bbox_px"])
        x0 = max(0, x0 - GLYPH_INFLATE); y0 = max(0, y0 - GLYPH_INFLATE)
        x1 = min(W, x1 + GLYPH_INFLATE); y1 = min(H, y1 + GLYPH_INFLATE)
        mask[y0:y1, x0:x1] = True
    return mask


def is_text_color(en_arr: np.ndarray) -> np.ndarray:
    """Mask of pixels matching text colors (incl. anti-alias halos).

    Text in the source PDF is black, red (heading), or teal (sub-heading).
    Their anti-aliased edges are near-grayscale at varying brightness, which
    a pure-black-only filter misses. So we treat any low-saturation pixel as
    text + add explicit red/teal bands. Colored icons (orange/blue/yellow/
    green) all have saturation >> 30 and pass through.
    """
    r = en_arr[..., 0].astype(int)
    g = en_arr[..., 1].astype(int)
    b = en_arr[..., 2].astype(int)
    sat = np.maximum.reduce([r, g, b]) - np.minimum.reduce([r, g, b])
    grayscale = sat < 50                                     # any near-grayscale (black text + AA halo)
    redish    = (r > 150) & (g < 140) & (b < 150) & (r > g + 30) & (r > b + 20)  # red text incl. AA
    tealish   = (g > 120) & (b > 120) & (r < g) & (g - r > 20)                   # teal text incl. AA
    return grayscale | redish | tealish


def restore_icons(en_img: Image.Image, ko_img: Image.Image, glyph_mask: np.ndarray) -> Image.Image:
    """Paste EN pixels onto KO where:
        - EN had ink   AND
        - KO is now blank (mask_bbox erased it)  AND
        - pixel is NOT inside a glyph bbox  AND
        - pixel color is NOT a text color (black/red/teal)
    This restores all icons/images and inline colored markers without bringing
    back any English glyphs.
    """
    en_arr = np.array(en_img); ko_arr = np.array(ko_img)
    en_lum = en_arr.mean(axis=2)
    ko_lum = ko_arr.mean(axis=2)
    text_pixel = is_text_color(en_arr)
    lost = (en_lum < WHITE_THRESH) & (ko_lum >= WHITE_THRESH) & ~glyph_mask & ~text_pixel
    ko_arr[lost] = en_arr[lost]
    print(f"[restore] {int(lost.sum())} px restored (EN icons/images, text colors filtered)")
    return Image.fromarray(ko_arr)


GLYPH_MAX_DIM = 50  # px — anything wider/taller treated as illustration


def is_text_fill(fill) -> bool:
    """True if fill matches any TEXT palette color in the source PDF.

    Source uses several reds and teals (pure red 1.0,0,0 in resources body;
    section-red 0.876,0.21,0.25; teal 0.298,0.722,0.769; dark-teal 0.24,0.47,0.47).
    Ranges are wide enough to catch all variants but exclude illustration colors.
    """
    if not fill:
        return True
    r, g, b = fill[:3]
    if r + g + b < 0.45:                                                  # near-black
        return True
    if r >= 0.80 and g <= 0.35 and b <= 0.40 and abs(g - b) < 0.20:       # any red text (incl. pure red)
        return True
    if 0.20 <= r <= 0.40 and 0.55 <= g <= 0.85 and 0.55 <= b <= 0.85 and abs(g - b) < 0.20:
        return True   # bright teal text ≈ (0.298,0.722,0.769)
    if 0.18 <= r <= 0.32 and 0.40 <= g <= 0.58 and 0.40 <= b <= 0.58 and abs(g - b) < 0.12:
        return True   # darker teal sub-label ≈ (0.24,0.47,0.47)
    return False


def is_real_glyph(p: dict) -> bool:
    """True if path is REALLY a text glyph (small + text-color fill), not an illustration.

    For `other` kind (scan_paths' catch-all) we tighten size to 30px because
    real text fragments there are 14-21px chars, while icon shapes (e.g. the
    ORBITAL pentagon's 40px compass body) must escape the filter.
    For `separator_dash` we tighten to 18px since real separator dots are
    tiny, and larger ones are usually illustration sub-strokes.
    """
    kind = p.get("kind", "")
    if kind in NON_GLYPH_KINDS:
        return False
    px0, py0, px1, py1 = p["bbox_px"]
    w = px1 - px0; h = py1 - py0
    if kind == "other":
        max_dim = 30
    elif kind == "separator_dash":
        max_dim = 18
    else:
        max_dim = GLYPH_MAX_DIM
    if w > max_dim or h > max_dim:
        return False
    return is_text_fill(p.get("fill"))


# Path kinds NEVER masked (preserved as visuals).
NON_GLYPH_KINDS = {"color_band", "separator_line", "icon"}


def glyphs_in_bbox(paths: list[dict], bbox: list[int]) -> list[dict]:
    """Return real text-glyph paths overlapping bbox. Illustrations excluded."""
    if not paths or not bbox or bbox == [0, 0, 0, 0]:
        return []
    bx0, by0, bx1, by1 = bbox
    out = []
    for p in paths:
        if not is_real_glyph(p):
            continue
        px0, py0, px1, py1 = p["bbox_px"]
        if px1 < bx0 or px0 > bx1 or py1 < by0 or py0 > by1:
            continue
        out.append(p)
    return out


def build_text_pixel_mask(en_arr: np.ndarray, paths: list[dict] | None) -> np.ndarray:
    """Pixel-perfect text mask.

    For each real-glyph path, examine the actual pixels inside its bbox and
    mark only those whose color matches the glyph's fill (with anti-alias
    tolerance). Pixels of other colors inside the same bbox (e.g. an icon
    border that happens to overlap the bbox) are NOT marked, so they survive.
    """
    H, W = en_arr.shape[:2]
    text = np.zeros((H, W), dtype=bool)
    if not paths:
        return text
    en_int = en_arr.astype(int)

    for p in paths:
        if not is_real_glyph(p):
            continue
        x0, y0, x1, y1 = (int(v) for v in p["bbox_px"])
        x0 = max(0, x0 - 1); y0 = max(0, y0 - 1)
        x1 = min(W, x1 + 1); y1 = min(H, y1 + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = en_int[y0:y1, x0:x1]
        fill = p.get("fill")
        if fill is None:
            # Stroke-only glyph: any dark pixel
            mask = crop.mean(axis=2) < 200
        else:
            target = np.array([int(c * 255) for c in fill[:3]])
            # Allow generous tolerance to catch anti-alias halos on glyph stroke
            diff = np.abs(crop - target).max(axis=2)
            mask = diff < 110
            # Also accept any near-grayscale pixel (text AA halo)
            sat = np.maximum.reduce([crop[..., 0], crop[..., 1], crop[..., 2]]) - \
                  np.minimum.reduce([crop[..., 0], crop[..., 1], crop[..., 2]])
            mask |= (sat < 25) & (crop.mean(axis=2) < 230)
        text[y0:y1, x0:x1] |= mask
    return text


def build_pixel_icon_mask(en_arr: np.ndarray, paths: list[dict] | None = None) -> np.ndarray:
    """Per-pixel icon detection.

    Order matters:
      1. Saturated pixels = candidate icon
      2. DILATE first so anti-alias halos around icons are protected
      3. CARVE OUT real text glyph regions (also dilated) so red/teal heading
         text never gets protected. If we carved first then dilated, the
         dilation would re-cover the glyph rectangles.
    """
    r = en_arr[..., 0].astype(int)
    g = en_arr[..., 1].astype(int)
    b = en_arr[..., 2].astype(int)
    sat = np.maximum.reduce([r, g, b]) - np.minimum.reduce([r, g, b])
    icon = sat > 35

    try:
        from scipy import ndimage
        icon = ndimage.binary_dilation(icon, iterations=3)
    except ImportError:
        pass

    if paths:
        H, W = icon.shape
        for p in paths:
            if not is_real_glyph(p):
                continue
            x0, y0, x1, y1 = (int(v) for v in p["bbox_px"])
            # Carve > dilation radius so the halo painted by dilate(3) is fully removed.
            pad = 10
            x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
            x1 = min(W, x1 + pad); y1 = min(H, y1 + pad)
            icon[y0:y1, x0:x1] = False

    return icon


def shrink_text_bbox_around_icons(text_bbox: list[int], icon_mask: np.ndarray) -> list[int]:
    """Crop text_bbox so it does NOT overlap icon pixels on its left or right edge.

    Walks in from each side, finding the first column whose icon-pixel count is
    significant (>= 3 px). If icons occupy the leftmost/rightmost stretch up to
    a threshold width (≤ 200 px), the bbox is shrunk past them with padding.
    """
    if not text_bbox or text_bbox == [0, 0, 0, 0]:
        return text_bbox
    x0, y0, x1, y1 = text_bbox
    if x1 <= x0 or y1 <= y0:
        return text_bbox
    crop = icon_mask[y0:y1, x0:x1]
    col_icon = crop.sum(axis=0) >= 3      # column has meaningful icon pixels
    if not col_icon.any():
        return text_bbox
    n = len(col_icon)

    new_x0 = x0
    if col_icon[:60].any():
        # find rightmost icon column inside the leftmost 220 px run
        scan = min(220, n)
        last = -1
        for i in range(scan):
            if col_icon[i]:
                last = i
        if 0 <= last < scan:
            new_x0 = x0 + last + 8

    new_x1 = x1
    rev = col_icon[::-1]
    if rev[:60].any():
        scan = min(220, n)
        first_from_right = -1
        for i in range(scan):
            if rev[i]:
                first_from_right = i
        if 0 <= first_from_right < scan:
            new_x1 = x1 - first_from_right - 8

    if new_x1 - new_x0 < 80:
        return text_bbox  # shrunk too much; leave original
    return [new_x0, y0, new_x1, y1]


def build_ocr_text_mask(shape: tuple[int, int], page_num: int, inflate: int = 4) -> np.ndarray:
    """Authoritative text mask from OCR cache (page0N_words.json).
    Each detected word's bbox (slightly inflated for AA halo) becomes True.
    """
    H, W = shape
    mask = np.zeros((H, W), dtype=bool)
    cache = OCR_AUDIT_DIR / f"page{page_num:02d}_words.json"
    if not cache.exists():
        return mask
    with open(cache) as f:
        data = json.load(f)
    for w in data.get("words", []):
        x0, y0, x1, y1 = w["bbox"]
        x0 = max(0, x0 - inflate); y0 = max(0, y0 - inflate)
        x1 = min(W, x1 + inflate); y1 = min(H, y1 + inflate)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask


def build_preserve_mask(shape: tuple[int, int], regions: list[dict]) -> np.ndarray:
    """Mask of explicit image-region bboxes — these areas are NEVER masked
    and are always copied back from EN at the end."""
    H, W = shape
    mask = np.zeros((H, W), dtype=bool)
    for r in regions:
        if r.get("type") != "image":
            continue
        bbox = r.get("bbox")
        if not bbox or bbox == [0, 0, 0, 0]:
            continue
        x0, y0, x1, y1 = (int(v) for v in bbox)
        x0 = max(0, x0); y0 = max(0, y0)
        x1 = min(W, x1); y1 = min(H, y1)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask


def render_page(doc: fitz.Document, page_index: int, page_spec: dict,
                font_map: dict, paths_for_page: list[dict] | None = None) -> Image.Image:
    page = doc[page_index]
    pix = page.get_pixmap(dpi=DPI)
    en_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    en_arr = np.array(en_img)
    H, W = en_arr.shape[:2]

    # Step 0: explicit image regions (preservation safety net).
    preserve_mask = build_preserve_mask((H, W), page_spec["regions"])

    # Step 1: text mask = (user-defined mask_bbox union) ∪ (OCR word bboxes
    # that are NOT inside any image region). This catches EN words that the
    # user-defined boxes don't fully cover, without harming protected images.
    text_pix = np.zeros((H, W), dtype=bool)
    for region in page_spec["regions"]:
        if region.get("type", "text") != "text": continue
        mb = region.get("mask_bbox")
        if not mb or mb == [0,0,0,0]: continue
        x0, y0, x1, y1 = (max(0,mb[0]), max(0,mb[1]),
                          min(W,mb[2]), min(H,mb[3]))
        if x1 > x0 and y1 > y0:
            text_pix[y0:y1, x0:x1] = True
    # Add OCR word bboxes (not inside image regions)
    ocr_mask = build_ocr_text_mask((H, W), page_index + 1, inflate=2)
    preserve = build_preserve_mask((H, W), page_spec["regions"])
    text_pix |= ocr_mask & ~preserve

    # Step 2: icon pixels = all EN ink that isn't text.
    en_lum = en_arr.mean(axis=2)
    bg = en_lum >= 240
    icon_pix = (~bg) & (~text_pix)
    # preserve_mask AND not text = guaranteed-protected pixels
    protect_pix = preserve_mask & (~text_pix)
    icon_pix |= protect_pix
    print(f"[page {page_index+1}] text_px={int(text_pix.sum())}  "
          f"icon_px={int(icon_pix.sum())}  preserve_px={int(preserve_mask.sum())}  "
          f"protect_px={int(protect_pix.sum())}")

    # Step 3: start from EN, mask each text region's mask_bbox with the
    # SURROUNDING background color (sampled from a thin border just outside
    # the box). This blends the masked area into the page background instead
    # of leaving harsh white rectangles.
    img_arr = en_arr.copy()
    for region in page_spec["regions"]:
        if region.get("type", "text") != "text": continue
        mb = region.get("mask_bbox")
        if not mb or mb == [0,0,0,0]: continue
        x0, y0, x1, y1 = (max(0,mb[0]), max(0,mb[1]),
                          min(W,mb[2]), min(H,mb[3]))
        if x1 <= x0 or y1 <= y0: continue
        # Sample background: 4-side outer strips (top, bottom, left, right).
        # Use mode (most common color) to dodge dark glyph fragments + colored bands.
        samples = []
        pad = 4
        if y0 - pad >= 0:
            samples.append(en_arr[y0-pad:y0, x0:x1].reshape(-1, 3))
        if y1 + pad <= H:
            samples.append(en_arr[y1:y1+pad, x0:x1].reshape(-1, 3))
        if x0 - pad >= 0:
            samples.append(en_arr[y0:y1, x0-pad:x0].reshape(-1, 3))
        if x1 + pad <= W:
            samples.append(en_arr[y0:y1, x1:x1+pad].reshape(-1, 3))
        if samples:
            allpx = np.concatenate(samples, axis=0)
            # Drop dark pixels (potential nearby glyph stroke) before median
            lum = allpx.sum(axis=1)
            light = allpx[lum > 600]   # near-white/beige only
            ref = light if len(light) > 30 else allpx
            bg = np.median(ref, axis=0).astype(np.uint8)
        else:
            bg = np.array([250, 246, 242], dtype=np.uint8)
        img_arr[y0:y1, x0:x1] = bg
    img = Image.fromarray(img_arr)
    draw = ImageDraw.Draw(img)

    # Step 4: draw Korean text in each text-type region, auto-avoiding icons.
    for region in page_spec["regions"]:
        if region.get("type", "text") != "text":
            continue
        try:
            adj_text_bbox = shrink_text_bbox_around_icons(region.get("text_bbox", [0,0,0,0]), icon_pix)
            render_region(img, draw, region, font_map, None, adj_text_bbox)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] region {region.get('id')} failed: {e}", file=sys.stderr)

    # Step 5: re-paste icon/preserve pixels from EN — image always wins z-order.
    img_arr = np.array(img)
    img_arr[icon_pix] = en_arr[icon_pix]
    img_arr[protect_pix] = en_arr[protect_pix]
    return Image.fromarray(img_arr)


def main() -> None:
    # Prefer per-page files (paragraphs_p1.json, paragraphs_p2.json) if present;
    # fallback to legacy paragraphs_ko.json single file.
    per_page_files = sorted((ROOT / "data").glob("paragraphs_p*.json"))
    if per_page_files:
        pages_data = []
        for pf in per_page_files:
            with open(pf, "r", encoding="utf-8") as f:
                pages_data.append(json.load(f))
        pages_data.sort(key=lambda p: p["page"])
        data = {
            "image_size": pages_data[0].get("image_size"),
            "pdf_size_pt": pages_data[0].get("pdf_size_pt", [1008, 612]),
            "pages": [{"page": p["page"], "regions": p["regions"]} for p in pages_data],
        }
        print(f"[load] using per-page files: {[pf.name for pf in per_page_files]}")
    else:
        with open(DATA, "r", encoding="utf-8") as f:
            data = json.load(f)
    with open(FONT_MAP, "r", encoding="utf-8") as f:
        font_map = json.load(f)
    paths_classified = None
    if PATHS_CLASSIFIED.exists():
        with open(PATHS_CLASSIFIED, "r", encoding="utf-8") as f:
            paths_classified = json.load(f)

    out_dir = ROOT / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_ko_dir = out_dir / "pages_ko"
    pages_ko_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(SRC_PDF)
    out_doc = fitz.open()

    pdf_w_pt, pdf_h_pt = data["pdf_size_pt"]

    for spec in data["pages"]:
        page_idx = spec["page"] - 1
        paths_for_page = (paths_classified or {}).get(str(spec["page"]))
        img = render_page(doc, page_idx, spec, font_map, paths_for_page)
        png_path = pages_ko_dir / f"page_{spec['page']:02d}_ko.png"
        img.save(png_path, "PNG", optimize=True)
        print(f"[ok] rendered {png_path}")

        new_page = out_doc.new_page(width=pdf_w_pt, height=pdf_h_pt)
        # PyMuPDF expects bytes for image insertion at full page rect.
        png_bytes = png_path.read_bytes()
        new_page.insert_image(new_page.rect, stream=png_bytes)

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(OUT_PDF, deflate=True)
    out_doc.close()
    doc.close()
    print(f"[ok] wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
