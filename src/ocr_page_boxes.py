"""OCR-based detection of text regions on page 1 (or any page).

Pipeline:
  1. Render PDF page at 300 DPI.
  2. OCR with Tesseract (word-level) and macOS Vision (line-level).
  3. Cluster Tesseract words into lines, then lines into blocks (paragraphs).
  4. Compare detected blocks vs current paragraphs_p{N}.json text regions.
  5. Produce overlay PNG + JSON report.

Outputs:
  out/verify/ocr_pN/page0N_ocr_blocks.png    overlay (red=Tess, blue=Vision, yellow=current)
  out/verify/ocr_pN/page0N_ocr_blocks.json   raw block bboxes + texts
  out/verify/ocr_pN/page0N_compare.json      missing / extra / matched regions
  out/verify/ocr_pN/page0N_compare.md        human-readable summary
"""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import fitz

ROOT = Path(__file__).resolve().parent.parent
SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
DPI = 300
MIN_CONF = 30


def render_page(page_idx: int) -> Image.Image:
    doc = fitz.open(SRC_PDF)
    pix = doc[page_idx - 1].get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def ocr_tesseract(img: Image.Image) -> list[dict]:
    import pytesseract
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang="eng")
    out = []
    for i in range(len(data["text"])):
        t = data["text"][i].strip()
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if not t or conf < MIN_CONF:
            continue
        x = int(data["left"][i]); y = int(data["top"][i])
        w = int(data["width"][i]); h = int(data["height"][i])
        out.append({"text": t, "bbox": [x, y, x + w, y + h], "conf": conf})
    return out


def ocr_vision(img: Image.Image) -> list[dict]:
    from Foundation import NSData
    import Vision, Quartz
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    nsdata = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    src = Quartz.CGImageSourceCreateWithData(nsdata, None)
    cgimg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W = Quartz.CGImageGetWidth(cgimg); H = Quartz.CGImageGetHeight(cgimg)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimg, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(["en-US"])
    handler.performRequests_error_([req], None)
    out = []
    for obs in (req.results() or []):
        text = str(obs.text())
        conf = float(obs.confidence()) * 100
        bb = obs.boundingBox()
        x0 = int(bb.origin.x * W)
        y0 = int((1 - bb.origin.y - bb.size.height) * H)
        x1 = int((bb.origin.x + bb.size.width) * W)
        y1 = int((1 - bb.origin.y) * H)
        out.append({"text": text, "bbox": [x0, y0, x1, y1], "conf": conf})
    return out


def cluster_words_to_lines(words: list[dict], y_tol: int = 10) -> list[list[dict]]:
    if not words: return []
    words = sorted(words, key=lambda w: (w["bbox"][1], w["bbox"][0]))
    lines = []
    cur, cur_cy = [], None
    for w in words:
        cy = (w["bbox"][1] + w["bbox"][3]) // 2
        if cur_cy is None or abs(cy - cur_cy) <= y_tol:
            cur.append(w); cur_cy = cy if cur_cy is None else (cur_cy + cy) // 2
        else:
            lines.append(cur); cur = [w]; cur_cy = cy
    if cur: lines.append(cur)
    return lines


def line_bbox(line: list[dict]) -> list[int]:
    x0 = min(w["bbox"][0] for w in line)
    y0 = min(w["bbox"][1] for w in line)
    x1 = max(w["bbox"][2] for w in line)
    y1 = max(w["bbox"][3] for w in line)
    return [x0, y0, x1, y1]


