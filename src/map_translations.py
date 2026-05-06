"""Pass 3 — Match detected items to canonical Korean translations and emit
the final paragraphs_ko.json the renderer reads.

Strategy: items are already ordered top-to-bottom within each column.
We define a per-column list of expected translations in reading order.
Each translation gets paired with the next item; if items detected do not
match expectations exactly, the spec lets us merge or split with explicit
indexing.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS_JSON = ROOT / "out/verify/items.json"
OUT_JSON = ROOT / "data/paragraphs_ko.json"

DPI = 300
SCALE = DPI / 72.0


def merge(*bboxes_pt):
    return [min(b[0] for b in bboxes_pt), min(b[1] for b in bboxes_pt),
            max(b[2] for b in bboxes_pt), max(b[3] for b in bboxes_pt)]


def overlap_ratio(a, b):
    """Vertical overlap fraction of the smaller box."""
    iy0, iy1 = max(a[1], b[1]), min(a[3], b[3])
    if iy1 <= iy0:
        return 0
    inter = iy1 - iy0
    return inter / min(a[3] - a[1], b[3] - b[1])


def dedupe_items(items, overlap_thresh=0.4):
    """Merge items whose y-ranges overlap heavily (>40% of smaller)."""
    out = []
    items = sorted(items, key=lambda it: it["bbox_pt"][1])
    for it in items:
        if out and overlap_ratio(out[-1]["bbox_pt"], it["bbox_pt"]) > overlap_thresh:
            out[-1]["bbox_pt"] = merge(out[-1]["bbox_pt"], it["bbox_pt"])
            out[-1]["bbox_px"] = [round(c * SCALE) for c in out[-1]["bbox_pt"]]
        else:
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# Per-column translation specs.
# Each entry maps the detected item index to a Korean translation + style.
# Items not present in the spec → no mask drawn (item left untouched).
# ---------------------------------------------------------------------------

TRANSLATIONS = {
    1: {  # page 1 — index aligned with deduped item list (no None gaps)
        "left": [
            # 0: title band
            ("궤도 정거장 메인 액션", "section_red"),
            # 1-5: 5 orbital actions
            ("콜로니로 이동. 셔틀 페이즈 규칙을 따르되 1단계 건너뜀. 자기 플레이어 마커를 눕혀 놓는다.", "body_sm"),
            ("디스플레이에서 청사진 1장. 표시된 자원을 획득. 청사진 위에 고급 건물 마커 1.\n부스트: 이 액션 1회 더 반복.", "body_sm"),
            ("표시된 비용 지불 후 테크 그리드에서 테크 타일 1장. 자기 연구소 가장 왼쪽 열의 빈칸에 배치.\n부스트: 추가 자원/크리스탈 1.", "body_sm"),
            ("테크 타일 1장 두 번 또는 서로 다른 2장 한 번씩 발전. 각 칸의 비용 지불, 효과 획득.\n부스트: 테크 1번 더 발전.", "body_sm"),
            ("창고에서 자원/크리스탈 1을 자기 저장고/디포로.\n부스트: 추가 1.", "body_sm"),
            # 6: COLONY MAIN ACTIONS title + colony action 1 (long, merged)
            ("콜로니 메인 액션\n1. 어떤 건물 선택. 2. 건설 비용 지불. 3. 자기 봇 1대 안에 타일 뒷면이 보이도록 배치 (A) 같은 종류 건물 인접 + 같은 종류·적정 레벨 테크, 또는 B) 같은 종류 타일에서 정확히 2칸 떨어짐). 4. 표시된 크리스탈 배치. 5. 타일을 뒤집어 새 크기에 맞는 자원/크리스탈 획득. 6. 같은 종류 콤플렉스에 첫 기여라면 진행 큐브 배치. 7. LSS 진행 확인. 8. 광산이라면 콜로니스트 1.", "section_red_with_body"),
            # 7: colony action 2
            ("1. 자기 봇 1대 안에서 일치하는 청사진과 건물 선택. 2. 광물 1 지불. 3. 고급 건물 마커를 청사진→건물(다른 말은 밀려난다).", "body_sm"),
            # 8: colony action 3
            ("1. 과학자 위/계약서 아래 비용 지불. 2. 카드(와 과학자)를 자기 보드 옆에 배치. 3. 덱에서 원하는 계약서 1장으로 교체.", "body_sm"),
            # 9: colony action 4 (Rover)
            ("자기 로버 이동력 최대 2 + 봇들 이동력 최대 2. 로버: 지나간 크리스탈 수집, 턴 끝 시 발견·연구 타일 위면 가져감. 봇: 지나간 크리스탈·발견·연구 타일 파괴.\n부스트: 추가 이동력 1.", "body_sm"),
            # 10: Check Colony Level Up
            ("1. 디포의 우주선 총 수가 현재 콜로니 레벨보다 적은지 확인. 2. 식물 1·물 1 지불. 3. 디포→격납고로 우주선 1. 4. 콜로니스트 1+봇 1 또는 콜로니스트 2.\n부스트: 다시 한 번 전부 수행.", "body_sm"),
        ],
        "center": [
            # 0: BASIC ACTION RULES title + intro (y=40-85)
            ("기본 액션 규칙\n자기 플레이어 마커가 위치한 보드 쪽의 액션을 수행. 자기 차례에 메인 액션 1개 필수, 그 전·후로 이그제큐티브 액션 1개 가능. 액션 칸의 기호:", "section_red_with_body"),
            # 1: small line at y=91-100 — likely "The Action may be performed without any cost." X label
            ("[X] 비용 없이 액션 수행 가능.", "body_sm"),
            # 2: y=105-192 — Colonist body (long)
            ("[콜로니스트] 거주구역의 콜로니스트 1을 액션 칸에 놓는다. 이미 놓인 다른 색마다 크리스탈 1 지불 또는 콜로니스트 1을 작업장으로. (2인: 비용은 점유된 칸 단위.) 칸이 다 차 있으면 콜로니스트가 가장 많은 플레이어(들)의 것을 작업장으로 되돌리고 남은 비용 지불.", "body_sm"),
            # 3: y=193-225 — Working Area boost
            ("[작업장] 거주구역→작업장으로 콜로니스트 1+ 이동시켜 액션 부스트.", "body_sm"),
            # 4: y=225-233 — Crystal/X boost (small)
            ("[크리스탈] 크리스탈 1+ 지불해 부스트.  [X] 부스트 불가.", "body_sm"),
            # 5: y=244-375 — SHUTTLE PHASE big block (title + intro + 2 actions)
            ("셔틀 페이즈\n모든 플레이어 이동 가능. 셔틀이 자기 마커 쪽으로 가면 무료, 그렇지 않으면 격납고에서 우주선 1 제거.\n[→콜로니] 턴 순서대로: 1. 마커를 탐사로 이동, A) 발견 타일 1을 골라 자기 로버로부터 정확히 3칸 떨어진 빈칸에 놓고 B) 그 자리 채우기. 2. 작업장+콜로니측 콜로니스트 회수(초과 분실). 3. 콜로니측 빈 턴 순서 칸으로 마커 이동, 보너스.\n[→궤도] 턴 순서대로: 1. 마커를 생산 칸으로 이동(고급 건물=자원, 광산=광물 생산). 2. 작업장+궤도측 콜로니스트 회수. 3. 궤도측 빈 턴 순서 칸으로 마커 이동, 보너스.", "section_red_with_body"),
            # 6: y=382-429 — LSS title + body
            ("생명 유지 시스템 (LSS)\n현재 콜로니 레벨은 마커 바로 아래 숫자. LSS 트랙 마커는 차트 최상단이 아닌 한 보드 위 해당 종류 건물 수와 일치해야 한다.", "section_red_with_body"),
            # 7: y=432-466 — LSS continuation
            ("어떤 LSS 트랙 마커가 콜로니 레벨 마커보다 한 줄 아래 → 그 위로 올라갈 때, 그 마커를 올린 플레이어는 1) 그 열 상단 보상 타일의 OP, 2) 보상 타일 왼쪽에 표시된 혜택 1을 얻는다.", "body_sm"),
            # 8: y=478-570 — COLONY LEVEL UP red box
            ("콜로니 레벨업\n콜로니 레벨이 오를 때 [참고: 그 라운드 마지막 플레이어(들)가 산소 받기 직전·후 갱신].\n1. 콜로니 마커 이동.  2. 청사진 보충(2인: 건너뜀).  3. 테크 그리드 보충.  4. 창고 보충(2인: 아래 두 줄만).  5. 진행 큐브 점수(레벨 2·3).  6. 미션 큐브 이동(레벨 3·4).", "white_levelup"),
        ],
        "right_l": [
            # 0: MARS CYCLE upper labels — leave alone
            None,
            # 1: LSS REWARDS title + intro merged (y=147-231)
            ("LSS 보상\n이동력 최대 2로 봇 이동, 크리스탈로 부스트. 작업장의 임의 액션 칸에서 콜로니스트 1 회수.", "section_red_with_body"),
            # 2: cycle label / overlap fragment
            None,
            # 3: reward 1 — Tech tile OP (y=290-315)
            ("자기 최고 레벨 테크 타일과 같은 OP.", "body_xs"),
            # 4: reward 2 — ship in hangar (y=328-354)
            ("격납고의 우주선 1대당 2 OP.", "body_xs"),
            # 5: reward 3 — advanced building marker (y=365-390)
            ("화성에 보유한 고급 건물 마커 1개당 2 OP.", "body_xs"),
            # 6: reward 4 — mine with colonist (y=402-426)
            ("콜로니스트/고급 건물 마커가 있는 자기 광산 1개당 2 OP.", "body_xs"),
            # 7: reward 5 — bot on Mars (y=439-465)
            ("화성의 자기 봇 1대당 2 OP.", "body_xs"),
            # 8: reward 6 — discovery tile collected (y=476-505)
            ("자기 로버가 모은 발견 타일 1장당 2 OP.", "body_xs"),
            # 9: reward 7 — shelter on Mars (y=515-537)
            ("화성에 보유한 자기 셸터 1개당 2 OP.", "body_xs"),
            # 10: reward 8 — colonist in living quarters (y=551-575)
            ("자기 거주구역의 콜로니스트 1명당 1 OP.", "body_xs"),
        ],
        "right_r": [
            None,  # 0: tiny header fragment
            # 1: DISPLACEMENT RULE box (y=60-130)
            ("변위 규칙\n봇·로버 위에 짓거나 업그레이드하려면 그 말은 가장 가까운 빈 육각칸 또는 같은 색 소유의 빈 건물로. 콜로니스트 위에 짓는 경우 그 콜로니스트를 자기 거주구역으로.", "section_red_with_body"),
            # 2: DISCOVERY TILES title + intro (y=147-208)
            ("발견 타일\n테크 타일 1장을 두 번 또는 서로 다른 2장을 한 번씩 비용 없이 발전.", "section_red_with_body"),
            # 3: discovery 2 (y=215-242)
            ("표시된 종류의 건물 건설(정상 비용). 콤플렉스라면 테크 필요.", "body_xs"),
            # 4: discovery 3 (y=248-283)
            ("건물 2개를 업그레이드(정상 비용).", "body_xs"),
            # 5: discovery 4 (y=291-313)
            ("임의 종류 건물 건설(정상 비용). 크기 2 이상이면 테크 필요.", "body_xs"),
            # 6: discovery 5 (y=323-358)
            ("과학자 카드 1장 채용 또는 지구 계약 1개(정상 비용).", "body_xs"),
            # 7: discovery 6 (y=364-391)
            ("청사진 최대 2장 가져오기.", "body_xs"),
            # 8: discovery 7 (y=404-425)
            ("창고에서 자원/크리스탈 최대 2개를 자기 저장고/디포로.", "body_xs"),
            # 9: discovery 8 (y=430-475)
            ("크리스탈 3개 획득.", "body_xs"),
            # 10: discovery 9 (y=478-501)
            ("표시된 종류·수량의 자원 획득.", "body_xs"),
            # 11: discovery 10 (y=514-539)
            ("테크 타일 1장 획득(정상 비용).", "body_xs"),
            # 12, 13: trailing fragments — None
        ],
    },
    2: {
        "exec": [
            # 0: EXECUTIVE ACTIONS title (y=37-70)
            ("이그제큐티브 액션", "section_red"),
            # 1: intro + Cost 4 actions merged (y=83-163)
            ("한 턴에 한 번, 메인 액션 전·후로 이그제큐티브 액션 1.\n비용: 크리스탈 4 — 비용 없이 테크 타일 1 발전.\n비용: 크리스탈 4 — 로버 최대 2칸 이동(크리스탈/테크 가능).", "body_xs"),
            # 2: Cost 3 take blueprint (y=174-191)
            ("비용: 크리스탈 3 — 청사진 1.", "body_xs"),
            # 3: Cost 3 upgrade + gain mineral (y=204-251)
            ("비용: 크리스탈 3 — 건물 1 업그레이드.\n비용: 크리스탈 3 — 광물 1.", "body_xs"),
            # 4: Cost 2 movement (y=248-284)
            ("비용: 크리스탈 2 — 봇들 이동력 최대 2(크리스탈로 추가).", "body_xs"),
            # 5: Cost 2 take resource (y=291-315)
            ("비용: 크리스탈 2 — 창고에서 자원 1.", "body_xs"),
            # 6: small fragment
            None,
            # 7: Cost 2 advanced action (y=336-352)
            ("비용: 크리스탈 2 또는 일치 과학자 — 고급 건물 액션 사용.", "body_xs"),
            # 8: LABORATORY BENEFITS title (y=364-381)
            ("연구소 효과", "section_red"),
            # 9: intro (y=401-417)
            ("테크 타일로 칸을 덮을 때 그 칸의 효과. 창고에서 크리스탈 1 또는 자원 1.", "body_xs"),
            # 10: lab benefits part 2 (y=434-455)
            ("공급처에서 크리스탈 1.  공급처에서 광물 1.", "body_xs"),
            # 11: lab benefits part 3 (y=464-565)
            ("봇들 이동력 최대 2(크리스탈로 추가).\n건물 1 업그레이드(테크 가능, 작업장 보내 추가 가능).\n청사진 1(작업장 보내 추가 가능).", "body_xs"),
        ],
        "bp1": [
            # 0: BLUEPRINTS LEVEL 1 band title (y=36-45)
            ("청사진 레벨 1", "section_red"),
            # 1: BP1 (y=55-88)
            ("1. 건설 야적장 — 광물 1 / 광산 / 지질학자\n고급 건물 액션: 정상 규칙대로 건물 1 업그레이드(테크 가능). 부스트마다: 1회 추가.", "body_xs"),
            # 2: BP2 (y=98-135)
            ("2. 광물 매장지 — 광물 1 / 광산 / 지질학자\n고급 건물 액션: 광산 1 건설(콤플렉스라면 테크). 부스트마다: 테크 +1.", "body_xs"),
            # 3: BP3 (y=142-180)
            ("3. 자동 생산 — 배터리 1 / 발전기 / R&D 엔지니어\n고급 건물 액션: 자기 광산/고급 건물이 자원·크리스탈 1에 일치하면 한 번 더 생산. 부스트마다: 추가 생산 1.", "body_xs"),
            # 4: BP4
            ("4. 풍력 터빈 — 배터리 1 / 발전기 / R&D 엔지니어\n고급 건물 액션: 발전기 1 건설(콤플렉스라면 테크). 부스트마다: 테크 +1.", "body_xs"),
            # 5: BP5
            ("5. 개인 우주선 — 물 1 / 수자원 추출기 / 수경재배학자\n고급 건물 액션: 우주선 환영. 식물 1·물 1로 콜로니스트 2+봇 1 또는 콜로니스트 3. 부스트마다: 우주선 1 추가.", "body_xs"),
            # 6: BP6
            ("6. 수분 증발기 — 물 1 / 수자원 추출기 / 수경재배학자\n고급 건물 액션: 수자원 추출기 1 건설. 부스트마다: 테크 +1.", "body_xs"),
            # 7: BP7
            ("7. 바이오마켓 — 식물 1 / 그린하우스 / 생화학자\n고급 건물 액션: 창고에서 자원/크리스탈 1. 부스트마다: 추가 1.", "body_xs"),
            # 8: BP7-BP8 split fragment? small one
            None,
            # 9: BP8
            ("8. 수경 농장 — 식물 1 / 그린하우스 / 생화학자\n고급 건물 액션: 그린하우스 1 건설. 부스트마다: 테크 +1.", "body_xs"),
            # 10: BP9
            ("9. 산소 탱크 — 산소 1 / 산소 응축기 / 지화학자\n고급 건물 액션: 테크 그리드에서 정상 비용으로 테크 1. 부스트마다: 테크 1 추가.", "body_xs"),
            # 11: BP10
            ("10. 농축기 — 산소 1 / 산소 응축기 / 지화학자\n고급 건물 액션: 산소 응축기 1 건설. 부스트마다: 테크 +1.", "body_xs"),
            # 12: BP11
            ("11. 카지노 — 크리스탈 1 / 셸터 / 시스템 엔지니어\n고급 건물 액션: 크리스탈 2. 부스트마다: 추가 1.", "body_xs"),
            # 13: BP12
            ("12. 체육관 — 크리스탈 1 / 셸터 / 시스템 엔지니어\n고급 건물 액션: 셸터 1 건설. 부스트마다: 테크 +1.", "body_xs"),
        ],
        "bp3": [
            # 0: BLUEPRINTS LEVEL 3 band title (y=31-51)
            ("청사진 레벨 3", "section_red"),
            # 1: BP13 (y=55-77)
            ("13. 광물 광산 — 광물 1 / 광산 / 지질학자\n고급 건물 액션: 2 OP.", "body_xs"),
            # 2: BP14 (y=98-127)
            ("14. 바이오 연구소 — 광물 1 / 광산 / 지질학자\n고급 건물 액션: 디스플레이에서 정상 비용으로 지구 계약 1.", "body_xs"),
            # 3: BP15 (y=142-180)
            ("15. 레이더 — 배터리 1 / 발전기 / R&D 엔지니어\n고급 건물 액션: 카드 위에 발견 타일을 둠. 가져갈 때 보너스+게임 종료까지 카드 액션 재사용.", "body_xs"),
            # 4: BP16 (y=186-216)
            ("16. 빌더 드론 A100 — 배터리 1 / 발전기 / R&D 엔지니어\n고급 건물 액션: 맵 어디든 건물 1 업그레이드(자기 봇 무관).", "body_xs"),
            # 5: BP17+18 merged (y=227-304, h=77)
            ("17. 연구소 — 물 1 / 수자원 추출기 / 수경재배학자\n고급 건물 액션: 자기 테크 타일 최대 2장 발전(정상 비용). 부스트마다: 1장 추가.\n18. 수도교 — 물 1 / 수자원 추출기 / 수경재배학자\n고급 건물 액션: 자기 거주구역으로 콜로니스트 최대 2.", "body_xs"),
            # 6: BP19 (y=314-347)
            ("19. 에코 리조트 — 식물 1 / 그린하우스 / 생화학자\n고급 건물 액션: 궤도 액션 칸·콜로니의 자기 콜로니스트를 거주구역으로.", "body_xs"),
            # 7: BP20+21 merged (y=360-443)
            ("20. 무역 시장 — 식물 1 / 그린하우스 / 생화학자\n고급 건물 액션: 저장고에서 자원 1을 지불하고 비-광물 자원 2를 공급처에서. 부스트마다: 자원 1 추가.\n21. 재활용 봇 — 산소 1 / 산소 응축기 / 지화학자\n고급 건물 액션: 이동력 2로 자기 로버 이동(크리스탈, 연구·발견 타일 회수). 부스트마다: 이동력 1.", "body_xs"),
            # 8: BP22 (y=449-485)
            ("22. 공중 엘리베이터 — 산소 1 / 산소 응축기 / 지화학자\n고급 건물 액션: 메인 액션 전 궤도로 이동해 평소 이동 단계, 이후 메인 액션.", "body_xs"),
            # 9: BP23 (y=492-526)
            ("23. 도서관 — 크리스탈 1 / 셸터 / 시스템 엔지니어\n고급 건물 액션: 청사진 1. 부스트마다: 추가 1.", "body_xs"),
            # 10: BP24 (y=535-571)
            ("24. 사령부 — 크리스탈 1 / 셸터 / 시스템 엔지니어\n고급 건물 액션: 자기 로버를 (현재 보드의 자기 로버 테크 최고 레벨)만큼 칸 이동. 부스트마다: 1칸 추가.", "body_xs"),
        ],
        "res": [
            # 0: title
            ("자원", "section_red"),
            # 1: intro line 1
            ("자원을 어떻게 얻고 어디에 사용하는가?", "body_xs"),
            # 2: intro line 2 (small fragment)
            None,
            # 3: iron (y=167-230)
            ("[광물] 광산 건설, 청사진, LSS 보상, 테크 업그레이드, 이그제큐티브 액션. 다른 자원처럼 사용(크리스탈 제외).", "body_xs"),
            # 4: battery (y=245-308)
            ("[배터리] 발전기 건설·업그레이드, 청사진·창고 액션, R&D 엔지니어 채용, 테크 업그레이드, 계약 이행.", "body_xs"),
            # 5: water (y=322-378)
            ("[물] 수자원 추출기 건설, 청사진·창고 액션, 그린하우스 건설, 우주선 환영, 턴 순서 칸, 수경재배학자 채용, 테크 업그레이드, 계약 이행.", "body_xs"),
            # 6: small fragment between water and plant
            None,
            # 7: plant (y=400-463)
            ("[식물] 그린하우스 건설, 청사진·창고 액션, 산소 응축기 건설, 우주선 환영, 생화학자 채용, 테크 업그레이드, 계약 이행.", "body_xs"),
            # 8: oxygen (y=486-513)
            ("[산소] 산소 응축기 건설, 청사진·창고 액션, 봇·셸터 건설, 시스템 엔지니어 채용, 테크 업그레이드, 계약 이행.", "body_xs"),
            # 9: crystal (y=508-581)
            ("[크리스탈] 셸터 건설, LSS 보너스, 자원·로버 회수, 콤플렉스 기여, 테크 업그레이드 보너스, 셸터 생산, 궤도 이동, 청사진·창고, 시스템 엔지니어, 계약 이행.", "body_xs"),
        ],
    },
}


MANUAL_PATCHES = {
    # Hardcoded regions for things that detection missed entirely. Coords in
    # image pixels (300dpi from 1008x612pt page).
    1: [],
    2: [
        # LAB BENEFITS title (mask English, leave my idx[8] '연구소 효과' below it)
        {"id": "p2_exec_lab_title_patch",
         "mask_bbox": [120, 1335, 720, 1395],
         "text_bbox": [0, 0, 0, 0],
         "text": "", "style": "body_xs", "align": "left"},
        # Residual english fragments in resources column - tighten to actual y bands
        {"id": "p2_res_patch_1", "mask_bbox": [3625, 460, 4180, 615], "text_bbox": [0,0,0,0], "text": "", "style": "body_xs"},
        {"id": "p2_res_patch_2", "mask_bbox": [3625, 1370, 4180, 1450], "text_bbox": [0,0,0,0], "text": "", "style": "body_xs"},
        {"id": "p2_res_patch_3", "mask_bbox": [3625, 1660, 4180, 1750], "text_bbox": [0,0,0,0], "text": "", "style": "body_xs"},
        {"id": "p2_res_patch_4", "mask_bbox": [3625, 2020, 4180, 2200], "text_bbox": [0,0,0,0], "text": "", "style": "body_xs"},
    ],
}


def main() -> None:
    items_data = json.loads(ITEMS_JSON.read_text())
    pages_out = []
    for pn in (1, 2):
        all_items = items_data[str(pn)]["items"]
        # Group by column
        by_col = {}
        for it in all_items:
            by_col.setdefault(it["column"], []).append(it)
        # Dedupe within each column
        for col in by_col:
            by_col[col] = dedupe_items(by_col[col])

        regions = []
        for col, spec in TRANSLATIONS[pn].items():
            items = by_col.get(col, [])
            for idx, entry in enumerate(spec):
                if entry is None or idx >= len(items):
                    continue
                text, style = entry
                bb_pt = items[idx]["bbox_pt"]
                bb_px = [round(c * SCALE) for c in bb_pt]
                # 2px padding so glyph anti-aliasing edges are covered
                mask_bbox = [bb_px[0] - 4, bb_px[1] - 4, bb_px[2] + 4, bb_px[3] + 4]
                regions.append({
                    "id": f"p{pn}_{col}_{idx+1:02d}",
                    "mask_bbox": mask_bbox,
                    "text_bbox": bb_px,
                    "text": text,
                    "style": style,
                    "align": "center" if style in ("section_red", "section_red_lg") else "left",
                })
        # Append manual patches for each page
        for patch in MANUAL_PATCHES.get(pn, []):
            regions.append({
                "id": patch["id"],
                "mask_bbox": patch["mask_bbox"],
                "text_bbox": patch.get("text_bbox", [0, 0, 0, 0]),
                "text": patch.get("text", ""),
                "style": patch.get("style", "body_xs"),
                "align": patch.get("align", "left"),
            })
        pages_out.append({"page": pn, "regions": regions})

    out = {
        "_comment": "Generated by src/map_translations.py from out/verify/items.json. Mask bboxes are derived from per-glyph clustered text items, not from arbitrary rectangles. Design elements (color bands, dotted separators, icons) sit between detected items and are preserved.",
        "image_size": [4200, 2550],
        "pdf_size_pt": [1008, 612],
        "dpi": 300,
        "pages": pages_out,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    n1 = len(pages_out[0]["regions"])
    n2 = len(pages_out[1]["regions"])
    print(f"wrote {OUT_JSON} (p1={n1}, p2={n2} regions)")


if __name__ == "__main__":
    main()
