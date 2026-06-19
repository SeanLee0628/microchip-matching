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


def parse_fcst(contents, sheet_name="Sales Revenue"):
    """FCST Sales Revenue → {mix: demand_total 합계}.  MIX = make_mix(Customer Code, MPN)."""
    wb = load_workbook(io.BytesIO(contents), data_only=True)
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
    required = {"Customer Code", "MPN", "Demand Total"}
    hdr, col = _find_header_row(ws, required, max_scan=5)
    if hdr is None:
        return {}

    out = {}
    for r in range(hdr + 1, ws.max_row + 1):
        def cv(name):
            c = col.get(name)
            return ws.cell(row=r, column=c).value if c else None

        mix = make_mix(cv("Customer Code"), cv("MPN"))
        if not mix:
            continue
        demand = _to_float(cv("Demand Total"))
        if demand is None:
            continue
        out[mix] = out.get(mix, 0) + demand
    return out


def _sum_available_qty(ws):
    """재고 워크시트 → {part_norm: available Q'ty 합계}.  헤더 못 찾으면 {}."""
    hdr, col = _find_header_row(ws, {"Part#", "available Q'ty"}, max_scan=4)
    if hdr is None:
        return {}
    out = {}
    pc, qc = col["Part#"], col["available Q'ty"]
    for r in range(hdr + 1, ws.max_row + 1):
        part = _norm_part(ws.cell(row=r, column=pc).value)
        if not part:
            continue
        qty = _to_float(ws.cell(row=r, column=qc).value) or 0
        out[part] = out.get(part, 0) + qty
    return out


def parse_inventory(contents, password=DEFAULT_INVENTORY_PASSWORD,
                    sheet_name="Jun inventory"):
    """암호화된 재고 .xlsx 를 복호화 후 PART#별 available Q'ty 합계.
    복호화 실패 시 ValueError('재고 파일 비밀번호가 올바르지 않습니다')."""
    import msoffcrypto
    dec = io.BytesIO()
    try:
        of = msoffcrypto.OfficeFile(io.BytesIO(contents))
        of.load_key(password=password)
        of.decrypt(dec)
    except Exception:
        # 암호화가 아닐 수도 있음 → 원본 그대로 시도
        dec = io.BytesIO(contents)
    dec.seek(0)
    try:
        wb = load_workbook(dec, data_only=True)
    except Exception:
        raise ValueError("재고 파일 비밀번호가 올바르지 않습니다")
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
    return _sum_available_qty(ws)


def build_records(inventory, fcst, blog, shipment, cutoff_date=None):
    """5개 파서 결과 → (COLUMNS, DASHBOARD_COLUMNS, records[dict]).
    행 집합 = 백록 ∪ 출고내역 ∪ FCST 의 MIX. Q'ty는 PART# 기준 조인.
    cutoff_date 는 parse_shipment 에서 이미 반영되므로 여기선 미사용(서명 호환용)."""
    records = []
    all_mixes = set(blog) | set(shipment) | set(fcst)
    for mix in all_mixes:
        bl = blog.get(mix, {})
        sh = shipment.get(mix, {})
        demand = fcst.get(mix)

        part = sh.get("part") or bl.get("part")
        qty = inventory.get(part) if part else None
        blog_ttl = bl.get("blog_ttl") or 0
        y2026 = sh.get("y2026")

        # Balance = Q'ty + BLOG TTL - Demand. 셋 다 없으면 None.
        if qty is None and blog_ttl == 0 and demand is None:
            balance = None
        else:
            balance = (qty or 0) + blog_ttl - (demand or 0)

        cancel = bl.get("cancel_window")
        rec = {
            "고객코드": sh.get("고객코드") or bl.get("더존코드"),
            "믹스#": mix,
            "담당자": sh.get("담당자"),
            "고객": sh.get("고객") or bl.get("더존업체명"),
            "품번": part,
            "Q'ty": qty,
            "Lead Time": bl.get("lead_time"),
            "Cancel Window": cancel.strftime("%Y-%m-%d") if cancel else None,
            "Demand Total": demand,
            "Balance": balance,
            "2023년": None, "2024년": None, "2025년": None,
            "2026년": y2026,
            "23~25추이": None, "25-26(w/BL)": None,
            "BLOG TTL": blog_ttl,
        }
        monthly = bl.get("monthly", {})
        for label, mnum in MONTH_COLUMNS:
            rec[label] = monthly.get(mnum, 0)
        records.append(rec)

    records.sort(key=lambda r: ((r.get("품번") or ""), (r.get("고객") or "")))
    return COLUMNS, DASHBOARD_COLUMNS, records


def export_workbook(columns, rows):
    """columns/rows → 스타일 적용 xlsx(BytesIO).  1행 그룹헤더 + 2행 컬럼헤더 + 데이터."""
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "마이크로칩(매칭)"

    # 1행: 그룹 헤더
    def col_idx(name):
        return columns.index(name) + 1 if name in columns else None

    y2023 = col_idx("2023년")
    y2026 = col_idx("2026년")
    jun = col_idx("6월")
    mar = col_idx("3월")
    if y2023:
        ws.cell(row=1, column=y2023, value="출하이력")
        if y2026 and y2026 > y2023:
            ws.merge_cells(start_row=1, start_column=y2023, end_row=1, end_column=y2026)
    if jun:
        ws.cell(row=1, column=jun, value="BLOG 2026(PDD기준)")
        if mar and mar > jun:
            ws.merge_cells(start_row=1, start_column=jun, end_row=1, end_column=mar)

    # 2행: 컬럼 헤더
    for i, name in enumerate(columns, start=1):
        ws.cell(row=2, column=i, value=name)

    # 3행~: 데이터
    for ridx, row in enumerate(rows, start=3):
        for i, name in enumerate(columns, start=1):
            ws.cell(row=ridx, column=i, value=row.get(name))

    # 스타일: 헤더(1·2행) 하늘색+볼드+가운데
    sky = PatternFill(start_color="87CEEB", end_color="87CEEB", fill_type="solid")
    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center")
    for r in (1, 2):
        for c in range(1, len(columns) + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = sky
            cell.font = bold
            cell.alignment = center

    # 필터(컬럼 헤더 행부터)
    last_col = get_column_letter(len(columns))
    ws.auto_filter.ref = f"A2:{last_col}2"
    ws.freeze_panes = "A3"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
