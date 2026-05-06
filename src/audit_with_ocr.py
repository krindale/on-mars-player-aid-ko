"""OCR-based box accuracy audit.

Runs Tesseract + macOS Vision on each EN page, unions detected word bboxes,
then classifies each word against the regions in paragraphs_ko.json:

  - inside a text region's mask_bbox  -> ✅ CORRECT (text will be masked)
  - inside an image region's bbox     -> ❌ WRONG (text protected as image)
  - in no region                       -> ❌ WRONG (text not masked, leaks through)

Outputs:
  out/verify/ocr_audit/page0{N}_ocr_overlay.png   colored word overlay
  out/verify/ocr_audit/page0{N}_defects.json      list of problem words
  out/verify/ocr_audit/summary.md                  per-page tally
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import fitz

ROOT = Path(__file__).resolve().parent.parent
SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
PARAGRAPHS = ROOT / "data" / "paragraphs_ko.json"
OUT = ROOT / "out" / "verify" / "ocr_audit"
OUT.mkdir(parents=True, exist_ok=True)
DPI = 300

MIN_CONF = 30  # ignore words with conf below this


# ---------- OCR backends ----------
def ocr_tesseract(img: Image.Image) -> list[dict]:
    import pytesseract
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang="eng")
    out = []
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if not text or conf < MIN_CONF:
            continue
        x = int(data["left"][i]); y = int(data["top"][i])
        w = int(data["width"][i]); h = int(data["height"][i])
        out.append({"text": text, "bbox": [x, y, x + w, y + h], "conf": conf, "src": "tess"})
    return out


def ocr_vision(img: Image.Image) -> list[dict]:
    """macOS Vision OCR via PyObjC."""
    import io
    import objc
    from Foundation import NSData
    import Vision
    import Quartz

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    nsdata = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    src = Quartz.CGImageSourceCreateWithData(nsdata, None)
    cgimg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W = Quartz.CGImageGetWidth(cgimg)
    H = Quartz.CGImageGetHeight(cgimg)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimg, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    request.setRecognitionLanguages_(["en-US"])
    handler.performRequests_error_([request], None)
    out = []
    for obs in (request.results() or []):
        text = str(obs.text())
        conf = float(obs.confidence()) * 100
        # Vision returns bbox in normalized [0,1] with origin at bottom-left
        bbox = obs.boundingBox()
        x = bbox.origin.x; y = bbox.origin.y
        w = bbox.size.width; h = bbox.size.height
        # Convert to pixel coords with origin at top-left
        px0 = int(x * W); px1 = int((x + w) * W)
        py1 = int((1 - y) * H); py0 = int((1 - y - h) * H)
        # Vision returns one bbox per text observation (often multi-word).
        # Split by whitespace to get rough per-word bboxes (proportional to text length).
        words = text.split()
        if not words:
            continue
        word_w = (px1 - px0) / max(1, len(text))
        cursor = px0
        for w_text in words:
            ww = int(word_w * len(w_text))
            wbbox = [cursor, py0, cursor + ww, py1]
            cursor += ww + int(word_w)  # +space
            out.append({"text": w_text, "bbox": wbbox, "conf": conf, "src": "vision"})
    return out


def union_words(tess: list[dict], vision: list[dict]) -> list[dict]:
    """Merge word lists by spatial overlap. Same word from both sources -> merged."""
    out = []
    used_v = [False] * len(vision)
    for tw in tess:
        tbbox = tw["bbox"]
        # Find overlapping vision word
        match = None
        for j, vw in enumerate(vision):
            if used_v[j]:
                continue
            vbbox = vw["bbox"]
            if (tbbox[2] >= vbbox[0] and tbbox[0] <= vbbox[2] and
                tbbox[3] >= vbbox[1] and tbbox[1] <= vbbox[3]):
                # overlap
                ix0 = max(tbbox[0], vbbox[0]); iy0 = max(tbbox[1], vbbox[1])
                ix1 = min(tbbox[2], vbbox[2]); iy1 = min(tbbox[3], vbbox[3])
                area_i = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                area_t = (tbbox[2] - tbbox[0]) * (tbbox[3] - tbbox[1])
                if area_i >= 0.3 * area_t:
                    match = j
                    break
        if match is not None:
            vw = vision[match]
            used_v[match] = True
            out.append({"text": tw["text"], "bbox": [
                min(tw["bbox"][0], vw["bbox"][0]),
                min(tw["bbox"][1], vw["bbox"][1]),
                max(tw["bbox"][2], vw["bbox"][2]),
                max(tw["bbox"][3], vw["bbox"][3]),
            ], "src": "both", "vtext": vw["text"]})
        else:
            out.append({**tw, "src": "tess-only"})
    for j, vw in enumerate(vision):
        if not used_v[j]:
            out.append({**vw, "src": "vision-only"})
    return out


# ---------- region classification ----------
def bbox_inside(inner, outer):
    """True if inner bbox center is inside outer bbox."""
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def classify_word(word: dict, regions: list[dict]) -> tuple[str, str | None]:
    """Return (verdict, region_id) where verdict is one of:
        'in_text'     - inside a text region's mask_bbox  ✅
        'in_image'    - inside an image region's bbox     ❌ wrong
        'no_region'   - in no region                       ❌ wrong
    """
    wbbox = word["bbox"]
    # Check image regions FIRST (they win priority for protection)
    for r in regions:
        if r.get("type") == "image":
            if bbox_inside(wbbox, r["bbox"]):
                return ("in_image", r.get("id"))
    # Then text regions (mask_bbox)
    for r in regions:
        if r.get("type", "text") == "text":
            mb = r.get("mask_bbox")
            if mb and mb != [0, 0, 0, 0] and bbox_inside(wbbox, mb):
                return ("in_text", r.get("id"))
    return ("no_region", None)


# ---------- main ----------
def main():
    with open(PARAGRAPHS) as f:
        para = json.load(f)

    doc = fitz.open(SRC_PDF)
    summary_lines = ["# OCR-based box accuracy audit\n"]

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except Exception:
        font = ImageFont.load_default()

    for spec in para["pages"]:
        pi = spec["page"]
        en_pix = doc[pi - 1].get_pixmap(dpi=DPI)
        en_img = Image.frombytes("RGB", (en_pix.width, en_pix.height), en_pix.samples)
        print(f"[page {pi}] running OCR...")
        try:
            t = ocr_tesseract(en_img)
        except Exception as e:
            print(f"  tesseract failed: {e}")
            t = []
        try:
            v = ocr_vision(en_img)
        except Exception as e:
            print(f"  vision failed: {e}")
            v = []
        words = union_words(t, v)
        print(f"  tesseract={len(t)}  vision={len(v)}  union={len(words)}")

        # Classify
        verdicts = {"in_text": [], "in_image": [], "no_region": []}
        for w in words:
            verdict, rid = classify_word(w, spec["regions"])
            w["verdict"] = verdict
            w["region_id"] = rid
            verdicts[verdict].append(w)

        n = len(words)
        n_ok = len(verdicts["in_text"])
        n_img = len(verdicts["in_image"])
        n_none = len(verdicts["no_region"])
        pct_ok = n_ok / n * 100 if n else 0
        print(f"  ✅ in_text={n_ok}({pct_ok:.1f}%)  ❌ in_image={n_img}  ❌ no_region={n_none}")
        summary_lines.append(
            f"\n## Page {pi}\n"
            f"- Total words detected: **{n}** (Tesseract {len(t)}, Vision {len(v)})\n"
            f"- ✅ Inside text region: **{n_ok}** ({pct_ok:.1f}%)\n"
            f"- ❌ Inside image region (text wrongly protected): **{n_img}**\n"
            f"- ❌ In no region (text leaks through): **{n_none}**\n"
        )

        # Visual overlay
        canvas = en_img.convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        COLORS = {
            "in_text":   (0, 200, 0, 90),
            "in_image":  (255, 0, 0, 130),
            "no_region": (255, 140, 0, 130),
        }
        OUTLINE = {
            "in_text":   (0, 150, 0, 255),
            "in_image":  (220, 0, 0, 255),
            "no_region": (220, 100, 0, 255),
        }
        for w in words:
            x0, y0, x1, y1 = w["bbox"]
            draw.rectangle([x0, y0, x1, y1], fill=COLORS[w["verdict"]],
                           outline=OUTLINE[w["verdict"]], width=2)
        # Legend
        ld = ImageDraw.Draw(overlay)
        try:
            lf = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        except Exception:
            lf = ImageFont.load_default()
        lx, ly = 30, en_img.height - 160
        ld.rectangle([lx-10, ly-10, lx+560, ly+140], fill=(255,255,255,230), outline=(0,0,0))
        ld.rectangle([lx, ly, lx+30, ly+30], fill=(0,200,0,200), outline=(0,150,0))
        ld.text((lx+40, ly+2), f"in text region ({n_ok})", fill=(0,0,0,255), font=lf)
        ld.rectangle([lx, ly+40, lx+30, ly+70], fill=(255,0,0,200), outline=(220,0,0))
        ld.text((lx+40, ly+42), f"in image region — DEFECT ({n_img})", fill=(0,0,0,255), font=lf)
        ld.rectangle([lx, ly+80, lx+30, ly+110], fill=(255,140,0,200), outline=(220,100,0))
        ld.text((lx+40, ly+82), f"no region — DEFECT ({n_none})", fill=(0,0,0,255), font=lf)

        composed = Image.alpha_composite(canvas, overlay).convert("RGB")
        composed.save(OUT / f"page{pi:02d}_ocr_overlay.png", "PNG", optimize=True)

        # Defect JSON
        with open(OUT / f"page{pi:02d}_defects.json", "w", encoding="utf-8") as f:
            json.dump({
                "page": pi,
                "in_image": verdicts["in_image"],
                "no_region": verdicts["no_region"],
            }, f, ensure_ascii=False, indent=2)

        # FULL word cache (used by render.py as authoritative text mask)
        with open(OUT / f"page{pi:02d}_words.json", "w", encoding="utf-8") as f:
            json.dump({"page": pi, "words": words}, f, ensure_ascii=False, indent=2)

    doc.close()

    with open(OUT / "summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))
    print(f"\n[ok] wrote {OUT}/summary.md and per-page overlays + defects JSON")


if __name__ == "__main__":
    main()
