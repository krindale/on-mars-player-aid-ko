"""Regenerate image regions in paragraphs_ko.json using OCR ground truth.

Algorithm:
  1. Run Tesseract + Vision OCR on EN page; union word bboxes -> precise text mask.
  2. Subtract text mask from EN ink mask -> non-text ink pixels (= illustrations).
  3. Connected component labeling -> each component is one image region candidate.
  4. Filter by size (skip tiny noise + overly huge clusters).
  5. Replace existing auto image regions in paragraphs_ko.json with these.
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

# Component filters
MIN_AREA = 200          # px² — drop tiny noise
MIN_DIM = 12
MAX_DIM = 700           # cap region size to avoid catching whole columns
PAD = 4                 # bbox padding

# Text-mask inflation around OCR word bboxes (anti-alias halo)
TEXT_INFLATE = 5

# White threshold
WHITE = 235


def main():
    with open(PARAGRAPHS) as f:
        para = json.load(f)

    doc = fitz.open(SRC_PDF)
    for spec in para["pages"]:
        pi = spec["page"]
        en_pix = doc[pi - 1].get_pixmap(dpi=DPI)
        en_img = Image.frombytes("RGB", (en_pix.width, en_pix.height), en_pix.samples)
        en_arr = np.array(en_img)
        H, W = en_arr.shape[:2]

        # OCR
        print(f"[page {pi}] OCR...")
        t = ocr_tesseract(en_img)
        v = ocr_vision(en_img)
        words = union_words(t, v)
        print(f"  union={len(words)} words")

        # Build inflated text mask from OCR word bboxes
        text_mask = np.zeros((H, W), dtype=bool)
        for w in words:
            x0, y0, x1, y1 = w["bbox"]
            x0 = max(0, x0 - TEXT_INFLATE); y0 = max(0, y0 - TEXT_INFLATE)
            x1 = min(W, x1 + TEXT_INFLATE); y1 = min(H, y1 + TEXT_INFLATE)
            text_mask[y0:y1, x0:x1] = True

        # Non-text ink = image candidate pixels
        en_lum = en_arr.mean(axis=2)
        ink = en_lum < WHITE
        image_pix = ink & ~text_mask

        # Connected component
        # Use 8-connectivity + small dilation to merge close fragments
        merged = ndimage.binary_dilation(image_pix, iterations=2)
        labels, n = ndimage.label(merged)
        print(f"  connected components: {n}")

        # Build regions
        regions = []
        for k in range(1, n + 1):
            mask_k = labels == k
            ys, xs = np.where(mask_k)
            if len(xs) < MIN_AREA: continue
            x0, y0 = xs.min(), ys.min()
            x1, y1 = xs.max(), ys.max()
            w = x1 - x0; h = y1 - y0
            if w < MIN_DIM or h < MIN_DIM: continue
            if w > MAX_DIM or h > MAX_DIM:
                # skip — likely a merged column-spanning blob
                continue
            x0p = max(0, x0 - PAD); y0p = max(0, y0 - PAD)
            x1p = min(W, x1 + PAD); y1p = min(H, y1 + PAD)
            regions.append({
                "id": f"p{pi}_img_ocr_{len(regions)+1:03d}",
                "type": "image",
                "bbox": [int(x0p), int(y0p), int(x1p), int(y1p)],
            })
        print(f"  -> {len(regions)} image regions (after filters)")

        # Replace existing image regions
        spec["regions"] = [r for r in spec["regions"] if r.get("type") != "image"]
        spec["regions"].extend(regions)

    doc.close()

    with open(PARAGRAPHS, "w", encoding="utf-8") as f:
        json.dump(para, f, ensure_ascii=False, indent=2)
    print(f"\n[ok] updated {PARAGRAPHS}")


if __name__ == "__main__":
    main()
