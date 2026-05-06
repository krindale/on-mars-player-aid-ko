"""Rebuild ONLY the BLUEPRINT 1/3 image regions on page 2.

Other regions stay untouched.

BP rows have a fixed structure:
  [LEFT large round illustration] [number + title]
                                  [small resource icons + text]
                                  Advanced Building Action: ...
                                  For each Boost: ...

Algorithm:
  1. For each BP column (BP1, BP3), find row gaps automatically (y-projection).
  2. For each row:
     - LEFT illustration = leftmost connected component (~80-110px wide)
     - Small inline icons = saturated-color components within the row, NOT
       inside an OCR text bbox.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from PIL import Image
import fitz
from scipy import ndimage

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
PARAGRAPHS = ROOT / "data" / "paragraphs_ko.json"
OCR_WORDS = ROOT / "out" / "verify" / "ocr_audit" / "page02_words.json"
DPI = 300
WHITE = 235

# BP column boundaries (measured from page 2 visual inspection)
BP_COLUMNS = {
    "BP1": (760, 2090),   # LEVEL 1 (rows 1-12)
    "BP3": (2090, 3460),  # LEVEL 3 (rows 13-24)
}
BP_Y_RANGE = (140, 2440)
LEFT_ILLUSTRATION_W = 130     # leftmost ~130px of each row holds the big round icon
ICON_PAD = 4
INLINE_DILATE = 2
INLINE_SAT_MIN = 20           # lower threshold so dim teal/purple icons get caught
INLINE_TEXT_INFLATE = 1       # tighter text exclusion -> more icon candidates kept


def find_row_starts_from_ocr(words, cx0, cx1, expected_numbers):
    """Find row y-start positions using OCR-detected BP number words ("1.", "2.", ...).

    Each BP row begins with its number followed by a period. We locate the
    leftmost word in the column for each expected number and use its y as the
    row boundary.
    """
    starts = {}
    for n in expected_numbers:
        candidates = []
        for w in words:
            wb = w["bbox"]
            cx = (wb[0] + wb[2]) / 2
            if not (cx0 <= cx <= cx0 + 250): continue  # number is in leftmost 250 px after large icon
            txt = w["text"].strip().rstrip(".").rstrip(",")
            if txt == str(n) or txt == f"{n}.":
                candidates.append(w)
        if candidates:
            # Take leftmost
            candidates.sort(key=lambda w: w["bbox"][0])
            starts[n] = candidates[0]["bbox"][1] - 8  # padding above
    return starts


def main():
    with open(PARAGRAPHS) as f: para = json.load(f)
    with open(OCR_WORDS) as f: ocr = json.load(f)
    words = ocr["words"]

    doc = fitz.open(SRC_PDF)
    en = np.array(Image.frombytes('RGB',
        (doc[1].get_pixmap(dpi=DPI).width, doc[1].get_pixmap(dpi=DPI).height),
        doc[1].get_pixmap(dpi=DPI).samples))
    H, W = en.shape[:2]
    ink = en.mean(axis=2) < WHITE
    sat = en.max(axis=2).astype(int) - en.min(axis=2).astype(int)
    colored = sat > INLINE_SAT_MIN

    # OCR text mask (tight)
    text_mask = np.zeros((H, W), dtype=bool)
    for w in words:
        x0, y0, x1, y1 = w["bbox"]
        x0 = max(0, x0-INLINE_TEXT_INFLATE); y0 = max(0, y0-INLINE_TEXT_INFLATE)
        x1 = min(W, x1+INLINE_TEXT_INFLATE); y1 = min(H, y1+INLINE_TEXT_INFLATE)
        text_mask[y0:y1, x0:x1] = True

    # Per-column expected BP numbers
    BP_NUMBERS = {"BP1": list(range(1, 13)), "BP3": list(range(13, 25))}

    new_regions = []

    for col_name, (cx0, cx1) in BP_COLUMNS.items():
        nums = BP_NUMBERS[col_name]
        starts = find_row_starts_from_ocr(words, cx0, cx1, nums)
        # Row bounds: each row from this number's y to the next number's y (or BP_Y_RANGE end)
        sorted_n = sorted(starts.keys())
        row_bounds = []
        for i, n in enumerate(sorted_n):
            ry0 = starts[n]
            ry1 = starts[sorted_n[i+1]] - 4 if i + 1 < len(sorted_n) else BP_Y_RANGE[1]
            row_bounds.append((max(BP_Y_RANGE[0], ry0), min(BP_Y_RANGE[1], ry1)))
        missing = [n for n in nums if n not in starts]
        print(f"{col_name}: {len(row_bounds)}/{len(nums)} rows detected (missing #s: {missing})")

        for ri, (ry0, ry1) in enumerate(row_bounds, start=1):
            row_id_prefix = f"p2_{col_name.lower()}_row{ri:02d}"

            # 1. LEFT illustration: leftmost connected component within row.
            #    Look at colored pixels in column [cx0, cx0 + LEFT_ILLUSTRATION_W*2]
            left_ink = ink[ry0:ry1, cx0:cx0 + LEFT_ILLUSTRATION_W * 2]
            left_dilated = ndimage.binary_dilation(left_ink, iterations=3)
            labels, n = ndimage.label(left_dilated)
            best = None  # smallest x, biggest area
            for k in range(1, n + 1):
                ys, xs = np.where(labels == k)
                if len(xs) < 200: continue
                lx0, lx1 = int(xs.min()), int(xs.max())
                ly0, ly1 = int(ys.min()), int(ys.max())
                w_ = lx1 - lx0; h_ = ly1 - ly0
                if w_ < 20 or h_ < 20: continue
                if w_ > 200 or h_ > 200: continue
                if best is None or lx0 < best["x0"]:
                    best = {"x0": lx0, "y0": ly0, "x1": lx1, "y1": ly1}
            if best:
                bx0 = cx0 + best["x0"] - ICON_PAD
                by0 = ry0 + best["y0"] - ICON_PAD
                bx1 = cx0 + best["x1"] + ICON_PAD
                by1 = ry0 + best["y1"] + ICON_PAD
                new_regions.append({
                    "id": f"{row_id_prefix}_main",
                    "type": "image",
                    "bbox": [bx0, by0, bx1, by1],
                })

            # 2. Inline icons in the rest of the row (right of LEFT illustration).
            #    Take colored pixels NOT inside any OCR text bbox.
            ix0 = cx0 + LEFT_ILLUSTRATION_W
            row_colored = colored[ry0:ry1, ix0:cx1] & ~text_mask[ry0:ry1, ix0:cx1]
            row_dilated = ndimage.binary_dilation(row_colored, iterations=INLINE_DILATE)
            labels2, n2 = ndimage.label(row_dilated)
            inline_count = 0
            for k in range(1, n2 + 1):
                ys, xs = np.where(labels2 == k)
                if len(xs) < 80: continue
                lx0, lx1 = int(xs.min()), int(xs.max())
                ly0, ly1 = int(ys.min()), int(ys.max())
                w_ = lx1 - lx0; h_ = ly1 - ly0
                if w_ < 14 or h_ < 14: continue
                if w_ > 80 or h_ > 80: continue
                bx0 = ix0 + lx0 - ICON_PAD
                by0 = ry0 + ly0 - ICON_PAD
                bx1 = ix0 + lx1 + ICON_PAD
                by1 = ry0 + ly1 + ICON_PAD
                inline_count += 1
                new_regions.append({
                    "id": f"{row_id_prefix}_inline_{inline_count:02d}",
                    "type": "image",
                    "bbox": [bx0, by0, bx1, by1],
                })
            print(f"  row{ri:02d}: y={ry0}-{ry1}  main={'yes' if best else 'NO'}  inline={inline_count}")

    print(f"\nTotal new BP image regions: {len(new_regions)}")

    # Replace BP-area image regions in paragraphs_ko.json page 2
    p2 = para["pages"][1]
    def is_in_bp_area(r):
        if r.get("type") != "image": return False
        bbox = r.get("bbox", [0,0,0,0])
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        if not (BP_Y_RANGE[0] <= cy <= BP_Y_RANGE[1]): return False
        for cx0, cx1 in BP_COLUMNS.values():
            if cx0 <= cx <= cx1: return True
        return False
    n_old = sum(1 for r in p2["regions"] if is_in_bp_area(r))
    p2["regions"] = [r for r in p2["regions"] if not is_in_bp_area(r)]
    p2["regions"].extend(new_regions)
    print(f"Replaced {n_old} BP image regions with {len(new_regions)}")

    doc.close()
    with open(PARAGRAPHS, "w", encoding="utf-8") as f:
        json.dump(para, f, ensure_ascii=False, indent=2)
    print(f"[ok] wrote {PARAGRAPHS}")


if __name__ == "__main__":
    main()
