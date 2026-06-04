"""자재 출고 자동등록 AI 에이전트 (배치).

흐름:
  사용자가 shipping management 시트에 출고건 직접 입력 → 엑셀 업로드
    → 8열 "AI처리됨" 마커가 빈 행만 골라 처리
    → AI 에이전트가 행마다:
        1. lookup_part: DATECODE에서 Part# 검색, 1실/2실 판단
        2. apply_row: FIFO 분배 → DATECODE 출고 채움 + Apr inventory 일자별 입력 + 8열 "AI처리됨" 마킹
    → 수정된 엑셀 자동 다운로드

원칙:
  - 셀 위치/FIFO/저장은 결정론 (LLM 환각 방지)
  - LLM은 의도 해석 + Part# 후보 결정 + 요약만
  - 실패 행은 8열 빈칸 유지, 경고 리포트
"""
from __future__ import annotations
import io
import json
import os
import re
from datetime import datetime, date

import anthropic
from openpyxl import load_workbook
from openpyxl.workbook import Workbook

# ==================== 시트 상수 ====================
DATECODE_SHEETS = {
    "1실": "DATECODE(영업1실)",
    "2실": "DATECODE(영업2실)",
}
SHIPPING_SHEET = "shipping management"
MARKER_COL = 8
MARKER_VALUE = "AI처리됨"

DC_IN_DATE = 1; DC_IN_SR = 2; DC_IN_PART = 3; DC_IN_QTY = 4
DC_IN_DATECODE = 5; DC_IN_SALES = 6; DC_IN_CUSTOMER = 7; DC_IN_REMARK = 8
DC_STOCK_FORMULA = 9
DC_OUT_DATE = 10; DC_OUT_CUSTOMER = 11; DC_OUT_PART = 12; DC_OUT_QTY = 13
DC_OUT_SALES = 14; DC_OUT_REMARK = 15; DC_OUT_STATUS = 16

APR_TEAM_COL = 2
APR_PART_COL = 7
APR_OUT_DAY1_COL = 62
APR_OUT_DAY31_COL = 92


