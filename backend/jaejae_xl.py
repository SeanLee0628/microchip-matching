"""xlwings 기반 자재 출고 자동등록 — 열려 있는 Excel을 직접 수정.

사용법:
  1) Excel에서 작업 파일 열기
  2) shipping management 시트에 신규 출고건 입력
  3) `python jaejae_xl.py` 실행 (현재 활성 워크북 자동 인식)
     또는 `python jaejae_xl.py "경로"` 로 특정 파일 지정

xlwings는 Excel 본체를 엔진으로 쓰므로:
  - 수식/차트/조건부서식/매크로 모두 보존
  - 셀 입력 즉시 화면에 반영
  - 파일 저장 비용 거의 0 (필요 시 사용자가 Ctrl+S)
"""
from __future__ import annotations
import re
import sys
from datetime import datetime, date

import xlwings as xw
from datetime import datetime as _dt

# 날짜 셀(진짜 날짜 / 텍스트로 친 날짜)을 비교 가능한 timestamp(float)로 통일.
# 파싱 불가하면 None → '최신 날짜' 후보에서 자연히 제외됨.
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y", "%Y%m%d")


def _date_ts(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):          # datetime/Timestamp
        return v.timestamp()
    s = str(v).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return _dt.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


# ========== 시트/컬럼 상수 ==========
DC_SHEETS = {"1실": "DATECODE(영업1실)", "2실": "DATECODE(영업2실)"}
SM_SHEET = "shipping management"
MARKER_COL = 8
MARKER_VALUE = "AI처리됨"

# 입고 컬럼은 양쪽 동일 (A~D)
DC_IN_DATE, DC_IN_SR, DC_IN_PART, DC_IN_QTY = 1, 2, 3, 4
DC_IN_DATECODE = 5

# 영업1실/2실은 컬럼 구조가 다름 (출고 시작 위치 1칸 차이)
DC_COLS = {
    "1실": {
        "in_remark_end": 8,   # A~H 입고측 정보 끝 (입고복사 시 사용)
        "stock": 9,           # I = 실재고 수식
        "out_date": 10, "out_cust": 11, "out_part": 12, "out_qty": 13,
        "out_sales": 14, "out_remark": 15, "out_status": 16,
    },
    "2실": {
        "in_remark_end": 7,   # A~G
        "stock": 8,           # H = 실재고 수식
        "out_date": 9, "out_cust": 10, "out_part": 11, "out_qty": 12,
        "out_sales": 13, "out_remark": 14, "out_status": 15,
    },
}

APR_TEAM_COL, APR_PART_COL = 2, 7
APR_OUT_DAY1_COL = 62  # BJ = 1일

_FOOTER_LABELS = {"입고수량", "출고수량", "출고예정수량", "현재고수량", "사용가능수량"}


def _norm(v):
    return "" if v is None else str(v).strip()


def _norm_part(p):
    return re.sub(r"\s+", "", str(p or "")).upper()


