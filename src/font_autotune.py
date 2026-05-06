"""Auto-tune font_map.json sizes/weights to match EN page metrics.

Iterates until KO cap-heights match EN cap-heights per style class.
"""
import json
import subprocess
import numpy as np
from PIL import Image
import fitz
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT/'OnMars-PlayerReference-v07.pdf'
P2 = ROOT/'data/paragraphs_p2.json'
KO_PNG = ROOT/'out/pages_ko/page_02_ko.png'
FONT_MAP = ROOT/'data/font_map.json'

# Style → reference EN sample (cap-height measurement)
# Each: (style_key, region_id used for KO measurement, en_bbox for EN measure, color_filter)
SAMPLES = [
    ('section_red',     'p2_exec_01',          (238, 152, 457, 189), 'red'),
    ('subheading_teal', 'p2_bp1_02_title',     (946, 225, 1194, 256), 'teal'),
    ('body_xs',         'p2_lab_intro',        (140, 201, 750, 305), 'black'),
]


def measure_cap(crop_arr, color):
    r,g,b = crop_arr[...,0].astype(int), crop_arr[...,1].astype(int), crop_arr[...,2].astype(int)
    if color == 'red':
        ink = (r > 180) & (g < 100)
    elif color == 'teal':
        ink = (g > 130) & (b > 110) & (g > r + 15)
    else:
        ink = (r + g + b) < 200
    if not ink.any(): return None
    rows = ink.any(axis=1)
    top = int(np.argmax(rows))
    bot = len(rows) - 1 - int(np.argmax(rows[::-1]))
    return bot - top + 1


def measure_en(en, samples):
    out = {}
    for sk, rid, bbox, color in samples:
        crop = en[bbox[1]:bbox[3], bbox[0]:bbox[2]]
        h = measure_cap(crop, color)
        out[sk] = h
    return out


def measure_ko(ko, samples, regions_lookup):
    out = {}
    for sk, rid, _, color in samples:
        if rid not in regions_lookup:
            out[sk] = None; continue
        r = regions_lookup[rid]
        mb = r.get('text_bbox') or r.get('mask_bbox')
        if not mb or mb == [0,0,0,0]:
            out[sk] = None; continue
        crop = ko[mb[1]:mb[3], mb[0]:mb[2]]
        h = measure_cap(crop, color)
        out[sk] = h
    return out


def render_page2():
    subprocess.run(['python3', str(ROOT/'src/render.py')], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_font_map():
    return json.load(open(FONT_MAP, encoding='utf-8'))

def save_font_map(fm):
    json.dump(fm, open(FONT_MAP, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def main(max_iter=8, tol=2, max_step=4, size_cap=80, size_floor=14):
    doc = fitz.open(PDF)
    en = np.array(Image.frombytes('RGB',
        (doc[1].get_pixmap(dpi=300).width, doc[1].get_pixmap(dpi=300).height),
        doc[1].get_pixmap(dpi=300).samples))
    doc.close()
    en_metrics = measure_en(en, SAMPLES)
    print(f"EN cap heights: {en_metrics}")

    para = json.load(open(P2, encoding='utf-8'))
    regions = {r['id']: r for r in para['regions']}

    for it in range(max_iter):
        if not KO_PNG.exists():
            render_page2()
        ko = np.array(Image.open(KO_PNG).convert('RGB'))
        ko_metrics = measure_ko(ko, SAMPLES, regions)
        print(f"\n[iter {it}] KO cap heights: {ko_metrics}")

        # Compute diffs
        all_ok = True
        fm = load_font_map()
        for sk, _, _, _ in SAMPLES:
            en_h = en_metrics.get(sk); ko_h = ko_metrics.get(sk)
            if en_h is None or ko_h is None or ko_h < 5:
                # measurement failure — skip this style
                print(f"  {sk}: SKIP (en={en_h} ko={ko_h})")
                continue
            diff = en_h - ko_h
            if abs(diff) > tol:
                all_ok = False
                cur = fm['styles'][sk]['size_px']
                # capped step
                step = int(round(diff))                  # 1px diff ≈ 1px size adj
                step = max(-max_step, min(max_step, step))
                new_size = cur + step
                new_size = max(size_floor, min(size_cap, new_size))
                fm['styles'][sk]['size_px'] = new_size
                print(f"  {sk}: EN={en_h} KO={ko_h} diff={diff:+}  size {cur} -> {new_size}")
        save_font_map(fm)
        if all_ok:
            print(f"\n[converged] iter {it}")
            break
        # Re-render
        render_page2()
    print(f"\nFinal styles: {load_font_map()['styles'].get('section_red')}, {load_font_map()['styles'].get('subheading_teal')}, {load_font_map()['styles'].get('body_xs')}")

if __name__ == '__main__':
    main()
