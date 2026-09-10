# -*- coding: utf-8 -*-
"""CRD 신호등 보드 순수 계산 로직.

데이터 소스: Backlog Shipment Report (오픈 주문, 행마다 CRD + MAD).
위험 판정 = MAD(자재 가용일) vs CRD(고객 요청일). 재고/영업실적 조인 불필요.
boto3/FastAPI 의존 없음 — 정규화된 dict 리스트만 받는 순수 함수.
단위테스트: test_crd_board.py

영업1실 용어 대응 (2026-09-10 요청서 기준):
  "경과일수" = MAD - CRD  → 코드의 delay_days. 양수면 자재 가용일이 요청일을 넘긴 것.
               첨부 "Backlog Shipment Report - 260909_CRD대비 MAD" 3,174행 전부 이 식과 일치.
  "경과된 것" = delay_days > 0 → 카드의 elapsed=True. (delay_days == 0 은 경과가 아니다)
  "GAP"      = 현재 MAD - 이전 MAD → compare_backlog 의 gap_days. 양수면 밀림, 음수면 당겨짐.
"""
from datetime import date, timedelta


def classify_backlog(orders, today, buffer_days=7):
    """오픈 백로그 주문을 MAD vs CRD로 위험판정.

    orders: [{"customer","did","mpn","qty","crd": date|None,"mad": date|None,"order_type"}]
    risk:
      green  = 자재 가용일(MAD) ≤ 요청일(CRD)             (여유)
      yellow = MAD 가 CRD 초과하나 buffer_days 이내       (임박)
      red    = MAD 가 CRD 보다 buffer_days 초과해서 늦음   (지각) / MAD 미상
      unknown= CRD 미상
    overdue = CRD 가 오늘(today) 이전 (이미 납기 경과).
    정렬: red→yellow→green→unknown, 동급은 CRD 빠른 순.
    """
    cards = []
    for o in orders:
        crd, mad = o.get("crd"), o.get("mad")
        # 원본 필드를 통째로 실어 보낸다 — PO#·PLANT·BOX_TYPE·CUST 처럼 판정에는
        # 안 쓰지만 화면 표와 엑셀에 그대로 나가야 하는 열이 있다.
        card = dict(o)
        card.update({
            "qty": o.get("qty") or 0, "crd": crd, "mad": mad,
            "delay_days": None,      # = 경과일수 (MAD - CRD)
            "elapsed": False,        # 경과일수 > 0 (영업1실 "경과된 것")
            "overdue": (crd is not None and crd < today),
            "risk": None, "reason": "",
        })

        if crd is None:
            card.update(risk="unknown", reason="CRD 미상 — 요청 납기일 확인 필요")
        elif mad is None:
            card.update(risk="red", reason="자재 가용일(MAD) 미상 — 확인 필요")
        else:
            delay = (mad - crd).days
            card["delay_days"] = delay
            card["elapsed"] = delay > 0
            if mad <= crd:
                card.update(risk="green", reason=f"자재 가용 {mad} ≤ 요청 {crd} (여유)")
            elif delay <= buffer_days:
                card.update(risk="yellow", reason=f"MAD {mad} · 요청 대비 {delay}일 임박")
            else:
                card.update(risk="red", reason=f"MAD {mad} · 요청 대비 {delay}일 지각")
            if card["overdue"]:
                card["reason"] += " · 이미 납기 경과"

        cards.append(card)

    rank = {"red": 0, "yellow": 1, "green": 2, "unknown": 3}
    cards.sort(key=lambda c: (rank.get(c["risk"], 9), c["crd"] is None, c["crd"] or date.max))
    return cards


def sort_by_mad(cards):
    """자재 가용일(MAD) 빠른 순으로 정렬. MAD 미상은 맨 아래.

    요청서(2026-09-10)의 화면 예시와 첨부 파일이 모두 이 순서다 — 위험도순이 아니라
    "언제 자재가 붙는가" 순. classify_backlog 의 위험도 정렬은 그대로 두고 여기서 다시 세운다.
    """
    return sorted(cards, key=lambda c: (c.get("mad") is None, c.get("mad") or date.max,
                                        c.get("crd") is None, c.get("crd") or date.max))


def elapsed_only(cards):
    """경과된 것(경과일수 > 0)만 남긴다. 경과일수 0(MAD == CRD)은 경과가 아니다."""
    return [c for c in cards if c.get("elapsed")]


