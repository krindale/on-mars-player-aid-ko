"""Pass 1 — Extract and classify all vector paths from the source PDF.

The source PDF has every glyph stored as filled vector path; backgrounds,
icons and dotted separators are also paths. We classify each path so later
passes can:
  - mask ONLY glyph paths (preserving all design)
  - use separator/band positions as ground-truth boundaries between items
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
OUT_JSON = ROOT / "out/verify/paths_classified.json"
OUT_OVERLAY = ROOT / "out/verify/paths_class_p{pn:02d}.png"

DPI = 300
SCALE = DPI / 72.0  # PDF point → image pixel


def is_dark(c):
    if not c: return False
    return sum(c[:3]) < 1.6


def is_red_text(c):
    if not c: return False
    r, g, b = c[:3]
    return r > 0.5 and g < 0.5 and b < 0.5


def is_teal_text(c):
    if not c: return False
    r, g, b = c[:3]
    return r < 0.5 and (g > 0.4 or b > 0.4)


def classify(d) -> str:
    bb = d["rect"]
    w, h = bb.width, bb.height
    fill = d.get("fill")
    t = d["type"]

    if t == "f" and is_dark(fill) and w <= 25 and h <= 35:
        return "glyph_dark"
    if t == "f" and is_red_text(fill) and w <= 30 and h <= 40:
        return "glyph_red"
    if t == "f" and is_teal_text(fill) and w <= 30 and h <= 40:
        return "glyph_teal"
    if t == "s" and w <= 12 and h <= 12:
        return "separator_dash"
    if t == "s" and (w > 100 or h > 100):
        return "separator_line"
    if t == "f" and (w > 200 or h > 200):
        if fill and sum(fill[:3]) / 3 > 0.95:
            return "page_bg"
        return "color_band"
    if t == "f" and max(w, h) >= 30:
        return "icon"
    return "other"


GLYPH_KINDS = {"glyph_dark", "glyph_red", "glyph_teal"}


def render_overlay(page, classified, out_path: Path):
    pix = page.get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    ovl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ovl)
    color_map = {
        "glyph_dark":    (255, 0, 0, 100),
        "glyph_red":     (255, 0, 0, 100),
        "glyph_teal":    (255, 0, 0, 100),
        "separator_dash":(0, 220, 0, 200),
        "separator_line":(0, 100, 255, 200),
        "color_band":    (255, 220, 0, 80),
        "icon":          (180, 0, 220, 90),
        "page_bg":       (0, 0, 0, 0),
        "other":         (128, 128, 128, 60),
    }
    for entry in classified:
        col = color_map.get(entry["kind"], (0, 0, 0, 0))
        x0, y0, x1, y1 = entry["bbox_px"]
        d.rectangle([x0, y0, x1, y1], fill=col)
    out = Image.alpha_composite(img, ovl)
    out.thumbnail((2400, 1700))
    out.convert("RGB").save(out_path)


def scan_page(page) -> list:
    drawings = page.get_drawings()
    out = []
    for i, d in enumerate(drawings):
        bb = d["rect"]
        kind = classify(d)
        out.append({
            "i": i,
            "kind": kind,
            "type": d["type"],
            "fill": list(d["fill"]) if d.get("fill") else None,
            "bbox_pt": [bb.x0, bb.y0, bb.x1, bb.y1],
            "bbox_px": [round(bb.x0 * SCALE), round(bb.y0 * SCALE),
                        round(bb.x1 * SCALE), round(bb.y1 * SCALE)],
        })
    return out


def main() -> None:
    doc = fitz.open(SRC_PDF)
    pages = {}
    for pn in (1, 2):
        page = doc[pn - 1]
        classified = scan_page(page)
        pages[pn] = classified
        render_overlay(page, classified, OUT_OVERLAY.with_name(f"paths_class_p{pn:02d}.png"))
        from collections import Counter
        c = Counter(e["kind"] for e in classified)
        print(f"page {pn}: {len(classified)} paths — {dict(c)}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({str(k): v for k, v in pages.items()}, indent=2))
    print(f"saved {OUT_JSON}")


if __name__ == "__main__":
    main()