def _to_dt(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _find_apr_sheet(wb):
    for s in wb.sheets:
        n = s.name.lower()
        if "inventory" in n and "warehouse" not in n:
            return s
    return None


# ========== 인덱스 빌드 (전체 시트 한번에 읽기 — xlwings에서 빠른 방법) ==========
def build_datecode_index(wb):
    """{team: {part_norm: [{row, in_date, in_qty, available, datecode, sr}, ...]}}"""
    index = {}
    for team, name in DC_SHEETS.items():
        cols = DC_COLS[team]
        out_qty_col = cols["out_qty"]
        out_status_col = cols["out_status"]
        max_col = out_status_col

        team_dict = {}
        try:
            sht = wb.sheets[name]
        except Exception:
            index[team] = team_dict
            continue
        last = sht.used_range.last_cell.row
        if last < 2:
            index[team] = team_dict
            continue
        data = sht.range((2, 1), (last, max_col)).value
        if not isinstance(data, list):
            data = [data]
        if data and not isinstance(data[0], list):
            data = [data]
        for offset, row in enumerate(data):
            r = 2 + offset
            if not row:
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
            out_qty_v = row[out_qty_col - 1] if len(row) > out_qty_col - 1 else None
            out_status_v = row[out_status_col - 1] if len(row) > out_status_col - 1 else None
            try:
                out_q = float(out_qty_v or 0) if out_qty_v and _norm(out_status_v) == "완료" else 0
            except (TypeError, ValueError):
                out_q = 0
            available = in_q - out_q
            if available <= 0:
                continue
            in_date_str = in_date_v.isoformat() if hasattr(in_date_v, "isoformat") else _norm(in_date_v)
            team_dict.setdefault(part_norm, []).append({
                "row": r, "in_date": in_date_str, "in_qty": in_q,
                "datecode": _norm(datecode_v), "available": available, "sr": _norm(sr_v),
            })
        for rows in team_dict.values():
            rows.sort(key=lambda x: x["in_date"] or "")
        index[team] = team_dict
    return index


def build_apr_index(wb):
    sht = _find_apr_sheet(wb)
    if not sht:
        return None, {}, {}
    last = sht.used_range.last_cell.row
    if last < 3:
        return sht, {}, {}
    cols_to_read = max(APR_PART_COL, APR_TEAM_COL)
    data = sht.range((3, 1), (last, cols_to_read)).value
    if not isinstance(data, list):
        data = [data]
    if data and not isinstance(data[0], list):
        data = [data]
    by_part_team, by_part_only = {}, {}
    for offset, row in enumerate(data):
        r = 3 + offset
        if not row:
            continue
        part_norm = _norm_part(row[APR_PART_COL - 1] if len(row) > APR_PART_COL - 1 else None)
        team_v = _norm(row[APR_TEAM_COL - 1] if len(row) > APR_TEAM_COL - 1 else None)
        if not part_norm:
            continue
        by_part_team.setdefault((part_norm, team_v), r)
        by_part_only.setdefault(part_norm, r)
    return sht, by_part_team, by_part_only


# ========== Pending 수집 ==========
def collect_pending(wb, latest_date_only=True):
    try:
        sht = wb.sheets[SM_SHEET]
    except Exception:
        return []
    # 헤더에 마커 컬럼 추가
    if not _norm(sht.cells(1, MARKER_COL).value):
        sht.cells(1, MARKER_COL).value = MARKER_VALUE
    last = sht.used_range.last_cell.row
    if last < 2:
        return []
    # A~H 한번에 읽기
    data = sht.range((2, 1), (last, MARKER_COL)).value
    if not isinstance(data, list):
        data = [data]
    if data and not isinstance(data[0], list):
        data = [data]
    all_pending = []
    for offset, row in enumerate(data):
        r = 2 + offset
        if not row:
            continue
        marker = row[MARKER_COL - 1] if len(row) > MARKER_COL - 1 else None
        if _norm(marker):
            continue
        date_v = row[0]
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
            "sm_row": r,
            "date_raw": date_v,
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

    keyed = [(p, _date_ts(p.get("date_raw"))) for p in all_pending]
    valid = [ts for _, ts in keyed if ts is not None]
    if not valid:
        # 날짜를 하나도 못 읽으면 필터 없이 전부 반환
        for p in all_pending:
            p.pop("date_raw", None)
        return all_pending
    max_key = max(valid)
    filtered = [p for p, ts in keyed if ts == max_key]
    for p in filtered:
        p.pop("date_raw", None)
    return filtered


def collect_recent_processed(wb, max_rows: int = 20) -> list:
    """최근 처리된 행 (AI처리됨 마커 있음) 중 가장 최근 날짜 기준."""
    try:
        sht = wb.sheets[SM_SHEET]
    except Exception:
        return []
    last = sht.used_range.last_cell.row
    if last < 2:
        return []
    data = sht.range((2, 1), (last, MARKER_COL)).value
    if not isinstance(data, list):
        data = [data]
    if data and not isinstance(data[0], list):
        data = [data]
    items = []
    for offset, row in enumerate(data):
        r = 2 + offset
        if not row:
            continue
        marker = row[MARKER_COL - 1] if len(row) > MARKER_COL - 1 else None
        if not _norm(marker):
            continue
        date_v = row[0]
        part = row[2] if len(row) > 2 else None
        qty = row[3] if len(row) > 3 else None
        if not (date_v and part and qty):
            continue
        try:
            qty_n = float(qty)
        except (TypeError, ValueError):
            continue
        items.append({
            "sm_row": r,
            "date_raw": date_v,
            "date": date_v.isoformat() if hasattr(date_v, "isoformat") else _norm(date_v),
            "customer": _norm(row[1] if len(row) > 1 else None),
            "part": _norm(part),
            "qty": qty_n,
            "sales": _norm(row[4] if len(row) > 4 else None),
        })
    if not items:
        return []
    # 가장 최근 날짜만
    def _key(d):
        v = d.get("date_raw")
        return v.timestamp() if hasattr(v, "timestamp") else str(v)
    max_key = max(_key(p) for p in items)
    filtered = [p for p in items if _key(p) == max_key]
    for p in filtered:
        p.pop("date_raw", None)
    # 행 번호 큰 순 (최근 추가 순)
    filtered.sort(key=lambda x: -x["sm_row"])
    return filtered[:max_rows]


# ========== Footer 감지 ==========
def find_footer_start(sht):
    last = sht.used_range.last_cell.row
    start = max(2, last - 60)
    # B열 한 번에 읽기
    bvals = sht.range((start, 2), (last, 2)).value
    if not isinstance(bvals, list):
        bvals = [bvals]
    label_rows = []
    for i, v in enumerate(bvals):
        if isinstance(v, str) and v.strip() in _FOOTER_LABELS:
            label_rows.append(start + i)
    if not label_rows:
        return None
    label_start = min(label_rows)
    footer_start = label_start
    for back in range(1, 4):
        r = label_start - back
        if r < 2:
            break
        d_v = sht.cells(r, 1).value
        p_v = sht.cells(r, 3).value
        if not d_v and not p_v:
            footer_start = r
        else:
            break
    return footer_start


# ========== 스냅샷 직렬화 ==========
def _serialize(v):
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, date):
        return {"__d__": v.isoformat()}
    return v