def summarize_by_part(cards, top=10):
    """위험 카드를 (DID, MPN) 부품별로 집계 — 어느 부품에 위험이 몰렸나.

    반환: [{"did","mpn","red","yellow","green","unknown","red_qty"}, ...]
      red 건수 내림차순, 동수면 red 수량 내림차순. top 개만 (top=0 이면 전체).
    """
    agg = {}
    for c in cards:
        key = (c.get("did"), c.get("mpn"))
        a = agg.get(key)
        if a is None:
            a = {"did": c.get("did"), "mpn": c.get("mpn"),
                 "red": 0, "yellow": 0, "green": 0, "unknown": 0, "red_qty": 0}
            agg[key] = a
        risk = c.get("risk")
        a[risk] = a.get(risk, 0) + 1
        if risk == "red":
            a["red_qty"] += c.get("qty") or 0

    parts = sorted(agg.values(), key=lambda p: (-p["red"], -p["red_qty"]))
    return parts[:top] if top else parts


def compare_backlog(prev_orders, cur_orders, today, indefinite_after_days=730):
    """이전·현재 백로그 스냅샷을 SO로 매칭해 MAD 변화(선적 일정 변동)를 분석.

    prev_orders, cur_orders: [{"so","did","mpn","customer","qty","crd","mad","fse","cust", ...}]
    반환:
      changed: 변화된 라인 전부 — GAP != 0 (밀림·당겨짐 양방향) + 신규 SO.
               MAD 오름차순. 각 항목 = 현재 주문 필드 + prev_mad, gap_days, is_new,
               slip_days(밀린 경우만), indefinite
               신규 SO 나 한쪽 MAD 미상이면 prev_mad·gap_days 가 None (화면·엑셀에선 N/A).
      slipped: changed 중 밀린 것만, 밀린 일수 내림차순 (기존 신호등 화면이 쓰던 목록).
      new:  현재에만 있는(신규) 주문, gone_count: 이전에만 있던(출하/소진) 수
      summary: {changed, slipped, improved, same, new, gone, indefinite}
    indefinite = 현재 MAD가 today + indefinite_after_days 이후 (사실상 무기한 연기).

    FSE·CUST 는 현재 백록에 비어 있으면 이전 백록의 같은 SO 에서 끌어온다
    (2026-09-10 영업1실 요청). 두 쪽 다 없으면 그대로 빈칸.
    """
    prev_by_so = {o.get("so"): o for o in prev_orders if o.get("so") is not None}
    cur_by_so = {o.get("so"): o for o in cur_orders if o.get("so") is not None}
    prev_so, cur_so = set(prev_by_so), set(cur_by_so)
    indefinite_cut = today + timedelta(days=indefinite_after_days)

    changed = []
    improved = same = indefinite_n = 0
    for so in cur_so:
        cur = cur_by_so[so]
        prev = prev_by_so.get(so)
        item = dict(cur)
        # FSE·CUST 는 이전 백록에서 보충 (SO 기준)
        for k in ("fse", "cust"):
            if not item.get(k) and prev is not None and prev.get(k):
                item[k] = prev[k]

        pmad = prev.get("mad") if prev is not None else None
        cmad = cur.get("mad")
        item["is_new"] = prev is None
        item["prev_mad"] = pmad
        item["gap_days"] = None
        item["slip_days"] = None
        item["indefinite"] = False

        if pmad is None or cmad is None:
            # 신규 SO 이거나 한쪽 MAD 미상 → GAP 계산 불가(N/A). 변화로 본다.
            changed.append(item)
            continue

        d = (cmad - pmad).days
        item["gap_days"] = d
        if d > 0:
            item["slip_days"] = d
            item["indefinite"] = cmad > indefinite_cut
            if item["indefinite"]:
                indefinite_n += 1
            changed.append(item)
        elif d < 0:
            improved += 1
            changed.append(item)
        else:
            same += 1

    changed = sort_by_mad(changed)
    slipped = sorted([c for c in changed if c["slip_days"]], key=lambda x: -x["slip_days"])
    new = [cur_by_so[so] for so in cur_so - prev_so]
    gone = prev_so - cur_so
    return {
        "changed": changed,
        "slipped": slipped,
        "new": new,
        "gone_count": len(gone),
        "summary": {
            "changed": len(changed), "slipped": len(slipped),
            "improved": improved, "same": same,
            "new": len(new), "gone": len(gone), "indefinite": indefinite_n,
        },
    }