def cluster_lines_to_blocks(lines: list[list[dict]], y_gap_factor: float = 1.2,
                            x_overlap_min: float = 0.20) -> list[dict]:
    """Group adjacent lines into blocks. Threshold = y_gap_factor * line_height."""
    if not lines: return []
    line_objs = []
    for L in lines:
        bb = line_bbox(L); h = bb[3] - bb[1]
        text = " ".join(w["text"] for w in L)
        line_objs.append({"bbox": bb, "h": h, "text": text, "words": L})
    line_objs.sort(key=lambda l: l["bbox"][1])
    blocks = [[line_objs[0]]]
    for L in line_objs[1:]:
        prev = blocks[-1][-1]
        gap = L["bbox"][1] - prev["bbox"][3]
        avg_h = (L["h"] + prev["h"]) / 2
        # X overlap relative to narrower line
        xo = max(0, min(L["bbox"][2], prev["bbox"][2]) - max(L["bbox"][0], prev["bbox"][0]))
        narrow_w = max(1, min(L["bbox"][2] - L["bbox"][0], prev["bbox"][2] - prev["bbox"][0]))
        ov = xo / narrow_w
        if gap <= avg_h * y_gap_factor and ov >= x_overlap_min:
            blocks[-1].append(L)
        else:
            blocks.append([L])
    out = []
    for B in blocks:
        x0 = min(L["bbox"][0] for L in B)
        y0 = min(L["bbox"][1] for L in B)
        x1 = max(L["bbox"][2] for L in B)
        y1 = max(L["bbox"][3] for L in B)
        out.append({"bbox": [x0, y0, x1, y1], "text": " | ".join(L["text"] for L in B), "n_lines": len(B)})
    return out


def iou(a: list[int], b: list[int]) -> float:
    ax0, ay0, ax1, ay1 = a; bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0); iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1); iy1 = min(ay1, by1)
    if ix0 >= ix1 or iy0 >= iy1: return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / max(1, union)


def contained(small: list[int], big: list[int], tol: int = 8) -> bool:
    return (small[0] >= big[0] - tol and small[1] >= big[1] - tol
            and small[2] <= big[2] + tol and small[3] <= big[3] + tol)


def compare(blocks: list[dict], regions: list[dict]) -> dict:
    """Each OCR block vs current text regions: matched / missing.
       Each current region: matched / extra (no OCR overlap)."""
    text_regs = [r for r in regions if r.get("type", "text") == "text" and r.get("mask_bbox")]
    img_regs = [r for r in regions if r.get("type") == "image" and r.get("bbox")]

    block_status = []
    for b in blocks:
        # Skip blocks fully inside an image region (likely OCR'd icon labels)
        in_image = any(contained(b["bbox"], r["bbox"]) for r in img_regs)
        # Find best matching text region by IoU
        best, best_iou = None, 0.0
        for r in text_regs:
            v = iou(b["bbox"], r["mask_bbox"])
            if v > best_iou: best, best_iou = r, v
        status = "matched" if best_iou >= 0.20 else ("in_image" if in_image else "missing")
        block_status.append({
            "bbox": b["bbox"], "text": b["text"][:80], "status": status,
            "matched_id": best["id"] if best and best_iou >= 0.20 else None,
            "iou": round(best_iou, 3),
        })

    region_status = []
    for r in text_regs:
        best_iou = 0.0
        for b in blocks:
            v = iou(b["bbox"], r["mask_bbox"])
            if v > best_iou: best_iou = v
        region_status.append({
            "id": r["id"], "mask_bbox": r["mask_bbox"], "text": (r.get("text") or "")[:50],
            "status": "matched" if best_iou >= 0.20 else "no_ocr",
            "iou": round(best_iou, 3),
        })

    return {"blocks": block_status, "regions": region_status}


def draw_overlay(img: Image.Image, tess_blocks, vis_blocks, regions, img_regions, out_path: Path):
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
    except Exception:
        f = ImageFont.load_default()
    # Current text regions (yellow)
    for r in regions:
        x0, y0, x1, y1 = r["mask_bbox"]
        d.rectangle([x0, y0, x1, y1], outline=(220, 200, 0, 255), width=3)
        d.text((x0, y1 + 2), r["id"], fill=(150, 130, 0, 255), font=f)
    # Current image regions (light green outline only, for context)
    for r in img_regions:
        x0, y0, x1, y1 = r["bbox"]
        d.rectangle([x0, y0, x1, y1], outline=(0, 200, 0, 130), width=1)
    # Tess blocks (red)
    for i, b in enumerate(tess_blocks, 1):
        x0, y0, x1, y1 = b["bbox"]
        d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0, 255), width=2)
        d.text((x0, y0 - 18), f"TB{i:02d}", fill=(255, 0, 0, 255), font=f)
    # Vision blocks (blue)
    for i, b in enumerate(vis_blocks, 1):
        x0, y0, x1, y1 = b["bbox"]
        d.rectangle([x0, y0, x1, y1], outline=(0, 100, 255, 255), width=2)
        d.text((x1 - 60, y1 + 2), f"VB{i:02d}", fill=(0, 100, 255, 255), font=f)
    out = Image.alpha_composite(base, layer).convert("RGB")
    out.save(out_path, "PNG", optimize=True)


