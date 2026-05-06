"""Pass 2 — Group glyph paths into hierarchical text items.

Input:  out/verify/paths_classified.json (from scan_paths.py)
Output: out/verify/items.json
        out/verify/items_overlay_p0{1,2}.png

Algorithm:
1. Glyph paths → characters: union nearby glyph paths (gap < 3pt) into char bboxes
2. Characters → lines: same y baseline (±3pt) → one line; lines sorted top-to-bottom
3. Lines → items: y-gap-threshold = avg line height × 1.5;  also break when a
   horizontal separator (separator_dash row) is detected between two lines
4. Item bbox = union of all lines' bboxes
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PATHS_JSON = ROOT / "out/verify/paths_classified.json"
OUT_JSON = ROOT / "out/verify/items.json"
EN_HI = {1: ROOT / "out/pages_en/page_01_hi.png",
         2: ROOT / "out/pages_en/page_02_hi.png"}

DPI = 300
SCALE = DPI / 72.0


def union_bbox(bboxes):
    return [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
            max(b[2] for b in bboxes), max(b[3] for b in bboxes)]


def overlaps_x(a, b, slack=0):
    return not (a[2] + slack < b[0] or b[2] + slack < a[0])


def group_glyphs_into_lines(glyphs, line_height_tol=6):
    """Group glyphs into text lines using vertical-center clustering.

    Each text line spans roughly 6–10pt vertically (cap height ≈ 7pt, with
    ascenders/descenders adding a few pt). We project glyphs onto y-center
    bins of size `line_height_tol` so that all glyphs of one visual line —
    regardless of whether they have descenders — fall in the same bin.
    """
    if not glyphs:
        return []
    # Sort by y-center (mid of bbox)
    sorted_g = sorted(glyphs, key=lambda g: ((g[1] + g[3]) / 2, g[0]))
    lines = []
    cur_glyphs = [sorted_g[0]]
    cur_center = (sorted_g[0][1] + sorted_g[0][3]) / 2
    for g in sorted_g[1:]:
        gc = (g[1] + g[3]) / 2
        if abs(gc - cur_center) <= line_height_tol / 2:
            cur_glyphs.append(g)
            # Keep cur_center stable: average ALL glyphs' centers
            cur_center = sum(((p[1] + p[3]) / 2) for p in cur_glyphs) / len(cur_glyphs)
        else:
            lines.append(union_bbox(cur_glyphs))
            cur_glyphs = [g]
            cur_center = gc
    lines.append(union_bbox(cur_glyphs))
    lines.sort(key=lambda b: b[1])
    return lines


def group_glyphs_per_column(glyphs, columns, baseline_tol=2):
    """Run line grouping separately within each column to avoid cross-column
    baseline merging."""
    out = {}
    for col_name, x0, x1 in columns:
        col_glyphs = [g for g in glyphs
                      if x0 <= (g[0] + g[2]) / 2 <= x1]
        out[col_name] = group_glyphs_into_lines(col_glyphs, baseline_tol)
    return out


def group_into_items(line_bboxes, separators_y, x_min, x_max):
    """Combine consecutive lines into items.

    A new item starts when:
      - the gap between line i and i+1 exceeds median_line_gap × 1.7, OR
      - there is a horizontal separator (y in `separators_y`) between them.
    """
    if not line_bboxes:
        return []

    # Filter lines to this column's x-range (by line center)
    in_col = []
    for b in line_bboxes:
        cx = (b[0] + b[2]) / 2
        if x_min <= cx <= x_max:
            in_col.append(b)
    if not in_col:
        return []

    # Compute typical line height & gap from this column's lines
    heights = [b[3] - b[1] for b in in_col]
    median_h = sorted(heights)[len(heights) // 2]
    gaps = []
    for i in range(len(in_col) - 1):
        gaps.append(in_col[i + 1][1] - in_col[i][3])
    if gaps:
        sorted_gaps = sorted(g for g in gaps if g > 0)
        median_gap = sorted_gaps[len(sorted_gaps) // 2] if sorted_gaps else median_h * 0.4
    else:
        median_gap = median_h * 0.4

    # Threshold: split when gap is more than 1.7× the typical line gap, OR
    # when a separator y lies between two lines.
    threshold = max(median_gap * 1.7, median_h * 0.7)

    items = []
    cur = [in_col[0]]
    for nxt in in_col[1:]:
        prev = cur[-1]
        gap = nxt[1] - prev[3]
        sep_between = any(prev[3] - 1 < sy < nxt[1] + 1 for sy in separators_y)
        if gap > threshold or sep_between:
            items.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    items.append(cur)

    return [union_bbox(group) for group in items]


def detect_separator_lines(paths):
    """Pull horizontal separator y-positions from path data.

    A horizontal separator is a row of `separator_dash` paths that lie on
    nearly-the-same y. We bin by integer y (pt) and call any y with >= 8
    dashes a separator line.
    """
    dashes = [p for p in paths if p["kind"] == "separator_dash"]
    from collections import Counter
    y_counter = Counter()
    for p in dashes:
        bb = p["bbox_pt"]
        cy = round((bb[1] + bb[3]) / 2)
        y_counter[cy] += 1
        # Also count adjacent y to handle slight misalignment
        y_counter[cy - 1] += 1
        y_counter[cy + 1] += 1
    sep_ys = sorted(y for y, c in y_counter.items() if c >= 8)
    # De-duplicate: keep only one entry per cluster of consecutive ys
    clean = []
    for y in sep_ys:
        if clean and y - clean[-1] <= 3:
            continue
        clean.append(y)
    return clean


def extract_columns(paths_pt):
    """Detect column x-ranges by looking at where text actually is.

    We project all glyph x-centers onto a histogram and identify valleys.
    For this 2-page document we just hardcode the columns since they are
    stable: page 1 has 3 columns, page 2 has 4 columns.
    """
    return None  # caller hardcodes


PAGE_COLUMNS = {
    # x-ranges in PDF pt (page = 1008×612), measured from glyph histograms.
    1: [
        ("left",   30, 335),
        ("center", 340, 660),
        ("right_l", 665, 755),       # MARS cycle labels + LSS rewards
        ("right_r", 755, 970),       # DISPLACEMENT body + Discovery tiles
    ],
    2: [
        ("exec", 30, 195),
        ("bp1",  200, 510),
        ("bp3",  515, 815),
        ("res",  820, 970),
    ],
}


def is_glyph(p):
    return p["kind"] in ("glyph_dark", "glyph_red", "glyph_teal") or (
        # Catch tiny "other" paths that are actually glyph fragments
        p["kind"] == "other" and p["type"] in ("f", "fs")
        and p["bbox_pt"][2] - p["bbox_pt"][0] < 25
        and p["bbox_pt"][3] - p["bbox_pt"][1] < 30
    )


def main() -> None:
    data = json.loads(PATHS_JSON.read_text())
    output = {}
    for pn in (1, 2):
        paths = data[str(pn)]
        glyphs = [p["bbox_pt"] for p in paths if is_glyph(p)]
        sep_ys = detect_separator_lines(paths)
        print(f"page {pn}: {len(glyphs)} glyph boxes, {len(sep_ys)} horizontal separators")
        print(f"  separator y-positions: {sep_ys[:30]}{'...' if len(sep_ys)>30 else ''}")

        lines_by_col = group_glyphs_per_column(glyphs, PAGE_COLUMNS[pn], baseline_tol=2)
        n_lines = sum(len(v) for v in lines_by_col.values())
        print(f"  → {n_lines} text lines across {len(lines_by_col)} columns")

        page_items = []
        for col_name, x0, x1 in PAGE_COLUMNS[pn]:
            col_lines = lines_by_col[col_name]
            items = group_into_items(col_lines, sep_ys, x0, x1)
            for i, bb in enumerate(items):
                page_items.append({
                    "id": f"p{pn}_{col_name}_{i+1:02d}",
                    "column": col_name,
                    "bbox_pt": [round(c, 2) for c in bb],
                    "bbox_px": [round(bb[0]*SCALE), round(bb[1]*SCALE),
                                round(bb[2]*SCALE), round(bb[3]*SCALE)],
                })
            print(f"  column {col_name}: {len(col_lines)} lines → {len(items)} items")
        output[str(pn)] = {"items": page_items, "separators_y": sep_ys}

    OUT_JSON.write_text(json.dumps(output, indent=2))
    print(f"\nsaved {OUT_JSON}")

    # Render overlay
    palette = [(0, 200, 0, 100), (50, 130, 255, 100), (255, 140, 0, 100), (220, 50, 220, 100)]
    for pn in (1, 2):
        img = Image.open(EN_HI[pn]).convert("RGBA")
        ovl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ovl)
        col_to_color = {col: palette[i % len(palette)] for i, (col, _, _) in enumerate(PAGE_COLUMNS[pn])}
        for it in output[str(pn)]["items"]:
            c = col_to_color[it["column"]]
            d.rectangle(it["bbox_px"], outline=c[:3] + (255,), width=4)
            d.rectangle(it["bbox_px"], fill=c)
        out = Image.alpha_composite(img, ovl)
        out.thumbnail((2400, 1700))
        out.convert("RGB").save(ROOT / f"out/verify/items_overlay_p{pn:02d}.png")
    print("overlays saved")


if __name__ == "__main__":
    main()