def _deserialize(v):
    if isinstance(v, dict):
        if "__dt__" in v:
            return datetime.fromisoformat(v["__dt__"])
        if "__d__" in v:
            return date.fromisoformat(v["__d__"])
    return v


def _capture_write(snapshot, sheet, row, col, sht, after=None, note=None):
    """셀 쓰기 전 현재 값 백업 + after/note 기록."""
    before = sht.cells(row, col).value
    snapshot["writes"].append({
        "sheet": sheet, "row": row, "col": col,
        "cell": f"{_col_letter_inline(col)}{row}",
        "before": _serialize(before),
        "after": _serialize(after) if after is not None else None,
        "note": note,
    })


# ========== 출고 적용 (xlwings — Excel이 행 삽입/수식 처리) ==========
def apply_one_row(wb, p, team, allocations, apr_ctx):
    dc_name = DC_SHEETS[team]
    dc = wb.sheets[dc_name]
    cols = DC_COLS[team]
    apr_sht, by_part_team, by_part_only = apr_ctx
    ship_dt = _to_dt(p["date"])
    if not ship_dt:
        raise ValueError(f"날짜 파싱 실패: {p['date']}")
    qty_total = sum(a["qty"] for a in allocations)

    snapshot = {"writes": [], "inserts": []}
    diff = {"datecode_writes": [], "apr_write": None, "marker": None, "snapshot": snapshot}

    # 셀 주소 letter (실재고/상태 수식용)
    stock_letter = _col_letter_inline(cols["stock"])
    qty_letter = _col_letter_inline(DC_IN_QTY)
    out_qty_letter = _col_letter_inline(cols["out_qty"])
    out_status_letter = _col_letter_inline(cols["out_status"])

    # 1) DATECODE
    footer_start = find_footer_start(dc)
    for alloc in allocations:
        r = alloc["row"]
        q = alloc["qty"]
        existing_out = dc.cells(r, cols["out_qty"]).value
        if existing_out is None or existing_out == "" or existing_out == 0:
            target_row = r
            is_insert = False
        else:
            # footer 위에 새 행 삽입
            if footer_start is not None:
                dc.api.Rows(footer_start).Insert()
                target_row = footer_start
                snapshot["inserts"].append({"sheet": dc_name, "row": target_row})
                # 입고 정보 복사 (A~in_remark_end)
                src_vals = dc.range((r, 1), (r, cols["in_remark_end"])).value
                dc.range((target_row, 1), (target_row, cols["in_remark_end"])).value = src_vals
                # 실재고 수식
                dc.cells(target_row, cols["stock"]).value = (
                    f'=IF({out_status_letter}{target_row}="완료",'
                    f'{qty_letter}{target_row}-{out_qty_letter}{target_row},'
                    f'{qty_letter}{target_row})'
                )
                footer_start += 1
                is_insert = True
            else:
                target_row = dc.cells.last_cell.row + 1
                is_insert = True
        # 출고 컬럼 입력 (팀별 매핑 적용)
        out_cols = [
            (cols["out_date"], ship_dt, "출고일자"),
            (cols["out_cust"], p["customer"], "출고 고객"),
            (cols["out_part"], p["part"], "출고 Part#"),
            (cols["out_qty"], q, "출고 수량"),
            (cols["out_sales"], p.get("sales", ""), "담당 SALES"),
            (cols["out_status"], "완료", "상태"),
        ]
        for col, val, note in out_cols:
            note_full = f"{note}{' (신규 행)' if is_insert else ''}"
            _capture_write(snapshot, dc_name, target_row, col, dc, after=val, note=note_full)
            dc.cells(target_row, col).value = val
        if is_insert:
            for src_col, label in [
                (DC_IN_DATE, "입고일자(복사)"), (DC_IN_SR, "SR#(복사)"),
                (DC_IN_PART, "Part#(복사)"), (DC_IN_QTY, "입고수량(복사)"),
                (DC_IN_DATECODE, "DATECODE(복사)"),
            ]:
                src_val = dc.cells(target_row, src_col).value
                if src_val is not None:
                    snapshot["writes"].append({
                        "sheet": dc_name, "row": target_row, "col": src_col,
                        "cell": f"{_col_letter_inline(src_col)}{target_row}",
                        "before": None, "after": _serialize(src_val), "note": label,
                    })
            snapshot["writes"].append({
                "sheet": dc_name, "row": target_row, "col": cols["stock"],
                "cell": f"{stock_letter}{target_row}",
                "before": None,
                "after": (f'=IF({out_status_letter}{target_row}="완료",'
                          f'{qty_letter}{target_row}-{out_qty_letter}{target_row},'
                          f'{qty_letter}{target_row})'),
                "note": "실재고 수식",
            })
        diff["datecode_writes"].append({"sheet": dc_name, "row": target_row, "qty": q, "from_input_row": r, "inserted": is_insert})

    # 2) Apr inventory 일자별 셀 (기존 셀 업데이트 — 백업)
    if apr_sht:
        target_team_label = f"영업{team[0]}실"
        day_of_month = ship_dt.day
        out_col = APR_OUT_DAY1_COL + day_of_month - 1
        part_norm = _norm_part(p["part"])
        matched = by_part_team.get((part_norm, target_team_label)) or by_part_only.get(part_norm)
        if matched:
            cur = apr_sht.cells(matched, out_col).value
            try:
                cur_n = float(cur) if cur else 0
            except (TypeError, ValueError):
                cur_n = 0
            new_v = cur_n + qty_total
            _capture_write(snapshot, apr_sht.name, matched, out_col, apr_sht, after=new_v,
                           note=f"{day_of_month}일 출고 (+{qty_total})")
            apr_sht.cells(matched, out_col).value = new_v
            diff["apr_write"] = {"sheet": apr_sht.name, "row": matched, "col": out_col, "day": day_of_month, "added": qty_total, "total": new_v}
        else:
            diff["apr_write"] = {"warning": f"Part# '{p['part']}' Apr inventory 매칭 없음"}

    # 3) SM 마커 (기존 셀, 백업)
    sm = wb.sheets[SM_SHEET]
    _capture_write(snapshot, SM_SHEET, p["sm_row"], MARKER_COL, sm,
                   after=MARKER_VALUE, note="처리완료 마커")
    sm.cells(p["sm_row"], MARKER_COL).value = MARKER_VALUE
    diff["marker"] = {"sheet": SM_SHEET, "row": p["sm_row"]}
    return diff


