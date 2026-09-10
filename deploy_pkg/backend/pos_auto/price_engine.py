# -*- coding: utf-8 -*-
"""단가 결정 — 유효성 검사를 통과한 후보만 가격 비교에 참여시킨다.

핵심: 가격을 먼저 정하고 나중에 경고로 무효화하지 않는다(기존 엑셀의 실패 방식).
"""
from datetime import date
from typing import List, Optional, Tuple

from .models import ASD, QTN


def qtn_reject_reason(q: QTN, ship_date: date, qty: float, rules: dict) -> Optional[str]:
    """후보에서 빠지는 이유. None 이면 유효."""
    if rules.get("require_valid_start", True):
        if q.valid_start is None:
            return "QTN_NOT_STARTED"          # 시작일 없음 = 기간 검증 불가
        if ship_date < q.valid_start:
            return "QTN_NOT_STARTED"
    if rules.get("require_valid_end", True):
        if q.valid_end is None:
            return "QTN_EXPIRED"
        if ship_date > q.valid_end:
            return "QTN_EXPIRED"
    if rules.get("require_positive_price", True):
        if q.qtn_price is None or q.qtn_price <= 0:
            return "INVALID_PRICE"
    if rules.get("require_sufficient_qty", True):
        if q.remaining_qty < qty:
            return "QTN_INSUFFICIENT_QTY"
    if not (q.quote_number or q.quote_item_number):
        return "NO_VALID_QTN"
    return None


def asd_reject_reason(a: ASD, ship_date: date, rules: dict) -> Optional[str]:
    blank = a.valid_start is None or a.valid_end is None
    if blank:
        if not rules.get("allow_blank_validity", False):
            return "ASD_EXPIRED"              # 기간 미상 → 무기한으로 해석하지 않음
    else:
        if ship_date < a.valid_start:
            return "ASD_NOT_STARTED"
        if ship_date > a.valid_end:
            return "ASD_EXPIRED"
    if rules.get("require_positive_price", True):
        if a.asd_price is None or a.asd_price <= 0:
            return "INVALID_PRICE"
    return None


_FAR = date(9999, 12, 31)


def _qtn_sort_key(q: QTN):
    return (float(q.qtn_price if q.qtn_price is not None else 1e18),
            q.valid_end or _FAR, q.valid_start or _FAR,
            q.quote_number, q.quote_item_number, q.source_row)


def _asd_sort_key(a: ASD):
    return (float(a.asd_price if a.asd_price is not None else 1e18),
            a.valid_end or _FAR, a.valid_start or _FAR, a.source_row)


def eligible_qtns(cands: List[QTN], ship_date: date, qty: float,
                  rules: dict) -> Tuple[List[QTN], List[str]]:
    ok, reasons = [], []
    for q in cands:
        r = qtn_reject_reason(q, ship_date, qty, rules)
        if r is None:
            ok.append(q)
        else:
            reasons.append(r)
    ok.sort(key=_qtn_sort_key)
    return ok, reasons


def eligible_asds(cands: List[ASD], ship_date: date, rules: dict) -> Tuple[List[ASD], List[str]]:
    ok, reasons = [], []
    for a in cands:
        r = asd_reject_reason(a, ship_date, rules)
        if r is None:
            ok.append(a)
        else:
            reasons.append(r)
    ok.sort(key=_asd_sort_key)
    return ok, reasons


def has_price_conflict(items: List, price_attr: str) -> bool:
    """1순위 동률인데 가격이 다르면 충돌."""
    if len(items) < 2:
        return False
    a, b = items[0], items[1]
    pa, pb = getattr(a, price_attr), getattr(b, price_attr)
    same_rank = (pa == pb and a.valid_end == b.valid_end and a.valid_start == b.valid_start)
    return bool(same_rank and pa != pb)


def select(qtn: Optional[QTN], asd: Optional[ASD], tie: str = "QTN"):
    """(적용유형, 적용단가, 근거, 규칙ID)"""
    qp = qtn.qtn_price if qtn else None
    ap = asd.asd_price if asd else None
    if qp is not None and ap is not None:
        if qp == ap:
            if tie.upper() == "QTN":
                return "QTN", qp, f"QTN({qp})=ASD({ap}) 동률 → QTN 우선", "R10-2"
            return "ASD", ap, f"QTN({qp})=ASD({ap}) 동률 → ASD 우선", "R10-2"
        if qp < ap:
            return "QTN", qp, f"QTN({qp}) < ASD({ap})", "R10-1"
        return "ASD", ap, f"ASD({ap}) < QTN({qp})", "R10-1"
    if qp is not None:
        return "QTN", qp, "유효 QTN 만 존재", "R10-3"
    if ap is not None:
        return "ASD", ap, "유효 ASD 만 존재", "R10-4"
    return "NONE", None, "유효한 QTN·ASD 없음", "R10-5"
