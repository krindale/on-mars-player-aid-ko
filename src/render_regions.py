"""Generate annotated REGIONS overlays from paragraphs_p*.json (text + image boxes).

Usage:
    python3 src/render_regions.py
Outputs:
    out/verify/icon_audit/page01_REGIONS.png
    out/verify/icon_audit/page02_REGIONS.png
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import fitz

ROOT = Path(__file__).resolve().parent.parent
SRC_PDF = ROOT / "OnMars-PlayerReference-v07.pdf"
OUT_DIR = ROOT / "out" / "verify" / "icon_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

try:
    f_label = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18)
    f_text  = ImageFont.truetype("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 16)
    f_legend = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
except Exception:
    f_label = f_text = f_legend = ImageFont.load_default()


def load_pages():
    files = sorted((ROOT / "data").glob("paragraphs_p*.json"))
    if files:
        return [(json.load(open(p, encoding="utf-8")), p.name) for p in files]
    # legacy single file
    legacy = ROOT / "data" / "paragraphs_ko.json"
    d = json.load(open(legacy, encoding="utf-8"))
    return [({"page": s["page"], "regions": s["regions"]}, legacy.name) for s in d["pages"]]


def main():
    doc = fitz.open(SRC_PDF)
    for page_data, source_name in load_pages():
        pi = page_data["page"]
        en_pix = doc[pi - 1].get_pixmap(dpi=300)
        base = Image.frombytes("RGB", (en_pix.width, en_pix.height), en_pix.samples).convert("RGBA")
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        text_idx = image_idx = 0
        regions = sorted(
            page_data["regions"],
            key=lambda r: ((r.get("mask_bbox") or r.get("bbox") or [0,0,0,0])[1],
                           (r.get("mask_bbox") or r.get("bbox") or [0,0,0,0])[0]),
        )
        # Two-pass: assign IDs in sorted order, but draw text first then image
        # so image boxes always appear on top (higher z).
        ordered = []
        for r in regions:
            rtype = r.get("type", "text")
            if rtype == "image":
                image_idx += 1
                ordered.append(("image", image_idx, r))
            else:
                text_idx += 1
                ordered.append(("text", text_idx, r))
        # draw text first
        for k, idx, r in ordered:
            if k != "text": continue
            mb = r.get("mask_bbox")
            if not mb or mb == [0,0,0,0]: continue
            x0, y0, x1, y1 = mb
            draw.rectangle([x0, y0, x1, y1], fill=(255,0,0,55), outline=(220,0,0,255), width=3)
            lbl = f"T{idx:03d}"
            draw.rectangle([x0, y0-26, x0+len(lbl)*11+8, y0], fill=(220,0,0,255))
            draw.text((x0+4, y0-23), lbl, fill=(255,255,255), font=f_label)
        # then image (on top)
        for k, idx, r in ordered:
            if k != "image": continue
            x0, y0, x1, y1 = r["bbox"]
            draw.rectangle([x0, y0, x1, y1], fill=(0,200,0,80), outline=(0,150,0,255), width=3)
            lbl = f"I{idx:03d}"
            draw.rectangle([x0, y0-26, x0+len(lbl)*11+8, y0], fill=(0,150,0,255))
            draw.text((x0+4, y0-23), lbl, fill=(255,255,255), font=f_label)
        # Skip the legacy single-pass loop below
        if False:
          for r in regions:
            rtype = r.get("type", "text")
            if rtype == "image":
                x0, y0, x1, y1 = r["bbox"]
                image_idx += 1
                draw.rectangle([x0, y0, x1, y1], fill=(0,200,0,80), outline=(0,150,0,255), width=3)
                lbl = f"I{image_idx:03d}"
                draw.rectangle([x0, y0-26, x0+len(lbl)*11+8, y0], fill=(0,150,0,255))
                draw.text((x0+4, y0-23), lbl, fill=(255,255,255), font=f_label)
            else:
                mb = r.get("mask_bbox")
                if not mb or mb == [0,0,0,0]: continue
                x0, y0, x1, y1 = mb
                text_idx += 1
                draw.rectangle([x0, y0, x1, y1], fill=(255,0,0,55), outline=(220,0,0,255), width=3)
                lbl = f"T{text_idx:03d}"
                draw.rectangle([x0, y0-26, x0+len(lbl)*11+8, y0], fill=(220,0,0,255))
                draw.text((x0+4, y0-23), lbl, fill=(255,255,255), font=f_label)
        composed = Image.alpha_composite(base, layer).convert("RGB")
        leg = Image.new("RGB", (760, 200), (255,255,255))
        ld = ImageDraw.Draw(leg)
        ld.rectangle([10,10,750,190], outline=(0,0,0), width=2)
        ld.text((20, 20), f"PAGE {pi}  ({source_name})", fill=(0,0,0), font=f_legend)
        ld.rectangle([20,70,80,100], fill=(255,0,0), outline=(220,0,0))
        ld.text((90,70), f"TEXT mask_bbox  T001-T{text_idx:03d}  ({text_idx})", fill=(0,0,0), font=f_legend)
        ld.rectangle([20,120,80,150], fill=(0,200,0), outline=(0,150,0))
        ld.text((90,120), f"IMAGE bbox  I001-I{image_idx:03d}  ({image_idx})", fill=(0,0,0), font=f_legend)
        composed.paste(leg, (composed.width-780, composed.height-220))
        out = OUT_DIR / f"page{pi:02d}_REGIONS.png"
        composed.save(out, "PNG", optimize=True)
        print(f"page {pi}: {text_idx} text + {image_idx} image  ->  {out}")
    doc.close()


if __name__ == "__main__":
    main()