# ==================== 헬퍼 ====================
def _norm(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _norm_part(p) -> str:
    return re.sub(r"\s+", "", str(p or "")).upper()


def _to_dt(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    # ISO 포맷 우선 (datetime.isoformat() 결과 처리)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _find_apr_sheet(wb: Workbook):
    for name in wb.sheetnames:
        if "inventory" in name.lower() and "warehouse" not in name.lower():
            return name
    return None


# DATECODE footer 라벨 (B열에 등장)
_FOOTER_LABELS = {"입고수량", "출고수량", "출고예정수량", "현재고수량", "사용가능수량"}


def find_datecode_footer_start(ws):
    """DATECODE 시트에서 footer가 시작되는 행 번호 반환.
    footer 패턴: B열에 '입고수량' 등 라벨 + 그 위 1~2행이 합계 수식 (`=SUM(...)`).
    footer 없으면 None.
    """
    last = ws.max_row
    # 끝에서부터 50행 정도 스캔
    label_rows = []
    for r in range(max(2, last - 60), last + 1):
        v = ws.cell(row=r, column=2).value
        if isinstance(v, str) and v.strip() in _FOOTER_LABELS:
            label_rows.append(r)
    if not label_rows:
        return None
    label_start = min(label_rows)
    # 라벨 위에 SUM 수식 행이 있는지 (보통 1~2행 간격)
    footer_start = label_start
    for back in range(1, 4):
        r = label_start - back
        if r < 2:
            break
        d_v = ws.cell(row=r, column=1).value
        p_v = ws.cell(row=r, column=3).value
        any_formula = False
        for col in (9, 8, 17):  # 합계 후보 컬럼
            cv = ws.cell(row=r, column=col).value
            if isinstance(cv, str) and cv.startswith("=SUM"):
                any_formula = True
                break
        # 데이터 행이 아닌데 (date+part 비어있고) 수식이나 빈 행이면 footer로 포함
        if not d_v and not p_v:
            footer_start = r
        else:
            break
    return footer_start


_CELL_REF_RE = re.compile(r"(\$?[A-Z]+)(\$?)(\d+)")


def _shift_formula_rows(formula: str, threshold: int, n: int) -> str:
    """수식 안의 셀 참조 중 row >= threshold인 것들의 row 번호를 +n.
    절대($)/상대 주소 모두 처리. 따옴표 안 텍스트는 무시 안 함 (드물어서 OK).
    """
    def repl(m):
        col_part, dollar, row_str = m.group(1), m.group(2), m.group(3)
        row_n = int(row_str)
        if row_n >= threshold:
            row_n += n
        return f"{col_part}{dollar}{row_n}"
    return _CELL_REF_RE.sub(repl, formula)


def shift_footer_down(ws, footer_start: int, n: int = 1) -> None:
    """footer_start부터 max_row까지 n행 아래로 이동. 수식 안 셀참조도 자동 갱신.
    새 데이터 자리 확보용."""
    if n <= 0:
        return
    last = ws.max_row
    max_col = ws.max_column

    # 1) footer 영역 백업 (값/수식)
    backup = []  # [(orig_row, [val, val, ...]), ...]
    for r in range(footer_start, last + 1):
        row_vals = [ws.cell(row=r, column=c).value for c in range(1, max_col + 1)]
        backup.append((r, row_vals))

    # 2) 원본 footer 자리 모두 비우기
    for r in range(footer_start, last + 1):
        for c in range(1, max_col + 1):
            ws.cell(row=r, column=c, value=None)

    # 3) 새 위치에 다시 쓰기 — 수식은 footer_start 이상 행참조에 +n 적용
    for orig_r, row_vals in backup:
        new_r = orig_r + n
        for c_idx, v in enumerate(row_vals, start=1):
            if isinstance(v, str) and v.startswith("="):
                v = _shift_formula_rows(v, footer_start, n)
            ws.cell(row=new_r, column=c_idx, value=v)


# ==================== 도메인 함수 (결정론) ====================
MAX_FIFO_ROWS_PER_TEAM = 20  # LLM 응답용 상한


def build_datecode_index(wb: Workbook) -> dict:
    """DATECODE 시트들을 한번에 스캔해서 part → [입고행 정보] 인덱스 구축.
    iter_rows(values_only=True)로 빠른 일괄 읽기. 약 30K행을 1~2초에 처리.
    """
    index = {}  # {team: {part_norm: [{row_idx, in_date, in_qty, available, datecode, sr}, ...]}}
    for team, sheet_name in DATECODE_SHEETS.items():
        team_dict = {}
        if sheet_name not in wb.sheetnames:
            index[team] = team_dict
            continue
        ws = wb[sheet_name]
        for r_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or len(row) < DC_IN_PART:
                continue
            part_v = row[DC_IN_PART - 1] if len(row) > DC_IN_PART - 1 else None
            part_norm = _norm_part(part_v)
            if not part_norm:
                continue
            try:
                in_q = float(row[DC_IN_QTY - 1] or 0) if len(row) > DC_IN_QTY - 1 else 0
            except (TypeError, ValueError):
                continue
            in_date_v = row[DC_IN_DATE - 1] if len(row) > DC_IN_DATE - 1 else None
            datecode_v = row[DC_IN_DATECODE - 1] if len(row) > DC_IN_DATECODE - 1 else None
            sr_v = row[DC_IN_SR - 1] if len(row) > DC_IN_SR - 1 else None
            out_qty_v = row[DC_OUT_QTY - 1] if len(row) > DC_OUT_QTY - 1 else None
            out_status_v = row[DC_OUT_STATUS - 1] if len(row) > DC_OUT_STATUS - 1 else None
            try:
                out_q = float(out_qty_v or 0) if out_qty_v and _norm(out_status_v) == "완료" else 0
            except (TypeError, ValueError):
                out_q = 0
            available = in_q - out_q
            if available <= 0:
                continue
            in_date_str = in_date_v.isoformat() if hasattr(in_date_v, "isoformat") else _norm(in_date_v)
            team_dict.setdefault(part_norm, []).append({
                "row_idx": r_idx, "in_date": in_date_str, "in_qty": in_q,
                "datecode": _norm(datecode_v), "available": available,
                "sr": _norm(sr_v),
            })
        # 각 part별 FIFO 정렬
        for rows in team_dict.values():
            rows.sort(key=lambda x: x["in_date"] or "")
        index[team] = team_dict
    return index


def build_apr_index(wb: Workbook):
    """Apr inventory part+team → row_idx 인덱스. 또한 part만으로의 fallback도 포함."""
    apr_name = _find_apr_sheet(wb)
    if not apr_name:
        return None, {}, {}
    ws = wb[apr_name]
    by_part_team = {}
    by_part_only = {}
    for r_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
        if not row or len(row) < APR_PART_COL:
            continue
        part_norm = _norm_part(row[APR_PART_COL - 1] if len(row) > APR_PART_COL - 1 else None)
        team_v = _norm(row[APR_TEAM_COL - 1] if len(row) > APR_TEAM_COL - 1 else None)
        if not part_norm:
            continue
        by_part_team.setdefault((part_norm, team_v), r_idx)
        by_part_only.setdefault(part_norm, r_idx)
    return apr_name, by_part_team, by_part_only


def lookup_part_in_workbook(wb: Workbook, part_query: str, datecode_index: dict | None = None) -> dict:
    """DATECODE 시트에서 Part# 검색. 인덱스를 받으면 즉시 조회, 없으면 빌드."""
    if datecode_index is None:
        datecode_index = build_datecode_index(wb)
    target = _norm_part(part_query)
    result_by_team = {}
    for team, team_dict in datecode_index.items():
        rows = team_dict.get(target)
        if rows:
            result_by_team[team] = rows[:MAX_FIFO_ROWS_PER_TEAM]

    if not result_by_team:
        # Fuzzy 후보 (인덱스의 part 키로 검색)
        candidates = []
        for team, team_dict in datecode_index.items():
            for p in team_dict.keys():
                if target and (target in p or p in target):
                    candidates.append({"team": team, "part": p})
                    if len(candidates) >= 10:
                        break
            if len(candidates) >= 10:
                break
        return {"found": False, "candidates": candidates[:10]}

    return {
        "found": True,
        "teams": list(result_by_team.keys()),
        "rows_by_team": result_by_team,
        "total_available_by_team": {t: sum(r["available"] for r in rs) for t, rs in result_by_team.items()},
    }


def apply_row_to_workbook(
    wb: Workbook,
    sm_row_idx: int,
    team: str,
    allocations: list,
    sm_data: dict,
    apr_ctx: tuple | None = None,  # (apr_name, by_part_team, by_part_only) — 캐시
) -> dict:
    """이미 shipping management에 입력된 행을 기준으로:
       1) DATECODE(team) FIFO 적용
       2) Apr inventory 일자별 셀 적산
       3) shipping management 8열에 'AI처리됨' 마킹
    """
    if team not in DATECODE_SHEETS:
        raise ValueError(f"Unknown team: {team}")
    dc_name = DATECODE_SHEETS[team]
    if dc_name not in wb.sheetnames:
        raise ValueError(f"Sheet '{dc_name}' 없음")
    if apr_ctx is None:
        apr_name, by_part_team, by_part_only = build_apr_index(wb)
    else:
        apr_name, by_part_team, by_part_only = apr_ctx
    if not apr_name:
        raise ValueError("월별 inventory 시트 없음")

    ship_dt = _to_dt(sm_data.get("date"))
    if not ship_dt:
        raise ValueError(f"날짜 파싱 실패: {sm_data.get('date')}")
    qty_total = sum(a["qty"] for a in allocations)
    customer = _norm(sm_data.get("customer"))
    sales = _norm(sm_data.get("sales"))
    part = _norm(sm_data.get("part"))

    diff = {"datecode_updates": [], "apr_updates": [], "marked_sm_row": None}

    # 1) DATECODE 출고 컬럼
    dc_ws = wb[dc_name]
    # footer 위치 1회 감지 (성능 + 일관성)
    footer_start = find_datecode_footer_start(dc_ws)
    for alloc in allocations:
        r = alloc["row_idx"]
        q = alloc["qty"]
        if q <= 0:
            continue
        existing_out = dc_ws.cell(row=r, column=DC_OUT_QTY).value
        if existing_out is None or existing_out == "" or existing_out == 0:
            target_row = r
        else:
            # 이미 출고된 행 → 입고 정보 복사한 새 행 추가 (footer 위에)
            if footer_start is not None:
                # footer 한 칸 아래로 밀고 footer 자리에 새 행
                shift_footer_down(dc_ws, footer_start, n=1)
                target_row = footer_start
                footer_start += 1  # 다음 alloc을 위해 갱신
            else:
                target_row = dc_ws.max_row + 1
            for col in range(1, DC_STOCK_FORMULA):
                dc_ws.cell(row=target_row, column=col, value=dc_ws.cell(row=r, column=col).value)
            dc_ws.cell(row=target_row, column=DC_STOCK_FORMULA,
                       value=f'=IF(P{target_row}="완료",D{target_row}-M{target_row},D{target_row})')
        dc_ws.cell(row=target_row, column=DC_OUT_DATE, value=ship_dt)
        dc_ws.cell(row=target_row, column=DC_OUT_CUSTOMER, value=customer)
        dc_ws.cell(row=target_row, column=DC_OUT_PART, value=part)
        dc_ws.cell(row=target_row, column=DC_OUT_QTY, value=q)
        dc_ws.cell(row=target_row, column=DC_OUT_SALES, value=sales)
        dc_ws.cell(row=target_row, column=DC_OUT_STATUS, value="완료")
        diff["datecode_updates"].append({
            "sheet": dc_name, "row": target_row, "from_input_row": r, "qty": q,
        })

    # 2) Apr inventory 일자별 셀 (인덱스 사용 — O(1))
    apr_ws = wb[apr_name]
    target_team = f"영업{team[0]}실"
    day_of_month = ship_dt.day
    out_col = APR_OUT_DAY1_COL + day_of_month - 1
    part_norm = _norm_part(part)
    matched = by_part_team.get((part_norm, target_team))
    if matched is None:
        matched = by_part_only.get(part_norm)
    if matched:
        cur = apr_ws.cell(row=matched, column=out_col).value
        try:
            cur_n = float(cur) if cur else 0
        except (TypeError, ValueError):
            cur_n = 0
        new_v = cur_n + qty_total
        apr_ws.cell(row=matched, column=out_col, value=new_v)
        diff["apr_updates"].append({
            "sheet": apr_name, "row": matched, "col": out_col,
            "day": day_of_month, "added": qty_total, "total": new_v,
        })
    else:
        diff["apr_updates"].append({
            "warning": f"Apr inventory에 Part# '{part}' 행 없음 — 일자별 셀 미반영",
        })

    # 3) shipping management 마커
    sm_ws = wb[SHIPPING_SHEET]
    sm_ws.cell(row=sm_row_idx, column=MARKER_COL, value=MARKER_VALUE)
    diff["marked_sm_row"] = sm_row_idx
    return diff


# ==================== 미처리 행 수집 ====================
def mark_all_existing_as_processed(wb: Workbook) -> int:
    """기존 shipping management 모든 데이터 행에 8열 'AI처리됨' 마커. 이미 마커 있는 행은 건드리지 않음.
    Returns: 새로 마킹된 행 수.
    """
    if SHIPPING_SHEET not in wb.sheetnames:
        return 0
    sm = wb[SHIPPING_SHEET]
    if not _norm(sm.cell(row=1, column=MARKER_COL).value):
        sm.cell(row=1, column=MARKER_COL, value=MARKER_VALUE)
    marked = 0
    for r in range(2, sm.max_row + 1):
        # 데이터가 있는 행만 (date+part+qty 기준)
        date_v = sm.cell(row=r, column=1).value
        part = sm.cell(row=r, column=3).value
        qty = sm.cell(row=r, column=4).value
        if not (date_v and part and qty):
            continue
        if _norm(sm.cell(row=r, column=MARKER_COL).value):
            continue  # 이미 마킹됨
        sm.cell(row=r, column=MARKER_COL, value=MARKER_VALUE)
        marked += 1
    return marked


def collect_pending_shipments(wb: Workbook, latest_date_only: bool = True) -> list:
    """미처리 행 수집.
    latest_date_only=True: 미처리 행 중 가장 최근 날짜의 행만 반환 (사용자가 방금 추가한 신규건 위주).
    """
    if SHIPPING_SHEET not in wb.sheetnames:
        return []
    sm = wb[SHIPPING_SHEET]
    if not _norm(sm.cell(row=1, column=MARKER_COL).value):
        sm.cell(row=1, column=MARKER_COL, value=MARKER_VALUE)

    all_pending = []
    for r_idx, row in enumerate(sm.iter_rows(min_row=2, values_only=True), start=2):
        if not row:
            continue
        marker = row[MARKER_COL - 1] if len(row) > MARKER_COL - 1 else None
        if _norm(marker):
            continue
        date_v = row[0] if len(row) > 0 else None
        cust = row[1] if len(row) > 1 else None
        part = row[2] if len(row) > 2 else None
        qty = row[3] if len(row) > 3 else None
        if not (date_v and part and qty):
            continue
        try:
            qty_n = float(qty)
        except (TypeError, ValueError):
            continue
        if qty_n <= 0:
            continue
        all_pending.append({
            "sm_row": r_idx,
            "date_raw": date_v,  # 원본 (datetime일 확률 높음) — 정렬용
            "date": date_v.isoformat() if hasattr(date_v, "isoformat") else _norm(date_v),
            "customer": _norm(cust),
            "part": _norm(part),
            "qty": qty_n,
            "sales": _norm(row[4] if len(row) > 4 else None),
            "lot": _norm(row[5] if len(row) > 5 else None),
            "datecode": _norm(row[6] if len(row) > 6 else None),
        })

    if not all_pending or not latest_date_only:
        for p in all_pending:
            p.pop("date_raw", None)
        return all_pending

    # 가장 최근 날짜 찾기 (datetime 비교 가능, 문자열은 ISO 형식)
    def _key(d):
        v = d.get("date_raw")
        if hasattr(v, "timestamp"):
            return v.timestamp()
        return str(v)
    max_key = max(_key(p) for p in all_pending)
    filtered = [p for p in all_pending if _key(p) == max_key]
    for p in filtered:
        p.pop("date_raw", None)
    return filtered


# ==================== AI Agent (배치) ====================
def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def compute_changes_dryrun(file_bytes: bytes, max_rows: int = 500) -> dict:
    """엑셀을 수정하지 않고 '어느 셀에 무엇을 입력해야 하는지'만 계산.
    파일 저장 안 하므로 빠름 (load만 비용).
    """
    import time as _time
    t0 = _time.time()
    wb = load_workbook(io.BytesIO(file_bytes), keep_links=False)
    t_load = _time.time() - t0

    pending = collect_pending_shipments(wb)
    if not pending:
        return {
            "summary": "처리할 미처리 행이 없습니다.",
            "row_results": [], "changes": [],
            "processed_count": 0, "skipped_count": 0, "pending_count": 0,
            "warnings": [],
            "timings": {"load": round(t_load, 2)},
        }

    if len(pending) > max_rows:
        return {
            "summary": (
                f"⚠️ 미처리 행이 {len(pending)}개로 너무 많습니다 (한도: {max_rows}).\n"
                "shipping management 시트에 새로 추가한 행에만 처리가 동작하도록\n"
                "기존 행들 8열 'AI처리됨' 마킹이 필요합니다."
            ),
            "row_results": [], "changes": [],
            "processed_count": 0, "skipped_count": 0, "pending_count": len(pending),
            "warnings": [f"안전 한도({max_rows}) 초과로 처리 중단"],
            "timings": {"load": round(t_load, 2)},
        }

    t1 = _time.time()
    datecode_index = build_datecode_index(wb)
    apr_name, by_part_team, by_part_only = build_apr_index(wb)
    t_index = _time.time() - t1

    # DATECODE footer 위치 (새 행은 그 위에 들어가야 함)
    footer_pos = {team: None for team in DATECODE_SHEETS}
    for team, sheet_name in DATECODE_SHEETS.items():
        if sheet_name in wb.sheetnames:
            footer_pos[team] = find_datecode_footer_start(wb[sheet_name])

    row_results = []
    changes = []  # 전체 셀 변경 목록 (사용자가 수동 입력할 가이드)
    processed = 0
    skipped = 0

    for p in pending:
        lookup = lookup_part_in_workbook(wb, p["part"], datecode_index=datecode_index)
        if not lookup.get("found"):
            cand = lookup.get("candidates", [])
            cand_str = ", ".join(f"{c['team']}/{c['part']}" for c in cand[:3]) if cand else "없음"
            row_results.append({
                **p, "status": "skipped",
                "error": f"DATECODE에 Part# 정확 매칭 없음. 유사 후보: {cand_str}",
            })
            skipped += 1
            continue

        teams = lookup["teams"]
        availability = lookup["total_available_by_team"]
        viable = [t for t in teams if availability[t] >= p["qty"]]
        if not viable:
            total_all = sum(availability.values())
            row_results.append({
                **p, "status": "skipped",
                "error": f"재고 부족: 출고 {p['qty']}, 잔량 {total_all} (1실:{availability.get('1실',0)} / 2실:{availability.get('2실',0)})",
            })
            skipped += 1
            continue
        team = viable[0] if len(viable) == 1 else max(viable, key=lambda t: availability[t])

        # FIFO 분배
        rows_for_team = lookup["rows_by_team"][team]
        allocations = []
        remaining = p["qty"]
        for r in rows_for_team:
            if remaining <= 0:
                break
            take = min(remaining, r["available"])
            allocations.append({"row_idx": r["row_idx"], "qty": take})
            remaining -= take

        if remaining > 0:
            row_results.append({
                **p, "team": team, "status": "skipped",
                "error": f"FIFO 상위 입고로 부족 (남은 {remaining})",
            })
            skipped += 1
            continue

        # 변경사항 계산 (실제 쓰기 안 함)
        ship_dt = _to_dt(p["date"])
        if not ship_dt:
            row_results.append({**p, "team": team, "status": "failed", "error": f"날짜 파싱 실패: {p['date']}"})
            skipped += 1
            continue

        dc_name = DATECODE_SHEETS[team]
        dc_ws = wb[dc_name]
        row_changes = []
        next_footer = footer_pos[team]

        for alloc in allocations:
            r = alloc["row_idx"]
            q = alloc["qty"]
            existing_out = dc_ws.cell(row=r, column=DC_OUT_QTY).value
            if existing_out is None or existing_out == "" or existing_out == 0:
                target_row = r
                is_new = False
            else:
                # footer 위에 새 행 (신규)
                if next_footer is not None:
                    target_row = next_footer
                    next_footer += 1  # 다음 alloc은 +1행 아래
                else:
                    target_row = dc_ws.max_row + 1
                is_new = True
                # 새 행: 입고 정보 복사 필요 (A~H + I=실재고 수식)
                for col in range(1, DC_STOCK_FORMULA):
                    src_v = dc_ws.cell(row=r, column=col).value
                    if src_v is not None:
                        row_changes.append({
                            "sheet": dc_name, "cell": f"{_col_letter(col)}{target_row}",
                            "value": str(src_v) if not hasattr(src_v, "isoformat") else src_v.isoformat()[:10],
                            "note": f"입고정보 복사 (원본 {r}행)",
                        })
                row_changes.append({
                    "sheet": dc_name, "cell": f"I{target_row}",
                    "value": f'=IF(P{target_row}="완료",D{target_row}-M{target_row},D{target_row})',
                    "note": "실재고 수식",
                })
            # 출고 컬럼 (J~P)
            row_changes.append({"sheet": dc_name, "cell": f"J{target_row}", "value": ship_dt.strftime("%Y-%m-%d"), "note": "출고일자"})
            row_changes.append({"sheet": dc_name, "cell": f"K{target_row}", "value": p["customer"], "note": "출고 고객"})
            row_changes.append({"sheet": dc_name, "cell": f"L{target_row}", "value": p["part"], "note": "출고 Part#"})
            row_changes.append({"sheet": dc_name, "cell": f"M{target_row}", "value": str(q), "note": "출고 수량"})
            row_changes.append({"sheet": dc_name, "cell": f"N{target_row}", "value": p.get("sales", ""), "note": "담당 SALES"})
            row_changes.append({"sheet": dc_name, "cell": f"P{target_row}", "value": "완료", "note": "상태"})

        # footer 위치 업데이트 (다음 행 처리에 반영)
        if footer_pos[team] is not None and next_footer != footer_pos[team]:
            footer_pos[team] = next_footer

        # Apr inventory 일자별 셀
        if apr_name:
            target_team_label = f"영업{team[0]}실"
            day_of_month = ship_dt.day
            out_col = APR_OUT_DAY1_COL + day_of_month - 1
            part_norm = _norm_part(p["part"])
            matched = by_part_team.get((part_norm, target_team_label)) or by_part_only.get(part_norm)
            if matched:
                cur = wb[apr_name].cell(row=matched, column=out_col).value
                try:
                    cur_n = float(cur) if cur else 0
                except (TypeError, ValueError):
                    cur_n = 0
                new_v = cur_n + p["qty"]
                row_changes.append({
                    "sheet": apr_name, "cell": f"{_col_letter(out_col)}{matched}",
                    "value": str(int(new_v) if new_v == int(new_v) else new_v),
                    "note": f"{day_of_month}일 출고 (기존 {cur_n} + {p['qty']})",
                })
            else:
                row_changes.append({
                    "sheet": apr_name, "cell": "-",
                    "value": "(매칭 행 없음)",
                    "note": f"Part# '{p['part']}' 행 없음 — Apr inventory 미반영",
                })

        # shipping management 마커
        row_changes.append({
            "sheet": SHIPPING_SHEET, "cell": f"H{p['sm_row']}",
            "value": MARKER_VALUE, "note": "처리완료 표시",
        })

        changes.extend(row_changes)
        row_results.append({**p, "team": team, "status": "ok", "row_changes": row_changes})
        processed += 1

    target_date = pending[0]["date"][:10] if pending else "-"
    summary = (
        f"📅 처리 대상 날짜: {target_date}\n"
        f"미처리 {len(pending)}건 중 처리 가능 {processed}건 / 스킵 {skipped}건\n"
        f"⏱️ 로드 {t_load:.1f}s · 인덱스 {t_index:.1f}s (저장 없음)"
    )

    return {
        "summary": summary,
        "row_results": row_results,
        "changes": changes,
        "processed_count": processed,
        "skipped_count": skipped,
        "pending_count": len(pending),
        "warnings": [],
        "timings": {"load": round(t_load, 2), "index": round(t_index, 2)},
    }


def run_batch_agent(file_bytes: bytes, max_rows: int = 500) -> dict:
    """업로드된 엑셀의 shipping management 미처리 행을 일괄 처리.

    max_rows: 한 번에 처리할 미처리 행의 상한. 초과하면 처리 거부 + 안내.
    """
    # API 키는 AI 위임이 발생할 때만 필요. 결정론 처리만 하면 키 없어도 동작.
    client = None
    # keep_links=False — 외부 참조 무시로 로드 속도 약간 개선
    wb = load_workbook(io.BytesIO(file_bytes), keep_links=False)

    pending = collect_pending_shipments(wb)
    if not pending:
        out_buf = io.BytesIO()
        wb.save(out_buf)
        return {
            "modified_xlsx": out_buf.getvalue(),
            "summary": "처리할 미처리 행이 없습니다.",
            "processed_count": 0, "skipped_count": 0, "pending_count": 0,
            "warnings": [], "agent_log": [], "row_results": [],
        }

    if len(pending) > max_rows:
        # 너무 많음 → 처리 거부 (init-mark 안내)
        return {
            "modified_xlsx": file_bytes,  # 원본 그대로 반환
            "summary": (
                f"⚠️ 미처리 행이 {len(pending)}개로 너무 많습니다 (한도: {max_rows}).\n"
                "→ 우선 [🏷️ 첫 사용: 기존 행 마킹] 버튼으로 누적 데이터를 처리완료 표시한 뒤,\n"
                "  새로 추가한 행만 [AI 자동등록 실행]으로 처리하세요."
            ),
            "processed_count": 0, "skipped_count": 0, "pending_count": len(pending),
            "warnings": [f"안전 한도({max_rows}) 초과로 처리 중단"],
            "agent_log": [], "row_results": [],
        }

    # ===== 인덱스 1회 빌드 (큰 파일 속도 핵심) =====
    import time as _time
    t0 = _time.time()
    datecode_index = build_datecode_index(wb)
    apr_ctx = build_apr_index(wb)
    t1 = _time.time()

    # ===== 1단계: 결정론 처리 =====
    row_results = []
    processed_sm_rows = set()
    needs_ai_rows = []

    for p in pending:
        try:
            lookup = lookup_part_in_workbook(wb, p["part"], datecode_index=datecode_index)
        except Exception as e:
            row_results.append({**p, "status": "failed", "error": f"lookup 오류: {e}"})
            continue

        if not lookup.get("found"):
            # 정확 매칭 없음 → 즉시 스킵 (AI 호출 안 함; 후보는 참고용)
            cand = lookup.get("candidates", [])
            cand_str = ", ".join(f"{c['team']}/{c['part']}" for c in cand[:3]) if cand else "없음"
            row_results.append({
                **p, "status": "skipped",
                "error": f"DATECODE에 Part# 정확 매칭 없음. 유사 후보: {cand_str}",
            })
            continue

        teams = lookup["teams"]
        availability = lookup["total_available_by_team"]
        # 잔량 충분한 team 우선
        viable = [t for t in teams if availability[t] >= p["qty"]]
        if not viable:
            total_all = sum(availability.values())
            row_results.append({
                **p, "status": "skipped",
                "error": f"재고 부족: 출고 {p['qty']}, 총 잔량 {total_all} (1실:{availability.get('1실',0)} / 2실:{availability.get('2실',0)})",
            })
            continue

        if len(viable) == 1:
            team = viable[0]
        else:
            # 여러 실 모두 충분 → AI에게 결정 위임 (일단 잔량 큰 쪽)
            team = max(viable, key=lambda t: availability[t])

        # FIFO 분배
        rows = lookup["rows_by_team"][team]
        allocations = []
        remaining = p["qty"]
        for r in rows:
            if remaining <= 0:
                break
            take = min(remaining, r["available"])
            allocations.append({"row_idx": r["row_idx"], "qty": take})
            remaining -= take

        if remaining > 0:
            row_results.append({
                **p, "team": team, "status": "skipped",
                "error": f"FIFO 상위 입고로 부족 (남은 {remaining}). 수동 처리 필요.",
            })
            continue

        try:
            diff = apply_row_to_workbook(
                wb, sm_row_idx=p["sm_row"], team=team,
                allocations=allocations, sm_data=p,
                apr_ctx=apr_ctx,
            )
            # 인덱스 갱신: 분배된 입고행의 available 차감
            for alloc in allocations:
                for entry in datecode_index[team].get(_norm_part(p["part"]), []):
                    if entry["row_idx"] == alloc["row_idx"]:
                        entry["available"] -= alloc["qty"]
                        break
            processed_sm_rows.add(p["sm_row"])
            row_results.append({**p, "team": team, "status": "processed", "diff": diff})
        except Exception as e:
            row_results.append({**p, "status": "failed", "error": str(e)})

    # ===== 2단계: AI 보조 (예외 케이스만) =====
    summary = ""
    warnings = []
    agent_log = []

    if needs_ai_rows:
        CHUNK_SIZE = 8
        chunks = [needs_ai_rows[i:i + CHUNK_SIZE] for i in range(0, len(needs_ai_rows), CHUNK_SIZE)]
    else:
        chunks = []

    tools = [
        {
            "name": "lookup_part",
            "description": "DATECODE 시트에서 Part# 검색. 어느 영업실(1실/2실)에 있는지 + 잔량 있는 입고행을 FIFO 정렬로 반환. 정확 매칭 안 되면 candidates 반환.",
            "input_schema": {
                "type": "object",
                "properties": {"part_number": {"type": "string"}},
                "required": ["part_number"],
            },
        },
        {
            "name": "apply_row",
            "description": "shipping management의 미처리 행 1건을 처리. team/Part#/FIFO allocations(입고행 row_idx와 분배수량) 받아 DATECODE 출고 채움 + Apr inventory 일자별 셀 가산 + SM 8열 'AI처리됨' 마킹.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "sm_row": {"type": "integer", "description": "shipping management의 처리 대상 행 번호"},
                    "team": {"type": "string", "enum": ["1실", "2실"]},
                    "allocations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "row_idx": {"type": "integer", "description": "DATECODE 시트의 입고행 번호"},
                                "qty": {"type": "number"},
                            },
                            "required": ["row_idx", "qty"],
                        },
                    },
                },
                "required": ["sm_row", "team", "allocations"],
            },
        },
    ]

    system = """당신은 자재 출고를 자동 처리하는 AI 에이전트입니다.

받은 미처리 행 목록을 순서대로 처리하세요. 각 행마다:
1. lookup_part(part_number)로 어느 영업실(1실/2실)에 있고 잔량이 충분한지 확인.
2. found=False면 candidates 출력하고 그 행은 skip (apply_row 호출 X).
3. teams 두 곳 모두 있으면 잔량 더 많은 쪽 선택.
4. allocations 계산: FIFO (in_date 오래된 순), 출고수량을 잔량 한도 내 분배.
5. 잔량 합계 < 출고수량이면 그 행은 skip (apply_row 호출 X).
6. 충분하면 apply_row(sm_row, team, allocations) 호출.

모든 행 처리가 끝나면 한국어로 결과 요약 (처리 N건, 스킵 M건, 사유).

주의:
- row_idx, sm_row는 lookup_part 응답 또는 입력 목록의 정확한 값만 사용 (절대 추측 금지).
- 한 행 처리 후 다음 행으로 진행."""

    for chunk_idx, chunk in enumerate(chunks):
        chunk_label = f"청크 {chunk_idx + 1}/{len(chunks)} ({len(chunk)}행)"
        user_msg = f"""미처리 출고 행 {len(chunk)}건을 처리해주세요 ({chunk_label}):

{json.dumps(chunk, ensure_ascii=False, indent=2)}"""
        messages = [{"role": "user", "content": user_msg}]

        for step in range(30):
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=4000,
                    system=system,
                    tools=tools,
                    messages=messages,
                )
            except Exception as e:
                warnings.append(f"{chunk_label} API 오류 step {step}: {e}")
                break
            agent_log.append({"chunk": chunk_idx, "step": step, "stop_reason": resp.stop_reason})
            for blk in resp.content:
                if blk.type == "text" and blk.text.strip():
                    summary = blk.text.strip()
            if resp.stop_reason == "end_turn":
                break
            if resp.stop_reason != "tool_use":
                break

            tool_results = []
            assistant_blocks = list(resp.content)
            for blk in resp.content:
                if blk.type != "tool_use":
                    continue
                name = blk.name
                args = blk.input
                try:
                    if name == "lookup_part":
                        out = lookup_part_in_workbook(wb, args.get("part_number", ""), datecode_index=datecode_index)
                    elif name == "apply_row":
                        sm_row = args["sm_row"]
                        sm_data = next((p for p in chunk if p["sm_row"] == sm_row), None)
                        if sm_data is None:
                            out = {"error": f"sm_row {sm_row}이 현재 청크 미처리 목록에 없음"}
                        else:
                            out = apply_row_to_workbook(
                                wb, sm_row_idx=sm_row,
                                team=args["team"],
                                allocations=args["allocations"],
                                sm_data=sm_data,
                                apr_ctx=apr_ctx,
                            )
                            processed_sm_rows.add(sm_row)
                            row_results.append({
                                "sm_row": sm_row,
                                "part": sm_data["part"],
                                "qty": sm_data["qty"],
                                "customer": sm_data.get("customer"),
                                "date": sm_data.get("date"),
                                "team": args["team"],
                                "status": "processed",
                                "diff": out,
                            })
                    else:
                        out = {"error": f"unknown tool: {name}"}
                except Exception as e:
                    out = {"error": str(e)}
                    warnings.append(f"{name}({args}) 실패: {e}")
                    if name == "apply_row":
                        row_results.append({
                            "sm_row": args.get("sm_row"),
                            "status": "failed",
                            "error": str(e),
                        })
                agent_log.append({"chunk": chunk_idx, "tool": name, "result": "error" if isinstance(out, dict) and "error" in out else "ok"})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": blk.id,
                    "content": json.dumps(out, ensure_ascii=False, default=str),
                })
            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_results})

    skipped_rows = [p["sm_row"] for p in pending if p["sm_row"] not in processed_sm_rows]
    for sm_row in skipped_rows:
        if not any(rr.get("sm_row") == sm_row for rr in row_results):
            sm_data = next((p for p in pending if p["sm_row"] == sm_row), None)
            row_results.append({
                "sm_row": sm_row, "status": "skipped",
                "part": sm_data.get("part") if sm_data else None,
                "qty": sm_data.get("qty") if sm_data else None,
                "customer": sm_data.get("customer") if sm_data else None,
                "date": sm_data.get("date") if sm_data else None,
            })

    t2 = _time.time()
    out_buf = io.BytesIO()
    wb.save(out_buf)
    t3 = _time.time()

    target_date = pending[0]["date"][:10] if pending else "-"
    final_summary = (
        f"📅 처리 대상 날짜: {target_date} (가장 최근 미처리 행)\n"
        f"미처리 {len(pending)}건 중 처리 {len(processed_sm_rows)}건 / 스킵 {len(skipped_rows)}건"
        + (f"\n에이전트 메시지: {summary}" if summary else "")
        + f"\n⏱️ 인덱스 {t1-t0:.1f}s · 처리 {t2-t1:.1f}s · 저장 {t3-t2:.1f}s"
    )
    return {
        "modified_xlsx": out_buf.getvalue(),
        "summary": final_summary,
        "processed_count": len(processed_sm_rows),
        "skipped_count": len(skipped_rows),
        "pending_count": len(pending),
        "warnings": warnings,
        "agent_log": agent_log,
        "row_results": row_results,
    }
