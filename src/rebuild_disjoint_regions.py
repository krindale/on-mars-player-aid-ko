"""Rebuild text + image regions accurately.

Algorithm:
  1. OCR word bboxes (Tesseract + Vision union)
  2. For each text region: shrink mask_bbox to the EXACT envelope of OCR words
     that fall inside it (with small padding). This produces precise text bboxes
     that don't overflow into icon space.
  3. image_pix = EN ink AND NOT inside any updated text mask_bbox
  4. image regions = connected components of image_pix (dilate + cluster)
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
from audit_with_ocr import ocr_tesseract, ocr_vision, union_words

SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
PARAGRAPHS = ROOT / "data" / "paragraphs_ko.json"
DPI = 300

WHITE = 235
TEXT_PAD = 12
IMG_DILATE = 2         # smaller — don't fuse neighbors that should stay separate
IMG_GAP = 6            # tight cluster
MIN_IMG_AREA = 150
MIN_IMG_DIM = 14
MAX_IMG_DIM = 1500     # large — we'll auto-split by internal separators
SPLIT_GAP = 5          # rows/cols with < this many ink pixels = potential separator
SPLIT_RUN = 22         # consecutive separator rows/cols = real split (BP row gap ~25px)
MIN_SUB_DIM = 35       # don't split if either resulting sub is smaller than this
PAD = 3


def word_in_bbox(word, bbox):
    """True if word center is inside bbox."""
    wb = word["bbox"]
    cx = (wb[0] + wb[2]) / 2; cy = (wb[1] + wb[3]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def envelope(words, pad=TEXT_PAD):
    if not words:
        return None
    x0 = min(w["bbox"][0] for w in words) - pad
    y0 = min(w["bbox"][1] for w in words) - pad
    x1 = max(w["bbox"][2] for w in words) + pad
    y1 = max(w["bbox"][3] for w in words) + pad
    return [int(x0), int(y0), int(x1), int(y1)]


def split_bbox_by_gaps(bbox: list[int], image_pix: np.ndarray) -> list[list[int]]:
    """Recursively split bbox along rows/columns where image_pix has long
    empty runs (>= SPLIT_RUN consecutive lines with < SPLIT_GAP ink pixels).

    BLUEPRINT 12-row grid + EXEC 7-card column both have whitespace gutters
    between cells. This finds them and splits the giant connected component
    into per-cell bboxes automatically.
    """
    x0, y0, x1, y1 = bbox
    H, W = image_pix.shape
    x0 = max(0, x0); y0 = max(0, y0); x1 = min(W, x1); y1 = min(H, y1)
    if x1 - x0 < 30 or y1 - y0 < 30:
        return [[x0, y0, x1, y1]]
    crop = image_pix[y0:y1, x0:x1]
    h, w = crop.shape

    def find_split_lines(line_sums: np.ndarray) -> list[int]:
        """Return midpoints of empty-run separators in line_sums."""
        empty = line_sums < SPLIT_GAP
        runs = []
        i = 0
        while i < len(empty):
            if empty[i]:
                j = i
                while j < len(empty) and empty[j]:
                    j += 1
                if j - i >= SPLIT_RUN:
                    # Don't split at the very edge
                    if i > 5 and j < len(empty) - 5:
                        runs.append((i + j) // 2)
                i = j
            else:
                i += 1
        return runs

    # Try horizontal split first (split by rows = produces row-stacked sub-bboxes)
    row_sums = crop.sum(axis=1)
    row_splits = [s for s in find_split_lines(row_sums)
                  if s >= MIN_SUB_DIM and (h - s) >= MIN_SUB_DIM]
    if row_splits:
        sub = []
        prev = 0
        for s in row_splits + [h]:
            if s - prev < MIN_SUB_DIM:
                continue
            sub_bbox = [x0, y0 + prev, x1, y0 + s]
            sub.extend(split_bbox_by_gaps(sub_bbox, image_pix))
            prev = s
        return sub if sub else [[x0, y0, x1, y1]]

    # Then column split
    col_sums = crop.sum(axis=0)
    col_splits = [s for s in find_split_lines(col_sums)
                  if s >= MIN_SUB_DIM and (w - s) >= MIN_SUB_DIM]
    if col_splits:
        sub = []
        prev = 0
        for s in col_splits + [w]:
            if s - prev < MIN_SUB_DIM:
                continue
            sub_bbox = [x0 + prev, y0, x0 + s, y1]
            sub.extend(split_bbox_by_gaps(sub_bbox, image_pix))
            prev = s
        return sub if sub else [[x0, y0, x1, y1]]

    # No more splits possible
    return [[x0, y0, x1, y1]]


def cluster_bboxes(items, gap):
    """Greedy gap-based clustering of bboxes."""
    if not items: return []
    items = sorted(items, key=lambda b: (b[1], b[0]))
    clusters = []
    for it in items:
        x0, y0, x1, y1 = it
        merged = False
        for c in clusters:
            cx0, cy0, cx1, cy1 = c
            if (x0 <= cx1 + gap and x1 >= cx0 - gap and
                y0 <= cy1 + gap and y1 >= cy0 - gap):
                c[0] = min(cx0, x0); c[1] = min(cy0, y0)
                c[2] = max(cx1, x1); c[3] = max(cy1, y1)
                merged = True
                break
        if not merged:
            clusters.append(list(it))
    # Multi-pass merge until stable
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            if clusters[i] is None: continue
            for j in range(i + 1, len(clusters)):
                if clusters[j] is None: continue
                ax0, ay0, ax1, ay1 = clusters[i]
                bx0, by0, bx1, by1 = clusters[j]
                if (bx0 <= ax1 + gap and bx1 >= ax0 - gap and
                    by0 <= ay1 + gap and by1 >= ay0 - gap):
                    clusters[i] = [min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)]
                    clusters[j] = None
                    changed = True
        clusters = [c for c in clusters if c is not None]
    return clusters


def main():
    with open(PARAGRAPHS) as f: para = json.load(f)
    doc = fitz.open(SRC_PDF)

    for spec in para["pages"]:
        pi = spec["page"]
        en_pix = doc[pi - 1].get_pixmap(dpi=DPI)
        en_img = Image.frombytes("RGB", (en_pix.width, en_pix.height), en_pix.samples)
        en_arr = np.array(en_img)
        H, W = en_arr.shape[:2]

        print(f"[page {pi}] OCR...")
        words = union_words(ocr_tesseract(en_img), ocr_vision(en_img))
        print(f"  words={len(words)}")

        # Drop existing image regions; keep text regions for fitting
        text_regions = [r for r in spec["regions"] if r.get("type", "text") == "text"]

        # 1. Fit each text region's mask_bbox to OCR word envelope inside it
        n_fitted = 0
        for r in text_regions:
            mb = r.get("mask_bbox")
            if not mb or mb == [0, 0, 0, 0]: continue
            inside = [w for w in words if word_in_bbox(w, mb)]
            if not inside:
                # No words inside — region is heading-only or degenerate; keep as-is
                continue
            new_mb = envelope(inside)
            if new_mb:
                # Constrain inside the original mask_bbox (so we don't grow into neighbors)
                new_mb = [
                    max(new_mb[0], mb[0]), max(new_mb[1], mb[1]),
                    min(new_mb[2], mb[2]), min(new_mb[3], mb[3]),
                ]
                if new_mb != mb:
                    r["mask_bbox"] = new_mb
                    n_fitted += 1
        print(f"  text mask_bbox fitted to OCR: {n_fitted} regions")

        # 2. Build text union mask (from updated mask_bboxes) — these areas
        #    will be subtracted from EN ink to find image pixels.
        text_mask = np.zeros((H, W), dtype=bool)
        for r in text_regions:
            mb = r.get("mask_bbox")
            if not mb or mb == [0, 0, 0, 0]: continue
            x0, y0, x1, y1 = mb
            x0 = max(0, x0); y0 = max(0, y0); x1 = min(W, x1); y1 = min(H, y1)
            if x1 > x0 and y1 > y0:
                text_mask[y0:y1, x0:x1] = True

        # 3. image_pix = ink AND NOT text
        en_lum = en_arr.mean(axis=2)
        ink = en_lum < WHITE
        image_pix = ink & ~text_mask

        # 4. Image regions: dilate + connected component, then split big CCs by internal gaps
        merged = ndimage.binary_dilation(image_pix, iterations=IMG_DILATE)
        labels, n = ndimage.label(merged)
        cc_bboxes = []
        for k in range(1, n + 1):
            ys, xs = np.where(labels == k)
            if len(xs) < MIN_IMG_AREA: continue
            x0, y0 = int(xs.min()), int(ys.min())
            x1, y1 = int(xs.max()), int(ys.max())
            w_, h_ = x1 - x0, y1 - y0
            if w_ < MIN_IMG_DIM or h_ < MIN_IMG_DIM: continue
            cc_bboxes.append([x0, y0, x1, y1])

        # Split each CC by internal whitespace separators (handles BP grid + EXEC column)
        split_bboxes = []
        for bb in cc_bboxes:
            split_bboxes.extend(split_bbox_by_gaps(bb, image_pix))

        new_img_regions = []
        for x0, y0, x1, y1 in split_bboxes:
            w_, h_ = x1 - x0, y1 - y0
            if w_ < MIN_IMG_DIM or h_ < MIN_IMG_DIM: continue
            if w_ > MAX_IMG_DIM or h_ > MAX_IMG_DIM: continue
            x0 = max(0, x0 - PAD); y0 = max(0, y0 - PAD)
            x1 = min(W, x1 + PAD); y1 = min(H, y1 + PAD)
            new_img_regions.append({
                "id": f"p{pi}_img_{len(new_img_regions)+1:03d}",
                "type": "image",
                "bbox": [x0, y0, x1, y1],
            })
        print(f"  -> {len(cc_bboxes)} CCs -> {len(split_bboxes)} split -> {len(new_img_regions)} image regions")

        spec["regions"] = text_regions + new_img_regions

    doc.close()
    with open(PARAGRAPHS, "w", encoding="utf-8") as f:
        json.dump(para, f, ensure_ascii=False, indent=2)
    print(f"\n[ok] wrote {PARAGRAPHS}")


if __name__ == "__main__":
    main()
