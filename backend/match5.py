# -*- coding: utf-8 -*-
"""영업5실 매칭 (Uniquant) 순수 로직.

4개 raw 파일(재고·FCST·백록·출고내역)을 MIX#(더존코드+PART#) 기준으로 조인해
매칭 레코드를 만든다. 무상태·DB/AI 미사용. FastAPI 엔드포인트는 main.py/main_aws.py.
"""
import io
import math
import re
from datetime import date, datetime

COLUMNS = [
    "고객코드", "믹스#", "담당자", "고객", "품번", "Q'ty", "Lead Time",
    "Cancel Window", "Demand Total", "Balance",
    "2023년", "2024년", "2025년", "2026년", "23~25추이", "25-26(w/BL)", "BLOG TTL",
    "6월", "7월", "8월", "9월", "10월", "11월", "12월", "1월", "2월", "3월",
]
DASHBOARD_COLUMNS = [
    "고객코드", "믹스#", "담당자", "고객", "품번", "Q'ty", "Lead Time",
    "Cancel Window", "Demand Total", "Balance",
    "2023년", "2024년", "2025년", "2026년", "BLOG TTL",
]
MONTH_COLUMNS = [("6월", 6), ("7월", 7), ("8월", 8), ("9월", 9), ("10월", 10),
                 ("11월", 11), ("12월", 12), ("1월", 1), ("2월", 2), ("3월", 3)]
DEFAULT_INVENTORY_PASSWORD = "9178"


def _s(v):
    """str 변환, NaN/None/'nan' 은 None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _code_str(v):
    """더존코드: float(131112.0) → '131112'."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() or None


def _norm_part(p):
    """PART# 정규화: 모든 공백/줄바꿈 제거 + 대문자."""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return None
    s = re.sub(r"\s+", "", str(p)).upper()
    return s or None


def make_mix(code, part):
    """MIX# = 정규화(더존코드) + 정규화(PART#). 둘 중 하나라도 없으면 None."""
    c = _code_str(code)
    p = _norm_part(part)
    if not c or not p:
        return None
    return c + p


def _to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _to_date(v):
    """datetime/date/Timestamp → date. 그 외 None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _find_header_row(ws, required, max_scan=6):
    """required(집합) 의 헤더 텍스트를 모두 포함하는 첫 행을 찾는다.
    반환: (행번호(1-base), {헤더텍스트: 열번호(1-base)}). 못 찾으면 (None, {}).
    헤더 비교는 공백·줄바꿈 제거 후 정확 일치.
    """
    def norm(v):
        if v is None:
            return None
        return re.sub(r"\s+", "", str(v))

    req_norm = {norm(x) for x in required}
    for r in range(1, min(ws.max_row, max_scan) + 1):
        colmap = {}
        present = set()
        for c in range(1, ws.max_column + 1):
            key = norm(ws.cell(row=r, column=c).value)
            if key is None:
                continue
            colmap.setdefault(key, c)
            if key in req_norm:
                present.add(key)
        if req_norm.issubset(present):
            # 원본 헤더 텍스트(required) 기준으로 열번호 매핑 반환
            out = {}
            for orig in required:
                out[orig] = colmap.get(norm(orig))
            return r, out
    return None, {}


import datetime as _dt
from openpyxl import load_workbook


def _open_first_ws(contents):
    wb = load_workbook(io.BytesIO(contents), data_only=True)
    return wb[wb.sheetnames[0]]


def parse_blog(contents):
    """백록(Blog) → {mix: {lead_time, cancel_window(date), blog_ttl, monthly{month:qty},
    더존코드, 더존업체명, part}}.  PDD 월 기준 버킷, Cancel Window=가장 이른 (PDD-ChangeWindow)."""
    ws = _open_first_ws(contents)
    required = {"PART#", "더존코드", "Qty Due", "PDD", "Change Window",
                "Lead Time Weeks", "Full Manufacturing Cycle Time Weeks", "더존업체명"}
    hdr, col = _find_header_row(ws, required, max_scan=4)
    if hdr is None:
        return {}

    out = {}
    for r in range(hdr + 1, ws.max_row + 1):
        def cv(name):
            c = col.get(name)
            return ws.cell(row=r, column=c).value if c else None

        mix = make_mix(cv("더존코드"), cv("PART#"))
        if not mix:
            continue
        qty = _to_float(cv("Qty Due")) or 0
        pdd = _to_date(cv("PDD"))
        cw_days = _to_float(cv("Change Window"))
        lt1 = _to_float(cv("Lead Time Weeks"))
        lt2 = _to_float(cv("Full Manufacturing Cycle Time Weeks"))

        rec = out.get(mix)
        if rec is None:
            rec = {"lead_time": None, "cancel_window": None, "blog_ttl": 0,
                   "monthly": {}, "더존코드": _code_str(cv("더존코드")),
                   "더존업체명": _s(cv("더존업체명")), "part": _norm_part(cv("PART#"))}
            out[mix] = rec

        rec["blog_ttl"] += qty
        if pdd is not None:
            rec["monthly"][pdd.month] = rec["monthly"].get(pdd.month, 0) + qty

        lt = max([x for x in (lt1, lt2) if x is not None], default=None)
        if lt is not None:
            rec["lead_time"] = lt if rec["lead_time"] is None else max(rec["lead_time"], lt)

        if pdd is not None and cw_days is not None:
            cancel = pdd - _dt.timedelta(days=int(cw_days))
            if rec["cancel_window"] is None or cancel < rec["cancel_window"]:
                rec["cancel_window"] = cancel
    return out


def parse_shipment(contents, cutoff_date=None):
    """출고내역 → {mix: {담당자, 고객, 고객코드, part, y2026}}.
    cutoff_date 지정 시 출고일자 >= cutoff_date 인 행은 2026 합산에서 제외(없으면 2026 전체)."""
    ws = _open_first_ws(contents)
    required = {"고객코드", "고객", "담당자", "품번", "출고일자", "출고수량"}
    hdr, col = _find_header_row(ws, required, max_scan=4)
    if hdr is None:
        return {}

    out = {}
    for r in range(hdr + 1, ws.max_row + 1):
        def cv(name):
            c = col.get(name)
            return ws.cell(row=r, column=c).value if c else None

        mix = make_mix(cv("고객코드"), cv("품번"))
        if not mix:
            continue
        rec = out.get(mix)
        if rec is None:
            rec = {"담당자": _s(cv("담당자")), "고객": _s(cv("고객")),
                   "고객코드": _code_str(cv("고객코드")), "part": _norm_part(cv("품번")),
                   "y2026": 0}
            out[mix] = rec

        d = _to_date(cv("출고일자"))
        qty = _to_float(cv("출고수량")) or 0
        if d is not None and d.year == 2026:
            if cutoff_date is None or d < cutoff_date:
                rec["y2026"] += qty
    return out