def main(page_idx: int = 1):
    OUT = ROOT / "out" / "verify" / f"ocr_p{page_idx}"
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[page {page_idx}] render at {DPI} DPI...")
    img = render_page(page_idx)
    print(f"  size = {img.size}")

    print("Tesseract OCR...")
    tess_words = ocr_tesseract(img)
    print(f"  {len(tess_words)} words")
    tess_lines = cluster_words_to_lines(tess_words)
    tess_blocks = cluster_lines_to_blocks(tess_lines)
    print(f"  → {len(tess_lines)} lines, {len(tess_blocks)} blocks")

    print("Vision OCR...")
    vis_lines_raw = ocr_vision(img)
    print(f"  {len(vis_lines_raw)} lines")
    # Treat each Vision line as a one-element line, then cluster vertically
    fake_lines = [[{"text": L["text"], "bbox": L["bbox"]}] for L in vis_lines_raw]
    vis_blocks = cluster_lines_to_blocks(fake_lines)
    print(f"  → {len(vis_blocks)} blocks")

    # Load current regions
    para = json.load(open(ROOT / f"data/paragraphs_p{page_idx}.json", encoding="utf-8"))
    text_regs = [r for r in para["regions"] if r.get("type", "text") == "text" and r.get("mask_bbox")]
    img_regs = [r for r in para["regions"] if r.get("type") == "image" and r.get("bbox")]
    print(f"current regions: {len(text_regs)} text, {len(img_regs)} image")

    # Compare (use Tesseract blocks as reference)
    cmp_tess = compare(tess_blocks, para["regions"])
    cmp_vis = compare(vis_blocks, para["regions"])

    # Save raw + comparison
    json.dump(
        {"tess_blocks": tess_blocks, "vis_blocks": vis_blocks},
        open(OUT / f"page0{page_idx}_ocr_blocks.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )
    json.dump(
        {"tess": cmp_tess, "vis": cmp_vis},
        open(OUT / f"page0{page_idx}_compare.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )

    # Markdown summary
    def write_md():
        lines = [f"# Page {page_idx} OCR vs Current Regions\n"]
        for label, cmp in (("Tesseract", cmp_tess), ("Vision", cmp_vis)):
            lines.append(f"\n## {label} blocks ({len(cmp['blocks'])})\n")
            mc = sum(1 for b in cmp["blocks"] if b["status"] == "matched")
            mi = sum(1 for b in cmp["blocks"] if b["status"] == "missing")
            ii = sum(1 for b in cmp["blocks"] if b["status"] == "in_image")
            lines.append(f"- matched={mc}  missing={mi}  in_image={ii}")
            if mi:
                lines.append("\n### MISSING blocks (no current text region)\n")
                for b in cmp["blocks"]:
                    if b["status"] == "missing":
                        lines.append(f"- bbox={b['bbox']}  iou={b['iou']}  text=`{b['text']}`")
        # extras (current region but no OCR)
        no_ocr = [r for r in cmp_tess["regions"] if r["status"] == "no_ocr"]
        lines.append(f"\n## Current text regions with NO Tess OCR overlap ({len(no_ocr)})\n")
        for r in no_ocr:
            lines.append(f"- {r['id']} bbox={r['mask_bbox']} text=`{r['text']}`")
        (OUT / f"page0{page_idx}_compare.md").write_text("\n".join(lines), encoding="utf-8")
    write_md()

    # Overlay PNG (use Tess blocks + current regions)
    overlay_path = OUT / f"page0{page_idx}_ocr_blocks.png"
    draw_overlay(img, tess_blocks, vis_blocks, text_regs, img_regs, overlay_path)
    print(f"\n✓ overlay: {overlay_path}")
    print(f"✓ json:    {OUT/f'page0{page_idx}_ocr_blocks.json'}")
    print(f"✓ compare: {OUT/f'page0{page_idx}_compare.md'}")


if __name__ == "__main__":
    page = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    main(page)