# ========== 백업 ==========
def backup_workbook(wb):
    import os
    full = wb.fullname
    if not full or not os.path.exists(full):
        return None
    base, ext = os.path.splitext(full)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{base}_백업_{ts}{ext}"
    try:
        wb.api.SaveCopyAs(path)
        return path
    except Exception as e:
        return f"FAIL:{e}"


# ========== 행별 되돌리기 ==========
def undo_row_snapshot(snapshot: dict, book_name: str | None = None) -> dict:
    """단일 행의 처리를 되돌림. 활성 워크북에서 동작.
    inserted 행은 삭제, writes는 before 값 복원.
    LIFO 순(최신부터)으로 되돌리는 게 안전.
    """
    if book_name:
        try:
            wb = xw.books[book_name]
        except Exception:
            return {"error": f"워크북 '{book_name}' 못 찾음"}
    else:
        try:
            wb = xw.books.active
        except Exception:
            return {"error": "활성 워크북 없음"}

    app = wb.app
    prev_screen = app.screen_updating
    prev_calc = app.calculation
    app.screen_updating = False
    app.calculation = "manual"
    deleted_rows = []
    restored = []
    try:
        # inserted 행 삭제 (역순: 행번호 큰 것부터 → 작은 행 삭제 후에도 큰 행 인덱스 유효)
        inserts = snapshot.get("inserts", [])
        for ins in sorted(inserts, key=lambda x: -x["row"]):
            try:
                wb.sheets[ins["sheet"]].api.Rows(ins["row"]).Delete()
                deleted_rows.append(f"{ins['sheet']}!{ins['row']}")
            except Exception as e:
                return {"error": f"행 삭제 실패 {ins}: {e}"}
        # 셀 복원 (insert된 행 안의 write는 행 삭제로 이미 처리됨)
        inserted_keys = {(i["sheet"], i["row"]) for i in inserts}
        for w in snapshot.get("writes", []):
            if (w["sheet"], w["row"]) in inserted_keys:
                continue
            try:
                v = _deserialize(w["before"])
                wb.sheets[w["sheet"]].cells(w["row"], w["col"]).value = v
                restored.append(f"{w['sheet']}!{_col_letter_inline(w['col'])}{w['row']}")
            except Exception as e:
                return {"error": f"셀 복원 실패 {w}: {e}"}
    finally:
        app.calculation = prev_calc
        app.screen_updating = prev_screen
        try:
            app.calculate()
        except Exception:
            pass
    return {
        "ok": True,
        "deleted_rows": deleted_rows,
        "restored_cells": restored,
        "summary": f"삭제 {len(deleted_rows)}행 / 복원 {len(restored)}셀",
    }


