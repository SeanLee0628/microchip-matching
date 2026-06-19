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
