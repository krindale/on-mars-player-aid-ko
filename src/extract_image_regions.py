"""Extract image region candidates from paths_classified.json.

Output: out/verify/image_candidates.json — list of {id, type:"image", bbox, note}
        + out/verify/icon_audit/page0{1,2}_image_overlay.png — visual verification

Logic:
  1. Collect all paths that are "image-like":
     - kind == 'icon'
     - kind == 'color_band' or 'separator_line' (preserved visuals)
     - kind in {'other', 'glyph_*'} but NOT is_real_glyph (i.e. wrongly classified
       large colored fills that are illustration parts)
  2. Cluster nearby image-like paths (gap-based clustering, max gap = 30px).
  3. Each cluster's union bbox = one image region.
  4. Optionally pad bbox by 4 px so anti-alias halos are included.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PATHS = ROOT / "out" / "verify" / "paths_classified.json"
SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
OUT_JSON = ROOT / "out" / "verify" / "image_candidates.json"
OUT_OVERLAY_DIR = ROOT / "out" / "verify" / "icon_audit"

GAP_PX = 8         # cluster paths whose bbox edges are within this many px
PAD_PX = 3         # pad final bbox to include anti-alias
MAX_DIM_PX = 600   # any cluster wider/taller than this is too big — likely merged with text

sys.path.insert(0, str(ROOT / "src"))
from render import is_real_glyph  # reuse existing logic


def candidate_paths(paths):
    """Return paths that are 'image-like' — to be preserved."""
    out = []
    for p in paths:
        kind = p.get("kind", "")
        if kind in ("icon", "color_band", "separator_line"):
            out.append(p)
            continue
        # Wrongly-classified glyph_* / other / separator_dash that's actually large illustration
        if kind.startswith("glyph_") or kind == "other" or kind == "separator_dash":
            if not is_real_glyph(p):
                # Big enough to be visual, not text
                x0, y0, x1, y1 = p["bbox_px"]
                if (x1 - x0) >= 8 and (y1 - y0) >= 8:
                    out.append(p)
    return out


def cluster_bboxes(items, gap=GAP_PX):
    """Greedy gap-based clustering. Each item is dict with bbox_px.
    Returns list of clusters (each cluster = list of items).
    """
    if not items:
        return []
    # Sort by y center then x center
    items_sorted = sorted(items, key=lambda p: (p["bbox_px"][1], p["bbox_px"][0]))
    clusters = []  # each cluster: dict {bbox: [x0,y0,x1,y1], items: [...]}
    for it in items_sorted:
        x0, y0, x1, y1 = it["bbox_px"]
        merged = False
        for c in clusters:
            cx0, cy0, cx1, cy1 = c["bbox"]
            # If item bbox is within `gap` of cluster bbox, merge
            if (x0 <= cx1 + gap and x1 >= cx0 - gap and
                y0 <= cy1 + gap and y1 >= cy0 - gap):
                c["bbox"] = [min(cx0, x0), min(cy0, y0), max(cx1, x1), max(cy1, y1)]
                c["items"].append(it)
                merged = True
                break
        if not merged:
            clusters.append({"bbox": [x0, y0, x1, y1], "items": [it]})

    # Multi-pass merge (clusters created later may now overlap earlier ones)
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            if clusters[i] is None:
                continue
            for j in range(i + 1, len(clusters)):
                if clusters[j] is None:
                    continue
                ax0, ay0, ax1, ay1 = clusters[i]["bbox"]
                bx0, by0, bx1, by1 = clusters[j]["bbox"]
                if (bx0 <= ax1 + gap and bx1 >= ax0 - gap and
                    by0 <= ay1 + gap and by1 >= ay0 - gap):
                    clusters[i]["bbox"] = [min(ax0, bx0), min(ay0, by0),
                                           max(ax1, bx1), max(ay1, by1)]
                    clusters[i]["items"].extend(clusters[j]["items"])
                    clusters[j] = None
                    changed = True
        clusters = [c for c in clusters if c is not None]
    return clusters


def main():
    with open(PATHS) as f:
        pdata = json.load(f)

    doc = fitz.open(SRC_PDF)
    out_data = {}

    for page_str, paths in pdata.items():
        page_idx = int(page_str) - 1
        cands = candidate_paths(paths)
        clusters = cluster_bboxes(cands)
        print(f"page {page_str}: {len(cands)} candidate paths -> {len(clusters)} clusters")

        # Pad and export
        en_pix = doc[page_idx].get_pixmap(dpi=300)
        H, W = en_pix.height, en_pix.width
        regions = []
        for i, c in enumerate(clusters):
            x0, y0, x1, y1 = c["bbox"]
            x0 = max(0, x0 - PAD_PX); y0 = max(0, y0 - PAD_PX)
            x1 = min(W, x1 + PAD_PX); y1 = min(H, y1 + PAD_PX)
            if (x1 - x0) < 12 or (y1 - y0) < 12:
                continue
            if (x1 - x0) > MAX_DIM_PX or (y1 - y0) > MAX_DIM_PX:
                # Skip overly large clusters — likely merged with surrounding text
                continue
            regions.append({
                "id": f"p{page_str}_img_{i+1:03d}",
                "type": "image",
                "bbox": [x0, y0, x1, y1],
                "n_paths": len(c["items"]),
            })
        out_data[page_str] = regions

        # Visual overlay
        en_img = Image.frombytes("RGB", (W, H), en_pix.samples).convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for r in regions:
            x0, y0, x1, y1 = r["bbox"]
            draw.rectangle([x0, y0, x1, y1], outline=(0, 200, 0, 255), width=4)
        composed = Image.alpha_composite(en_img, overlay).convert("RGB")
        composed.save(OUT_OVERLAY_DIR / f"page{int(page_str):02d}_image_overlay.png", "PNG", optimize=True)
        print(f"  saved overlay -> page{int(page_str):02d}_image_overlay.png")

    doc.close()

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
