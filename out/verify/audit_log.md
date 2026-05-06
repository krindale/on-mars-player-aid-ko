# On Mars 한글판 — 2-pass 글자 단위 검출 결과

원본: `OnMars-PlayerReference-v07.pdf` (1008×612pt 가로형, 2 페이지)
번역본: `out/OnMars-PlayerReference-v07_KO.pdf`

## 알고리즘 (사용자 제안 채택)

> "전체 화면을 스캔하고 그 이후 텍스트를 스캔한 후 텍스트의 위치를 1픽셀 단위로 잘라서 구획을 정한다"

### Pass 1 — 전체 화면 스캔 (`src/scan_paths.py`)
PyMuPDF `page.get_drawings()` 로 모든 vector path 추출 (페이지 1: 7020, 페이지 2: 8585) → 종류별 분류:

| kind | 페이지 1 | 페이지 2 | 처리 |
|---|---|---|---|
| `glyph_dark` | 5249 | 5233 | **마스킹** (검정 본문 글자) |
| `glyph_teal` | 637 | 960 | **마스킹** (청록 강조 글자) |
| `glyph_red` | 6 | 0 | **마스킹** (빨강 헤딩 글자) |
| `separator_dash` | 131 | 256 | 보존 (항목 구분 점선) |
| `separator_line` | 8 | 5 | 보존 (긴 가로/세로 선) |
| `color_band` | 4 | 0 | 보존 (섹션 헤더 띠) |
| `icon` | 13 | 50 | 보존 (액션·BP 아이콘) |
| `other` (작은 fill) | 971 | 2080 | 일부 마스킹 (< 25×30pt 글리프 조각) |

→ `out/verify/paths_classified.json`, 시각 검증 `out/verify/paths_class_p0{1,2}.png`

### Pass 2 — 글자 path → 항목 그루핑 (`src/extract_items.py`)
글자 path 만 추려서 bottom-up:
1. 컬럼별 분리 (페이지 1: 4 컬럼, 페이지 2: 4 컬럼)
2. y-center 기반 line clustering (line_height_tol=6pt)
3. line gap > median × 1.7 또는 점선 path 가 사이에 있을 때 항목 경계
4. 항목 bbox = 그 안의 모든 글자 bbox 의 union

결과: 페이지 1 = 45 items, 페이지 2 = 49 items (`out/verify/items.json`)
시각 검증: `out/verify/items_overlay_p0{1,2}.png`

### Pass 3 — 한글 매핑 (`src/map_translations.py`)
각 컬럼별 spec 리스트(인덱스 정렬)로 항목과 한글 번역 매칭. 컬럼별 spec 은 검출된 deduped 항목 순서에 맞춰 수동 작성.

검출이 빠뜨린 영역(예: EXEC col 의 "LABORATORY BENEFITS" 타이틀)은 `MANUAL_PATCHES` 에 하드코딩한 마스크로 보완.

→ `data/paragraphs_ko.json` (페이지 1: 40 region, 페이지 2: 48 region)

### Pass 4-5 — 마스킹·렌더 (`src/render.py`)
각 region 의 `mask_bbox` 만 흰색으로 채우고 `text_bbox` 에 SUIT 폰트로 한글 그림. 색띠·점선·아이콘 path 는 마스크 영역 밖에 있어 자동 보존.

## 디자인 보존 검증

`out/verify/compare_p0{1,2}.png` 좌우 비교:

| 디자인 요소 | 보존 |
|---|---|
| 상단/하단 검정 색 띠 + 별 패턴 | ✅ |
| 섹션 헤더 tan 띠 (구별선·점선 포함) | ✅ |
| 좌측 액션 아이콘 (원형 배지) | ✅ |
| 액션 사이 빨강 점선 구분선 | ✅ |
| 컬럼 사이 세로 점선 | ✅ |
| MARS CONSTRUCTION CYCLE 원형 다이어그램 | ✅ |
| DISPLACEMENT RULE tan 박스 + 빨강 헤더 | ✅ |
| LSS REWARDS / DISCOVERY TILES 컬럼 헤더 | ✅ |
| COLONY LEVEL UP 빨강 박스 + 흰색 글씨 | ✅ |
| 페이지 2 청사진 24개 아이콘 | ✅ |
| 자원 컬럼 6개 자원 아이콘 | ✅ |
| 인라인 아이콘 (광산·발전기·셸터 등 본문 내) | ❌ 글리프와 함께 마스킹됨 — 한글에서는 [광산]·[발전기] 식 텍스트 마커 |
| MARS CYCLE 다이어그램 라벨 (Gain 1 Mineral 등) | 영문 그대로 (다이어그램 일부) |

## 알려진 한계

1. **인라인 아이콘**: 본문 글자 사이의 작은 아이콘은 글리프 마스크에 함께 덮임. 텍스트 마커로 대체.
2. **MARS CYCLE 다이어그램 안 라벨**은 다이어그램 path 와 분리되지 않아 영문 유지.
3. **bp7-8 / bp13-14 / bp17-18 / bp20-21**: 검출이 일부 청사진 행을 합쳐서 두 청사진의 한글이 한 박스에 들어가 글자 크기가 작아짐.

## 산출물

- `out/OnMars-PlayerReference-v07_KO.pdf` — 최종 한글 PDF (2 페이지)
- `out/pages_ko/page_0{1,2}_ko.png` — 페이지 PNG
- `out/verify/paths_classified.json` — Pass 1 결과
- `out/verify/paths_class_p0{1,2}.png` — Pass 1 시각 오버레이
- `out/verify/items.json` — Pass 2 항목 목록
- `out/verify/items_overlay_p0{1,2}.png` — Pass 2 시각 오버레이
- `out/verify/compare_p0{1,2}.png` — EN/KO 좌우 비교

## 재실행

```bash
python3 src/scan_paths.py        # Pass 1
python3 src/extract_items.py     # Pass 2
python3 src/map_translations.py  # Pass 3
python3 src/render.py            # Pass 4-5
```
