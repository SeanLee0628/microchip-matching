# -*- coding: utf-8 -*-
"""CRD 신호등 보드 순수 계산 로직.

데이터 소스: Backlog Shipment Report (오픈 주문, 행마다 CRD + MAD).
위험 판정 = MAD(자재 가용일) vs CRD(고객 요청일). 재고/영업실적 조인 불필요.
boto3/FastAPI 의존 없음 — 정규화된 dict 리스트만 받는 순수 함수.
단위테스트: test_crd_board.py
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
        card = {
            "customer": o.get("customer"), "did": o.get("did"), "mpn": o.get("mpn"),
            "qty": o.get("qty") or 0, "crd": crd, "mad": mad,
            "order_type": o.get("order_type"), "fse": o.get("fse"),
            "delay_days": None,
            "overdue": (crd is not None and crd < today),
            "risk": None, "reason": "",
        }

        if crd is None:
            card.update(risk="unknown", reason="CRD 미상 — 요청 납기일 확인 필요")
        elif mad is None:
            card.update(risk="red", reason="자재 가용일(MAD) 미상 — 확인 필요")
        else:
            delay = (mad - crd).days
            card["delay_days"] = delay
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

    prev_orders, cur_orders: [{"so","did","mpn","customer","qty","crd","mad","fse","order_type"}]
    반환:
      slipped: MAD가 늦어진(밀린) 주문 목록, 밀린 일수 내림차순.
               각 항목 = 현재 주문 필드 + prev_mad, slip_days, indefinite
      new:  현재에만 있는(신규) 주문, gone_count: 이전에만 있던(출하/소진) 수
      summary: {slipped, improved, same, new, gone, indefinite}
    indefinite = 현재 MAD가 today + indefinite_after_days 이후 (사실상 무기한 연기).
    이전·현재 중 MAD 가 없으면 밀림 계산에서 제외.
    """
    prev_by_so = {o.get("so"): o for o in prev_orders if o.get("so") is not None}
    cur_by_so = {o.get("so"): o for o in cur_orders if o.get("so") is not None}
    prev_so, cur_so = set(prev_by_so), set(cur_by_so)
    indefinite_cut = today + timedelta(days=indefinite_after_days)

    slipped = []
    improved = same = indefinite_n = 0
    for so in prev_so & cur_so:
        pmad = prev_by_so[so].get("mad")
        cmad = cur_by_so[so].get("mad")
        if pmad is None or cmad is None:
            continue
        d = (cmad - pmad).days
        if d > 0:
            indef = cmad > indefinite_cut
            if indef:
                indefinite_n += 1
            item = dict(cur_by_so[so])
            item["prev_mad"] = pmad
            item["slip_days"] = d
            item["indefinite"] = indef
            slipped.append(item)
        elif d < 0:
            improved += 1
        else:
            same += 1

    slipped.sort(key=lambda x: -x["slip_days"])
    new = [cur_by_so[so] for so in cur_so - prev_so]
    gone = prev_so - cur_so
    return {
        "slipped": slipped,
        "new": new,
        "gone_count": len(gone),
        "summary": {
            "slipped": len(slipped), "improved": improved, "same": same,
            "new": len(new), "gone": len(gone), "indefinite": indefinite_n,
        },
    }