def _col_letter_inline(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ========== 메인 ==========
def process(book_path: str | None = None, save: bool = False, verbose: bool = True) -> dict:
    """현재 활성 Excel 또는 지정 경로 파일을 처리. 결과 dict 반환."""
    import time
    t0 = time.time()
    log = (lambda m: print(m)) if verbose else (lambda m: None)

    if book_path:
        wb = xw.Book(book_path)
    else:
        try:
            wb = xw.books.active
        except Exception:
            return {"error": "활성 Excel 워크북이 없습니다. 파일을 먼저 여세요."}
    if wb is None:
        return {"error": "워크북 없음"}

    app = wb.app
    prev_screen = app.screen_updating
    prev_calc = app.calculation
    app.screen_updating = False
    app.calculation = "manual"

    results = []
    target_date = None
    backup_path = None
    try:
        pending = collect_pending(wb)
        if not pending:
            # 미처리는 없음 — 최근 처리된 행을 정보용으로 보여줌
            recent = collect_recent_processed(wb)
            recent_results = [
                {
                    "sm_row": r["sm_row"], "part": r["part"], "qty": r["qty"],
                    "customer": r["customer"], "status": "already_processed",
                    "date": r["date"][:10],
                }
                for r in recent
            ]
            recent_summary = (
                f"처리할 새 행이 없습니다.\n"
                + (f"📅 가장 최근 처리: {recent[0]['date'][:10]} ({len(recent)}건 — 아래는 참고용)" if recent else "이전 처리 내역도 없습니다.")
            )
            return {
                "summary": recent_summary,
                "processed_count": 0, "skipped_count": 0,
                "results": recent_results, "book": wb.name,
            }
        target_date = pending[0]["date"][:10]
        log(f"📅 처리 대상: {target_date} (미처리 {len(pending)}건)")

        # 처리 직전 백업
        backup_path = backup_workbook(wb)
        if backup_path and not str(backup_path).startswith("FAIL"):
            log(f"💾 백업: {backup_path}")
        elif backup_path:
            log(f"⚠️ 백업 실패: {backup_path}")

        t_idx = time.time()
        dc_idx = build_datecode_index(wb)
        apr_ctx = build_apr_index(wb)
        log(f"⏱️ 인덱스 빌드: {time.time()-t_idx:.1f}s")

        ok, skip = 0, 0
        for p in pending:
            target = _norm_part(p["part"])
            found = {}
            for team in ("1실", "2실"):
                rows = dc_idx.get(team, {}).get(target)
                if rows:
                    found[team] = rows

            base = {"sm_row": p["sm_row"], "part": p["part"], "qty": p["qty"], "customer": p["customer"]}

            if not found:
                results.append({**base, "status": "skipped", "error": "DATECODE 매칭 없음"})
                log(f"  ❌ SM {p['sm_row']} {p['part']}: DATECODE 매칭 없음")
                skip += 1
                continue
            availability = {t: sum(r["available"] for r in rs) for t, rs in found.items()}
            viable = [t for t in found if availability[t] >= p["qty"]]
            if not viable:
                results.append({**base, "status": "skipped",
                                "error": f"재고 부족 (잔량 {sum(availability.values())} < {p['qty']})"})
                log(f"  ❌ SM {p['sm_row']} {p['part']}: 재고 부족")
                skip += 1
                continue
            team = viable[0] if len(viable) == 1 else max(viable, key=lambda t: availability[t])

            # FIFO 분배
            allocations = []
            remaining = p["qty"]
            for r in found[team]:
                if remaining <= 0:
                    break
                take = min(remaining, r["available"])
                allocations.append({"row": r["row"], "qty": take})
                remaining -= take

            try:
                diff = apply_one_row(wb, p, team, allocations, apr_ctx)
                # 인덱스 갱신
                for alloc in allocations:
                    for entry in dc_idx[team].get(target, []):
                        if entry["row"] == alloc["row"]:
                            entry["available"] -= alloc["qty"]
                            break
                # 간략 변경 요약
                dc_writes = diff.get("datecode_writes", [])
                dc_summary = ", ".join(f"{w['sheet']}!{w['row']}행" for w in dc_writes) if dc_writes else "-"
                apr_w = diff.get("apr_write")
                if apr_w and "warning" not in apr_w:
                    apr_summary = f"{apr_w['sheet']}!{_col_letter(apr_w['col'])}{apr_w['row']} (+{int(apr_w['added'])})"
                elif apr_w:
                    apr_summary = f"⚠️ {apr_w.get('warning')}"
                else:
                    apr_summary = "-"
                marker = diff.get("marker") or {}
                marker_summary = f"H{marker.get('row')}" if marker else "-"
                results.append({
                    **base, "team": team, "status": "ok",
                    "datecode_summary": dc_summary,
                    "apr_summary": apr_summary,
                    "marker_summary": marker_summary,
                    "snapshot": diff.get("snapshot"),  # 되돌리기용
                })
                ok += 1
                log(f"  ✅ SM {p['sm_row']} {p['part']} {p['qty']}개 → {team} ({dc_summary}, Apr {apr_summary})")
            except Exception as e:
                results.append({**base, "status": "failed", "error": str(e)})
                log(f"  ❌ SM {p['sm_row']} {p['part']}: {e}")
                skip += 1
    finally:
        app.calculation = prev_calc
        app.screen_updating = prev_screen
        try:
            app.calculate()
        except Exception:
            pass
        if save:
            wb.save()

    elapsed = time.time() - t0
    summary = (
        f"📅 {target_date} 처리 완료\n"
        f"✅ {ok}건 / ❌ {skip}건 (총 {ok+skip}건)\n"
        f"⏱️ {elapsed:.1f}초"
        + (" (자동 저장됨)" if save else " · 저장하려면 Excel에서 Ctrl+S")
    )
    return {
        "summary": summary,
        "processed_count": ok,
        "skipped_count": skip,
        "results": results,
        "book": wb.name,
        "saved": save,
        "elapsed": round(elapsed, 1),
        "backup_path": backup_path,
    }


def _col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    save_flag = "--save" in sys.argv
    process(book_path=path, save=save_flag)
