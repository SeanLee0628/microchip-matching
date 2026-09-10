from fastapi import FastAPI, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import pandas as pd
import io
import os
import math
import re
import uuid
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

from database import engine, get_db, Base
from models_ublox import UbloxBacklog, UBLOX_COLUMN_MAP, UBLOX_DISPLAY_COLUMNS
from models_sales import SalesPerformance as SalesModel, SALES_FIELD_MAP, SALES_REVERSE_MAP
from jaejae_agent import run_batch_agent, mark_all_existing_as_processed, compute_changes_dryrun
from invoice_render import (_build_invoice_xlsx_bytes, _build_invoice_pdf_bytes,
                            build_invoice_zip_bytes)
from invoice_upload import parse_invoice_upload
from po_report import (PO_REPORT_T1_COLS, build_preview as build_po_report_preview,
                       build_xlsx_bytes as build_po_report_xlsx_bytes)

# 테이블 생성
Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Result-Json-B64", "X-Marked-Count", "Content-Disposition"],
)


# Private Network Access (PNA): 공개 사이트(클라우드 EB http)에서 로컬(localhost)로
# 호출하면 크롬/엣지가 프리플라이트에 Access-Control-Request-Private-Network: true 를
# 보내고, 서버가 Access-Control-Allow-Private-Network: true 를 돌려줘야만 허용한다.
# CORSMiddleware는 이 헤더를 안 붙여서 출고 자동등록(클라우드 페이지 → localhost:8001)이
# 브라우저에서 차단됐다. 아래 미들웨어가 그 헤더를 붙여준다.
@app.middleware("http")
async def allow_private_network(request, call_next):
    response = await call_next(request)
    if request.method == "OPTIONS" and request.headers.get(
        "access-control-request-private-network"
    ):
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

COLUMNS = [
    "고객코드", "믹스#", "Sales", "고객", "END", "PURCHASING", "PART#", "FAB2", "LT",
    "2023년", "2024년", "2025년", "2026년", "23~25추이", "25-26(w/BL)", "BLOG TTL",
    "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월",
    "믹스#(customer&part)",
]

MONTH_COLUMNS = ["3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]


def clean_value(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v


def to_float(v):
    """DB 저장용 float 변환"""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _s(v):
    """str 변환, NaN/None은 None"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _code_str(v):
    """고객코드: float(131112.0) → '131112'"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() or None


def _norm_mix(s):
    """믹스# 매칭용 정규화: 공백 제거 + 대문자 (baseline_shipments.json 키와 동일 규칙)."""
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return ""
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return ""
    return re.sub(r"\s+", "", s).upper()


def _build_mix(code, part):
    """믹스#가 비어있을 때 '고객코드 + 품번'으로 생성."""
    code = _code_str(code) or ""
    part = _s(part) or ""
    combined = code + part
    return combined or None


# 2023~2025 출하수량 고정 기준선.
# 참조파일 '마이크로칩(매칭)20260324.xlsx' 의 '출고기준(백록매칭)' 시트에서 1회 추출.
# 키 = 정규화된 믹스#(=고객코드+품번), 값 = {"2023","2024","2025"} (각 float 또는 None).
# 매칭 시 2023~2025 컬럼은 업로드 데이터로 재계산하지 않고 이 테이블에서 항상 채운다.
_BASELINE_PATH = os.path.join(os.path.dirname(__file__), "baseline_shipments.json")
try:
    import json as _baseline_json
    with open(_BASELINE_PATH, encoding="utf-8") as _bf:
        BASELINE_SHIPMENTS = _baseline_json.load(_bf)
except (OSError, ValueError):
    BASELINE_SHIPMENTS = {}


def _parse_snapshot_date(sheet_name: str):
    """'백록260324' → Timestamp(2026, 3, 24)"""
    m = re.search(r"(\d{6})$", sheet_name)
    if not m:
        return None
    s = m.group(1)
    try:
        return pd.Timestamp(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def _bucket_month(crd, snapshot_date):
    """CRD를 2026년 월 버킷에 배치. snapshot 이전이면 snapshot 월로 당김."""
    if not isinstance(crd, pd.Timestamp) or pd.isna(crd):
        return None
    if snapshot_date and crd < snapshot_date:
        if snapshot_date.year == 2026 and 3 <= snapshot_date.month <= 12:
            return snapshot_date.month
        return None
    if crd.year == 2026 and 3 <= crd.month <= 12:
        return crd.month
    return None


def parse_excel(contents: bytes, cutoff_date=None):
    """백록260324 + 출고내역 + FAB2 → 출고기준(백록매칭) 포맷 레코드 조립.
    cutoff_date: 당해년도 출하 합산 기준 (이 날짜 미만만 합산). None이면 오늘 기준 당월 1일.
    """
    xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")

    shipment_sheet = None
    backlog_sheet = None
    fab2_sheet = None
    for name in xls.sheet_names:
        if name == "출고내역":
            shipment_sheet = name
        elif name.startswith("백록") and "피벗" not in name:
            backlog_sheet = name
        elif name == "FAB2":
            fab2_sheet = name

    if not shipment_sheet and not backlog_sheet:
        return None, None, None

    snapshot_date = _parse_snapshot_date(backlog_sheet) if backlog_sheet else None

    # 출고내역 집계: 믹스# → 고객/PART 정보 + 연도별 출고수량 합계
    # 원본 작업 패턴: 당해년도 컬럼은 "전월 말까지의 누적"만 합산 (97% 일치 검증됨)
    from datetime import date as _date_cls
    if cutoff_date is None:
        _today = _date_cls.today()
        month_cutoff = _date_cls(_today.year, _today.month, 1)
    else:
        month_cutoff = cutoff_date

    ship_agg = {}
    if shipment_sheet:
        df = pd.read_excel(xls, sheet_name=shipment_sheet, header=0)
        for _, row in df.iterrows():
            # 믹스#가 비어있으면 고객코드+품번으로 생성
            mix = _s(row.get("믹스#")) or _build_mix(row.get("고객코드"), row.get("품번"))
            if not mix:
                continue
            qty = to_float(row.get("출고수량")) or 0
            date = row.get("출고일자")

            if mix not in ship_agg:
                ship_agg[mix] = {
                    "고객코드": _code_str(row.get("고객코드")),
                    "Sales": _s(row.get("담당자")),
                    "고객": _s(row.get("고객")),
                    "END": _s(row.get("END고객사명")),
                    "PURCHASING": _s(row.get("PURCHSING")),
                    "PART#": _s(row.get("품번")),
                    "yearly": {},
                }
            # 출고일자 < 당월 1일 인 행만 합산 (당월 미완료분 제외)
            if isinstance(date, pd.Timestamp) and not pd.isna(date):
                ship_date = date.date()
                if ship_date < month_cutoff:
                    year = ship_date.year
                    ship_agg[mix]["yearly"][year] = ship_agg[mix]["yearly"].get(year, 0) + qty

    # 백록 집계: 믹스 → LT + 월별 Qty Due 합계
    bl_agg = {}
    if backlog_sheet:
        df = pd.read_excel(xls, sheet_name=backlog_sheet, header=0)
        for _, row in df.iterrows():
            # 믹스가 비어있으면 업체코드+Customer Part Number 로 생성
            mix = _s(row.get("믹스")) or _build_mix(row.get("업체코드"), row.get("Customer Part Number"))
            if not mix:
                continue
            qty = to_float(row.get("Qty Due")) or 0
            month = _bucket_month(row.get("CRD"), snapshot_date)
            lt = to_float(row.get("Lead Time Weeks"))

            if mix not in bl_agg:
                bl_agg[mix] = {
                    "고객코드": _code_str(row.get("업체코드")),
                    "고객": _s(row.get("업체명")),
                    "END": _s(row.get("End Customer Name")),
                    "PURCHASING": _s(row.get("ODM/SubCon Name")),
                    "PART#": _s(row.get("Customer Part Number")),
                    "LT": lt,
                    "monthly": {},
                }
            else:
                # LT가 비어있던 행이 있으면 보충
                if bl_agg[mix]["LT"] is None and lt is not None:
                    bl_agg[mix]["LT"] = lt
            if month:
                bl_agg[mix]["monthly"][month] = bl_agg[mix]["monthly"].get(month, 0) + qty

    # FAB2 매핑: PART# → Remark 컬럼 (원본 수식과 동일: XLOOKUP first match)
    # 원본: =IFERROR(XLOOKUP(G4, 'FAB2'!A:A, 'FAB2'!G:G), "-")
    fab2_map = {}
    if fab2_sheet:
        df = pd.read_excel(xls, sheet_name=fab2_sheet, header=0)
        for _, row in df.iterrows():
            pn = _s(row.get("PN"))
            if pn and pn not in fab2_map:  # XLOOKUP 기본 동작: 첫 매칭만
                fab2_map[pn] = _s(row.get("Remark")) or "-"

    # 병합
    records = []
    for mix in set(ship_agg) | set(bl_agg):
        ship = ship_agg.get(mix, {})
        bl = bl_agg.get(mix, {})
        yearly = ship.get("yearly", {})
        monthly = bl.get("monthly", {})

        part_no = ship.get("PART#") or bl.get("PART#")
        end = ship.get("END") or bl.get("END")

        blog_ttl = sum(monthly.values()) if monthly else 0
        # 2023~2025: 고정 기준선에서 (업로드로 재계산하지 않음). 2026: 업로드 출고내역에서 동적.
        base = BASELINE_SHIPMENTS.get(_norm_mix(mix), {})
        y2023 = base.get("2023")
        y2024 = base.get("2024")
        y2025 = base.get("2025")
        y2026 = yearly.get(2026)
        if y2025 is None and y2026 is None and blog_ttl == 0:
            wbl = None
        else:
            wbl = (y2026 or 0) + blog_ttl - (y2025 or 0)

        rec = {
            "고객코드": ship.get("고객코드") or bl.get("고객코드"),
            "믹스#": mix,
            "Sales": ship.get("Sales"),
            "고객": ship.get("고객") or bl.get("고객"),
            "END": end,
            "PURCHASING": ship.get("PURCHASING") or bl.get("PURCHASING"),
            "PART#": part_no,
            "FAB2": fab2_map.get(part_no, "-") if part_no else "-",
            "LT": bl.get("LT"),
            "2023년": y2023,
            "2024년": y2024,
            "2025년": y2025,
            "2026년": y2026,
            "23~25추이": None,
            "25-26(w/BL)": wbl,
            "BLOG TTL": blog_ttl,
        }
        for i, col in enumerate(MONTH_COLUMNS, start=3):
            rec[col] = monthly.get(i, 0)
        rec["믹스#(customer&part)"] = (end or "") + (part_no or "")
        records.append(rec)

    records.sort(key=lambda r: ((r.get("PART#") or ""), (r.get("END") or "")))

    return "출고기준(백록매칭)", COLUMNS, records


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)):
    contents = await file.read()
    target_sheet, final_columns, records = parse_excel(contents)

    if target_sheet is None:
        return {"error": "백록 또는 출고내역 시트를 찾을 수 없습니다."}
    if records is None:
        return {"error": "데이터를 파싱할 수 없습니다."}

    return {
        "sheet_name": target_sheet,
        "columns": final_columns,
        "data": records,
        "total_rows": len(records),
    }


@app.post("/api/export")
async def export_excel(data: dict):
    from openpyxl.styles import PatternFill, Font, Alignment

    rows = data.get("data", [])
    columns = data.get("columns", COLUMNS)

    df = pd.DataFrame(rows, columns=[c for c in columns if c in (rows[0].keys() if rows else columns)])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="마이크로칩(매칭)", index=False)
        ws = writer.sheets["마이크로칩(매칭)"]

        # 헤더 스타일: 하늘색 배경 + 볼드 + 가운데 정렬
        sky_blue = PatternFill(start_color="87CEEB", end_color="87CEEB", fill_type="solid")
        bold_font = Font(bold=True)
        center_align = Alignment(horizontal="center")

        for col_idx in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = sky_blue
            cell.font = bold_font
            cell.alignment = center_align

        # 숫자 컬럼 천단위 콤마 서식 (#,##0). 고객코드·믹스# 등 식별자는 제외.
        NUMERIC_COLS = {
            "2023년", "2024년", "2025년", "2026년", "23~25추이", "25-26(w/BL)",
            "BLOG TTL", "3월", "4월", "5월", "6월", "7월", "8월", "9월",
            "10월", "11월", "12월",
        }
        for col_idx, col_name in enumerate(df.columns, start=1):
            if col_name in NUMERIC_COLS:
                for row_idx in range(2, len(df) + 2):
                    ws.cell(row=row_idx, column=col_idx).number_format = "#,##0"

        # 필터 설정
        ws.auto_filter.ref = ws.dimensions

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=microchip_matching_export.xlsx"},
    )


# ==================== 영업5실 매칭 (Uniquant) ====================
from fastapi import Form


@app.post("/api/match5/upload")
async def match5_upload(
    inventory: UploadFile = File(...),
    fcst: UploadFile = File(...),
    blog: UploadFile = File(...),
    shipment: UploadFile = File(...),
    matching: UploadFile = File(None),
    password: str = Form("9178"),
):
    import match5
    warnings = []
    try:
        inv = match5.parse_inventory(await inventory.read(), password=password or "9178")
    except ValueError as e:
        return {"error": str(e)}
    if not inv:
        warnings.append("재고 파일에서 PART#/available Q'ty 헤더를 찾지 못했습니다.")
    fc = match5.parse_fcst(await fcst.read())
    if not fc:
        warnings.append("FCST에서 Demand Total 데이터를 찾지 못했습니다.")
    bl, bl_meta = match5.read_blog(await blog.read())
    if not bl:
        warnings.append("백록 파일에서 데이터를 찾지 못했습니다.")
    sh = match5.parse_shipment(await shipment.read())
    if not sh:
        warnings.append("출고내역에서 데이터를 찾지 못했습니다.")

    # 마이크로칩(매칭) 파일(선택): 2023~2025 출하이력 소스
    hist = {}
    if matching is not None:
        hist = match5.parse_matching_history(await matching.read())
        if not hist:
            warnings.append("마이크로칩(매칭) 파일에서 믹스#/2023~2025년 헤더를 찾지 못했습니다 → 2023~2025 출하이력은 '-' 로 표시됩니다.")
    else:
        warnings.append("마이크로칩(매칭) 파일 미첨부 → 2023~2025 출하이력은 '-' (출고내역엔 2026만 존재).")

    columns, dashboard_columns, records = match5.build_records(inv, fc, bl, sh, history=hist)
    warnings.extend(match5.diagnostics(bl_meta, records))
    return {
        "columns": columns,
        "dashboard_columns": dashboard_columns,
        "data": records,
        "total_rows": len(records),
        "warnings": warnings,
    }


@app.post("/api/match5/export")
async def match5_export(payload: dict):
    import match5
    columns = payload.get("columns") or match5.COLUMNS
    rows = payload.get("data", [])
    buf = match5.export_workbook(columns, rows)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''%EC%98%81%EC%97%855%EC%8B%A4_%EB%A7%A4%EC%B9%AD.xlsx"},
    )


# ==================== u-blox 백로그 ====================

def parse_ublox_excel(contents: bytes):
    """u-blox 백로그 엑셀 파싱"""
    xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")
    df = pd.read_excel(xls, sheet_name=0, header=None)

    # 헤더 행 찾기
    header_row = 0
    for i in range(min(5, len(df))):
        row_values = [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]
        if "Order Name" in row_values or "Type Number" in row_values:
            header_row = i
            break

    # 14번 컬럼의 헤더 (전일 날짜)
    prev_date_header = str(df.iloc[header_row, 14]) if df.shape[1] > 14 else None

    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df = df.dropna(how="all").reset_index(drop=True)

    records = []
    for _, row in df.iterrows():
        record = {}
        has_data = False
        for col_idx, (db_field, display_name, dtype) in UBLOX_COLUMN_MAP.items():
            if col_idx >= len(row):
                record[display_name] = None
                continue
            v = row.iloc[col_idx]
            v = clean_value(v)
            if dtype == "float":
                record[display_name] = to_float(v)
            elif dtype == "date" and isinstance(v, str):
                record[display_name] = v
            elif v is not None and isinstance(v, pd.Timestamp):
                record[display_name] = v.strftime("%Y-%m-%d")
            else:
                record[display_name] = str(v) if v is not None else None
            if v is not None:
                has_data = True
        if has_data and record.get("Order Name"):
            records.append(record)

    return records, prev_date_header


def ublox_record_to_db(record: dict, upload_date, version: int) -> UbloxBacklog:
    kwargs = {"upload_date": upload_date, "upload_version": version}
    for col_idx, (db_field, display_name, dtype) in UBLOX_COLUMN_MAP.items():
        val = record.get(display_name)
        if dtype == "float":
            kwargs[db_field] = to_float(val)
        else:
            kwargs[db_field] = str(val) if val is not None else None
    return UbloxBacklog(**kwargs)


def ublox_db_to_record(row: UbloxBacklog) -> dict:
    record = {}
    for col_idx, (db_field, display_name, dtype) in UBLOX_COLUMN_MAP.items():
        record[display_name] = getattr(row, db_field)
    return record


@app.post("/api/ublox/upload")
async def upload_ublox(file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    records, prev_date_header = parse_ublox_excel(contents)

    if not records:
        return {"error": "데이터를 찾을 수 없습니다."}

    today = datetime.now().date()

    # 최신 버전 번호 조회
    latest_version = db.query(UbloxBacklog.upload_version).order_by(
        UbloxBacklog.upload_version.desc()
    ).first()
    prev_version = latest_version[0] if latest_version else None
    new_version = (prev_version or 0) + 1

    # 이전 데이터 조회 (직전 업로드와 비교)
    prev_records_map = {}
    if prev_version:
        prev_rows = db.query(UbloxBacklog).filter(
            UbloxBacklog.upload_version == prev_version
        ).all()
        for r in prev_rows:
            key = r.order_name
            prev_records_map[key] = ublox_db_to_record(r)

    # 새 버전으로 저장
    for r in records:
        db.add(ublox_record_to_db(r, today, new_version))
    db.commit()

    # 변경 비교
    changes = []
    for r in records:
        order_name = r.get("Order Name")
        change_info = {"type": None, "changed_fields": []}

        if order_name not in prev_records_map:
            if prev_records_map:  # 전일 데이터가 있을 때만 신규 표시
                change_info["type"] = "new"
        else:
            prev = prev_records_map[order_name]
            changed = []
            for col in ["Delivery Date", "Qty Ordered", "Price per unit", "Order Status"]:
                if str(r.get(col, "")) != str(prev.get(col, "")):
                    changed.append(col)
            if changed:
                change_info["type"] = "modified"
                change_info["changed_fields"] = changed
            del prev_records_map[order_name]

        r["_change"] = change_info

    # 삭제된 주문
    deleted = []
    for order_name, prev in prev_records_map.items():
        prev["_change"] = {"type": "deleted", "changed_fields": []}
        deleted.append(prev)

    return {
        "columns": UBLOX_DISPLAY_COLUMNS,
        "data": records,
        "deleted": deleted,
        "total_rows": len(records),
        "has_prev": prev_version is not None,
        "prev_version": prev_version,
        "version": new_version,
        "upload_date": str(today),
    }


@app.get("/api/ublox/data")
async def get_ublox_data(db: Session = Depends(get_db)):
    """최신 u-blox 데이터 조회 (전일 비교 포함)"""
    versions = db.query(UbloxBacklog.upload_version).distinct().order_by(
        UbloxBacklog.upload_version.desc()
    ).limit(2).all()

    if not versions:
        return {"data": [], "columns": UBLOX_DISPLAY_COLUMNS, "total_rows": 0}

    latest_version = versions[0][0]
    prev_version = versions[1][0] if len(versions) > 1 else None

    rows = db.query(UbloxBacklog).filter(
        UbloxBacklog.upload_version == latest_version
    ).order_by(UbloxBacklog.id).all()

    records = [ublox_db_to_record(r) for r in rows]

    # 전일 비교
    prev_records_map = {}
    deleted = []
    if prev_version:
        prev_rows = db.query(UbloxBacklog).filter(
            UbloxBacklog.upload_version == prev_version
        ).all()
        for r in prev_rows:
            prev_records_map[r.order_name] = ublox_db_to_record(r)

        for r in records:
            order_name = r.get("Order Name")
            change_info = {"type": None, "changed_fields": []}
            if order_name not in prev_records_map:
                change_info["type"] = "new"
            else:
                prev = prev_records_map[order_name]
                changed = []
                for col in ["Delivery Date", "Qty Ordered", "Price per unit", "Order Status"]:
                    if str(r.get(col, "")) != str(prev.get(col, "")):
                        changed.append(col)
                if changed:
                    change_info["type"] = "modified"
                    change_info["changed_fields"] = changed
                del prev_records_map[order_name]
            r["_change"] = change_info

        for order_name, prev in prev_records_map.items():
            prev["_change"] = {"type": "deleted", "changed_fields": []}
            deleted.append(prev)
    else:
        for r in records:
            r["_change"] = {"type": None, "changed_fields": []}

    return {
        "columns": UBLOX_DISPLAY_COLUMNS,
        "data": records,
        "deleted": deleted,
        "total_rows": len(records),
        "upload_date": str(rows[0].upload_date) if rows else None,
        "version": latest_version,
        "has_prev": prev_version is not None,
    }


@app.get("/api/ublox/search/{type_number}")
async def search_ublox(type_number: str, db: Session = Depends(get_db)):
    """품명으로 백로그 조회"""
    latest_version = db.query(UbloxBacklog.upload_version).order_by(
        UbloxBacklog.upload_version.desc()
    ).first()

    if not latest_version:
        return {"data": [], "summary": None}

    rows = db.query(UbloxBacklog).filter(
        UbloxBacklog.upload_version == latest_version[0],
        UbloxBacklog.type_number.ilike(f"%{type_number}%")
    ).order_by(UbloxBacklog.request_date).all()

    records = [ublox_db_to_record(r) for r in rows]

    # 요약
    total_qty = sum(to_float(r.get("Qty Ordered")) or 0 for r in records)
    total_value = sum(to_float(r.get("Total Value")) or 0 for r in records)
    customers = list(set(r.get("End Customer") for r in records if r.get("End Customer")))

    return {
        "columns": UBLOX_DISPLAY_COLUMNS,
        "data": records,
        "total_rows": len(records),
        "summary": {
            "type_number": type_number,
            "total_qty": total_qty,
            "total_value": total_value,
            "order_count": len(records),
            "customers": customers,
        },
    }


@app.delete("/api/ublox/data")
async def reset_ublox(db: Session = Depends(get_db)):
    count = db.query(UbloxBacklog).count()
    db.query(UbloxBacklog).delete()
    db.commit()
    return {"deleted": count}


# ==================== 영업실적 ====================

FIXED_EXCHANGE_RATE = 1400

SALES_COLUMNS = [
    "구분", "MPN", "QTY", "DCPL($)", "매입금액($)",
    "SP($)", "매출금액($)", "매출환율", "SP(KRW)", "매출금액(KRW)",
    "GP($)", "GP%($)", "GP(KRW)", "GP%(KRW)",
    "담당자", "납품처", "거래처코드", "출고일자", "입고일", "Month",
]


def parse_remark(remark):
    """비고(내역)에서 매입단가, 매출단가 추출: '2.2_2.8' → (2.2, 2.8)"""
    if not remark or str(remark) == "nan":
        return None, None
    parts = str(remark).split("_")
    try:
        buy = float(parts[0])
    except (ValueError, IndexError):
        buy = None
    try:
        sell = float(parts[1]) if len(parts) > 1 else None
    except (ValueError, IndexError):
        sell = None
    return buy, sell


def parse_lot_date(lot_no):
    """LOT No.에서 입고일 추출: '260129_8K_$2.2_Korni' → '2026-01-29'"""
    if not lot_no or str(lot_no) == "nan":
        return None
    parts = str(lot_no).split("_")
    if not parts:
        return None
    date_str = parts[0].strip()
    if len(date_str) == 6 and date_str.isdigit():
        yy, mm, dd = date_str[:2], date_str[2:4], date_str[4:6]
        return f"20{yy}-{mm}-{dd}"
    return None


def sales_record_to_db(record: dict, batch_id: str) -> SalesModel:
    float_fields = {"QTY", "DCPL($)", "매입금액($)", "SP($)", "매출금액($)", "매출환율",
                     "SP(KRW)", "매출금액(KRW)", "GP($)", "GP%($)", "GP(KRW)", "GP%(KRW)"}
    kwargs = {"upload_batch": batch_id}
    for excel_col, db_field in SALES_FIELD_MAP.items():
        val = record.get(excel_col)
        if excel_col in float_fields:
            kwargs[db_field] = to_float(val)
        else:
            kwargs[db_field] = str(val) if val is not None else None
    return SalesModel(**kwargs)


def sales_db_to_record(row: SalesModel) -> dict:
    record = {}
    for excel_col, db_field in SALES_FIELD_MAP.items():
        record[excel_col] = getattr(row, db_field)
    return record


def compute_sales_summary(records):
    total_sales_usd = sum(r.get("매출금액($)") or 0 for r in records)
    total_buy_usd = sum(r.get("매입금액($)") or 0 for r in records)
    total_gp_usd = sum(r.get("GP($)") or 0 for r in records)
    total_sales_krw = sum(r.get("매출금액(KRW)") or 0 for r in records)
    total_gp_krw = sum(r.get("GP(KRW)") or 0 for r in records)
    return {
        "total_sales_usd": round(total_sales_usd, 2),
        "total_buy_usd": round(total_buy_usd, 2),
        "total_gp_usd": round(total_gp_usd, 2),
        "total_gp_pct": round(total_gp_usd / total_sales_usd * 100, 2) if total_sales_usd else 0,
        "total_sales_krw": round(total_sales_krw, 2),
        "total_gp_krw": round(total_gp_krw, 2),
        "total_gp_pct_krw": round(total_gp_krw / total_sales_krw * 100, 2) if total_sales_krw else 0,
    }


@app.post("/api/sales/upload")
async def upload_sales(file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents), header=0)
    except Exception:
        df = pd.read_excel(io.BytesIO(contents), header=0, engine="xlrd")

    records = []
    for _, row in df.iterrows():
        remark = row.get("비고(내역)")
        lot_no = row.get("LOT No.")
        qty = to_float(row.get("출고수량"))
        if not qty or qty == 0:
            continue

        dcpl, sp = parse_remark(remark)
        inbound_date = parse_lot_date(lot_no)

        # 외화단가가 있으면 SP로 사용 (USD 거래)
        foreign_price = to_float(row.get("외화단가"))
        if foreign_price and foreign_price > 0:
            sp = foreign_price

        # 매출환율: 더존 환율이 있으면 사용, 없으면 고정
        exch_rate = to_float(row.get("환율"))
        if not exch_rate or exch_rate <= 1:
            exch_rate = FIXED_EXCHANGE_RATE

        # 계산
        buy_amt = round(dcpl * qty, 2) if dcpl else None
        sell_amt = round(sp * qty, 2) if sp else None
        sp_krw = round(sp * exch_rate, 2) if sp else None
        sell_amt_krw = round(sell_amt * exch_rate, 2) if sell_amt else None
        gp_usd = round(sell_amt - buy_amt, 2) if sell_amt and buy_amt else None
        gp_pct = round(gp_usd / sell_amt * 100, 2) if gp_usd and sell_amt and sell_amt != 0 else None
        gp_krw = round(gp_usd * exch_rate, 2) if gp_usd else None
        gp_pct_krw = round(gp_krw / sell_amt_krw * 100, 2) if gp_krw and sell_amt_krw and sell_amt_krw != 0 else None

        # 출고일자
        ship_date = row.get("출고일자")
        if isinstance(ship_date, pd.Timestamp):
            ship_date = ship_date.strftime("%Y-%m-%d")
        else:
            ship_date = str(ship_date) if ship_date and str(ship_date) != "nan" else None

        # Month
        month_val = row.get("출고년월")
        if month_val and str(month_val) != "nan":
            month_val = str(month_val).replace("/", "")
        else:
            month_val = None

        vendor = row.get("품목군") or row.get("품목대분류") or ""
        vendor = str(vendor) if str(vendor) != "nan" else ""

        records.append({
            "구분": vendor,
            "MPN": str(row.get("품번", "")) if str(row.get("품번", "")) != "nan" else "",
            "QTY": qty,
            "DCPL($)": dcpl,
            "매입금액($)": buy_amt,
            "SP($)": sp,
            "매출금액($)": sell_amt,
            "매출환율": exch_rate,
            "SP(KRW)": sp_krw,
            "매출금액(KRW)": sell_amt_krw,
            "GP($)": gp_usd,
            "GP%($)": gp_pct,
            "GP(KRW)": gp_krw,
            "GP%(KRW)": gp_pct_krw,
            "담당자": str(row.get("담당자", "")) if str(row.get("담당자", "")) != "nan" else "",
            "납품처": str(row.get("고객", "")) if str(row.get("고객", "")) != "nan" else "",
            "거래처코드": str(row.get("고객코드", "")) if str(row.get("고객코드", "")) != "nan" else "",
            "출고일자": ship_date,
            "입고일": inbound_date,
            "Month": month_val,
        })

    # DB 저장
    db.query(SalesModel).delete()
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    for r in records:
        db.add(sales_record_to_db(r, batch_id))
    db.commit()

    return {
        "columns": SALES_COLUMNS,
        "data": records,
        "total_rows": len(records),
        "summary": compute_sales_summary(records),
        "saved_to_db": True,
    }


@app.get("/api/sales/data")
async def get_sales_data(db: Session = Depends(get_db)):
    """DB에서 영업실적 데이터 조회"""
    rows = db.query(SalesModel).order_by(SalesModel.id).all()
    if not rows:
        return {"data": [], "columns": SALES_COLUMNS, "total_rows": 0}

    records = [sales_db_to_record(r) for r in rows]
    return {
        "columns": SALES_COLUMNS,
        "data": records,
        "total_rows": len(records),
        "summary": compute_sales_summary(records),
    }


@app.post("/api/reset-tables")
async def reset_tables():
    """DB 테이블 재생성 (스키마 변경 시)"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return {"status": "ok", "message": "All tables recreated"}


# ==================== 마이크론 재고 ====================

MICRON_COLUMNS = [
    "Status", "Type", "PO", "DID", "MPN", "CPN (MOBIS ID 포함)", "BOX_TYPE",
    "QTY", "DNNo.", "Ship Date", "MicronInvoice#", "수입면장번호", "BL번호",
    "수입신고일", "FSE", "End customer", "Date Code",
    "Booking Customer & FSE", "Qty_booking", "비고",
]

micron_data = []  # 로컬 메모리 저장


@app.post("/api/micron/upload")
async def upload_micron(file: UploadFile = File(...)):
    global micron_data
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents), sheet_name="Detail", header=0)
    except Exception:
        return {"error": "Detail 시트를 찾을 수 없습니다."}

    records = []
    for idx, row in df.iterrows():
        rec = {}
        for col in df.columns:
            v = row[col]
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                rec[col] = None
            elif isinstance(v, pd.Timestamp):
                rec[col] = v.strftime("%Y-%m-%d")
            elif v is not None:
                rec[col] = str(v) if not isinstance(v, (int, float)) else v
            else:
                rec[col] = None
        rec["_id"] = str(idx)
        records.append(rec)

    micron_data = records

    return {
        "columns": MICRON_COLUMNS,
        "data": records,
        "total_rows": len(records),
    }


@app.get("/api/micron/data")
async def get_micron_data(
    status: str = "",
    did: str = "",
    mpn: str = "",
    notes_only: str = "",
):
    filtered = micron_data
    if status:
        tokens = [t.strip() for t in status.split(",") if t.strip()]
        if tokens:
            filtered = [r for r in filtered if any(t in str(r.get("Status", "")) for t in tokens)]
    if did:
        filtered = [r for r in filtered if did.lower() in str(r.get("DID", "")).lower()]
    if mpn:
        filtered = [r for r in filtered if mpn.lower() in str(r.get("MPN", "")).lower()]
    if notes_only == "true":
        filtered = [r for r in filtered if r.get("비고") and str(r.get("비고")) not in ("None", "", "nan")]

    return {"columns": MICRON_COLUMNS, "data": filtered, "total_rows": len(filtered)}


@app.get("/api/micron/summary/{did}")
async def micron_summary(did: str):
    """DID 기준 재고+입고예정 통합 조회"""
    items = [r for r in micron_data if str(r.get("DID", "")).upper() == did.upper()]
    if not items:
        return {"error": "해당 DID 없음"}

    by_status = {}
    for r in items:
        s = str(r.get("Status", "기타"))
        if s not in by_status:
            by_status[s] = {"count": 0, "qty": 0, "items": []}
        by_status[s]["count"] += 1
        by_status[s]["qty"] += to_float(r.get("QTY")) or 0
        by_status[s]["items"].append(r)

    total_qty = sum(v["qty"] for v in by_status.values())
    mpns = list(set(str(r.get("MPN", "")) for r in items))

    return {
        "did": did, "mpns": mpns, "total_qty": total_qty,
        "by_status": {k: {"count": v["count"], "qty": v["qty"]} for k, v in by_status.items()},
        "items": items,
    }


@app.post("/api/micron/update")
async def update_micron(data: dict):
    """R~T열 수정 (CS팀 권한)"""
    item_id = data.get("_id")
    if item_id is None:
        return {"error": "ID 없음"}

    for r in micron_data:
        if str(r.get("_id")) == str(item_id):
            for field in ("Status", "수입면장번호", "BL번호",
                          "Booking Customer & FSE", "Qty_booking", "비고"):
                if field in data:
                    r[field] = data[field]
            return {"updated": item_id}

    return {"error": "not found"}


# ==================== 1실 CRD 신호등 보드 ====================
# Backlog Shipment Report(오픈 주문, 행마다 CRD+MAD) 업로드 → MAD vs CRD 위험판정.
# 재고/영업실적/DynamoDB 조인 불필요 — 파일 하나로 끝(stateless).
# 순수 로직은 crd_board.py (단위테스트 test_crd_board.py). main_aws.py 동일 엔드포인트.

def _bl_clean(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    s = str(v).strip()
    return s or None


def _bl_blank_zero(v):
    """FSE·CUST 는 값이 문자 0 으로 들어오는 행이 있다(샘플 3건) — 공란으로 본다.
    (요청 2026-09-10: "데이터에 해당 열(FSE CUST) 없다면 공란표기")"""
    s = _bl_clean(v)
    return None if s in ("0", "0.0") else s


BL_REQUIRED_COLS = {"MPN", "DID", "CRD", "MAD", "QTY"}


def _parse_backlog_orders(contents):
    """Backlog Shipment Report 파싱. 필수 컬럼이 있는 시트를 자동 탐지(시트명 제각각 대응).
    반환: 주문 dict 리스트, 백로그 형식이 아니면 None.

    요청서(2026-09-10) 표에 나가는 열을 전부 읽는다 — 예전엔 판정에 쓰는 것만 읽어서
    PO#·CUSTOMER_MATERIAL·PLANT·BOX_TYPE·CUST 가 화면에 못 나왔다."""
    try:
        xls = pd.ExcelFile(io.BytesIO(contents))
    except Exception:
        return None
    df = None
    for s in xls.sheet_names:
        d = pd.read_excel(xls, sheet_name=s, header=0)
        if BL_REQUIRED_COLS.issubset(set(d.columns)):
            df = d
            break
    if df is None:
        return None
    orders = []
    for _, row in df.iterrows():
        if row.isna().all():
            continue
        did, mpn = _bl_clean(row.get("DID")), _bl_clean(row.get("MPN"))
        if not did and not mpn:
            continue
        delivery = _bl_clean(row.get("DELIVERY_NUMBER"))
        orders.append({
            "so": _bl_clean(row.get("SO")),
            "did": did, "mpn": mpn,
            # 실제 헤더는 END_CUSTOMER_NAME. 예전 코드가 "End customer" 만 찾아서
            # 고객명이 전 행 빈칸으로 나왔다 (2026-09-10 수정). 구 헤더도 같이 본다.
            "customer": (_bl_clean(row.get("END_CUSTOMER_NAME"))
                         or _bl_clean(row.get("End customer"))),
            "po": _bl_clean(row.get("PURCH_ORDER_NO")),
            "cust_material": _bl_clean(row.get("CUSTOMER_MATERIAL")),
            "qty": to_float(row.get("QTY")) or 0,
            "crd": _parse_date(row.get("CRD")),
            "mad": _parse_date(row.get("MAD")),
            "plant": _bl_clean(row.get("PLANT")),
            "box_type": _bl_clean(row.get("BOX_TYPE")),
            "delivery_number": delivery,
            "order_type": _bl_clean(row.get("ORDER_TYPE")),  # OR=양산, FD=샘플
            "fse": _bl_blank_zero(row.get("FSE")),
            "cust": _bl_blank_zero(row.get("CUST")),
            "open": delivery is None,  # 출하번호 없으면 미출하
        })
    return orders


def _bl_ser_card(c):
    c = dict(c)
    for k in ("crd", "mad", "prev_mad"):
        if k in c and hasattr(c[k], "isoformat"):
            c[k] = c[k].isoformat()
    return c


# ───────── 엑셀 내보내기 (요청 2026-09-10) ─────────
# "경과된 것 뿐만 아니라 원본 엑셀에서 경과일수 열 추가된 엑셀 다운 가능하게"
# → 화면 필터와 무관하게 업로드한 파일의 전체 행을 그대로 내보내고 계산열만 덧붙인다.

def _bl_read_raw_sheet(contents):
    """업로드 원본에서 백로그 시트를 (헤더, 데이터행들) 로 읽는다. 값만 — 서식은 안 가져온다.
    헤더가 1행이 아닐 수 있어 앞 10행까지 훑는다. 못 찾으면 (None, None)."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(contents), data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            for i, r in enumerate(rows[:10]):
                hdr = [(str(v).strip() if v is not None else "") for v in r]
                if BL_REQUIRED_COLS.issubset(set(hdr)):
                    data = [list(x) for x in rows[i + 1:]
                            if any(v is not None and str(v).strip() != "" for v in x)]
                    return hdr, data
    finally:
        wb.close()
    return None, None


def _bl_cell_date(v):
    from datetime import datetime as _dtc, date as _datec
    if isinstance(v, _dtc):
        return v.date()
    if isinstance(v, _datec):
        return v
    return _parse_date(v)


def _bl_export_workbook(hdr, rows, extra_names, extra_values, sheet_title="Backlog"):
    """원본 헤더·행을 그대로 옮기고 MAD 열 바로 뒤에 계산열을 끼운 워크북을 만든다.

    extra_names: 추가 열 이름들, extra_values: 행별 추가값 리스트(rows 와 같은 길이).
    값이 None 이면 "N/A" 로 쓴다 (이전 백록에 없던 신규 SO 등).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from datetime import datetime as _dtc, date as _datec

    HDR_FILL = PatternFill("solid", fgColor="1F3A8A")
    ADD_FILL = PatternFill("solid", fgColor="C43A3A")   # 추가한 계산열은 색으로 구분
    ADD_BODY = PatternFill("solid", fgColor="FEF2F2")
    HDR_FONT = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
    DATA_FONT = Font(name="맑은 고딕", size=10)
    THIN = Side(border_style="thin", color="D1D5DB")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CENTER = Alignment(horizontal="center", vertical="center")

    pos = hdr.index("MAD") + 1 if "MAD" in hdr else len(hdr)
    out_hdr = hdr[:pos] + list(extra_names) + hdr[pos:]
    add_cols = set(range(pos + 1, pos + 1 + len(extra_names)))  # 1-indexed 열번호

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    for j, name in enumerate(out_hdr, start=1):
        c = ws.cell(1, j, name)
        c.fill = ADD_FILL if j in add_cols else HDR_FILL
        c.font = HDR_FONT
        c.alignment = CENTER
        c.border = BORDER

    for i, (row, extra) in enumerate(zip(rows, extra_values), start=2):
        row = list(row) + [None] * (len(hdr) - len(row))
        vals = row[:pos] + [("N/A" if v is None else v) for v in extra] + row[pos:len(hdr)]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(i, j, v)
            c.font = DATA_FONT
            c.border = BORDER
            if j in add_cols:
                c.fill = ADD_BODY
                c.alignment = CENTER
            if isinstance(v, (_dtc, _datec)):
                c.number_format = "yyyy-mm-dd"
                c.alignment = CENTER

    for j, name in enumerate(out_hdr, start=1):
        width = max(9, min(26, len(str(name)) + 4))
        if name in ("MPN", "PURCH_ORDER_NO", "END_CUSTOMER_NAME", "CUSTOMER_MATERIAL", "CUST"):
            width = 24
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.freeze_panes = "A2"
    return wb


def _bl_xlsx_response(wb, fname, extra_headers=None):
    from urllib.parse import quote
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    h = {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname)}
    h.update(extra_headers or {})
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=h,
    )


@app.post("/api/crd-board")
async def crd_board_compute(file: UploadFile = File(...), buffer_days: int = 7):
    import crd_board
    from datetime import datetime as _dt
    orders_all = _parse_backlog_orders(await file.read())
    if orders_all is None:
        return {"error": "Backlog Shipment Report 형식이 아닙니다 (MPN·DID·CRD·MAD·QTY 컬럼 필요)."}

    open_orders = [o for o in orders_all if o["open"]]
    shipped = len(orders_all) - len(open_orders)
    type_counts = {}
    for o in open_orders:
        type_counts[o["order_type"]] = type_counts.get(o["order_type"], 0) + 1

    today = _dt.now().date()
    cards = crd_board.classify_backlog(open_orders, today=today, buffer_days=buffer_days)
    # 화면 정렬은 위험도순이 아니라 MAD(자재 가용일) 빠른 순 — 요청서 예시와 첨부 파일이 그 순서.
    cards = crd_board.sort_by_mad(cards)

    board, summary = [], {"red": 0, "yellow": 0, "green": 0, "unknown": 0}
    for c in cards:
        board.append(_bl_ser_card(c))
        summary[c["risk"]] = summary.get(c["risk"], 0) + 1

    return {
        "board": board, "summary": summary,
        "part_summary": crd_board.summarize_by_part(cards),
        "open_count": len(open_orders), "shipped_skipped": shipped,
        "elapsed_count": len(crd_board.elapsed_only(cards)),   # 경과일수 > 0
        "order_types": type_counts, "buffer_days": buffer_days,
        "today": today.isoformat(),
    }


@app.post("/api/crd-board/export")
async def crd_board_export(file: UploadFile = File(...)):
    """업로드 원본 전체 행 + 경과일수(MAD-CRD) 열 xlsx.
    화면의 "경과된 것만" 필터와 무관하게 전수 — 요청서 N7 코멘트."""
    from datetime import datetime as _dt
    contents = await file.read()
    hdr, rows = _bl_read_raw_sheet(contents)
    if hdr is None:
        return {"error": "Backlog Shipment Report 형식이 아닙니다 (MPN·DID·CRD·MAD·QTY 컬럼 필요)."}

    i_crd, i_mad = hdr.index("CRD"), hdr.index("MAD")
    extras, n_elapsed = [], 0
    for r in rows:
        crd = _bl_cell_date(r[i_crd] if i_crd < len(r) else None)
        mad = _bl_cell_date(r[i_mad] if i_mad < len(r) else None)
        if crd is None or mad is None:
            extras.append([None])          # 날짜 없으면 N/A
        else:
            d = (mad - crd).days
            extras.append([d])
            if d > 0:
                n_elapsed += 1

    wb = _bl_export_workbook(hdr, rows, ["경과일수"], extras)
    stamp = _dt.now().strftime("%Y%m%d_%H%M")
    return _bl_xlsx_response(wb, f"CRD현황_경과일수_{stamp}.xlsx",
                             {"X-Row-Count": str(len(rows)), "X-Elapsed-Count": str(n_elapsed)})


@app.post("/api/crd-board/compare")
async def crd_board_compare(prev: UploadFile = File(...), current: UploadFile = File(...)):
    """이전·현재 백로그 두 파일 비교 → MAD 변화(밀림·당겨짐·신규) + 주간 움직임 요약."""
    import crd_board
    from datetime import datetime as _dt
    prev_all = _parse_backlog_orders(await prev.read())
    cur_all = _parse_backlog_orders(await current.read())
    if prev_all is None or cur_all is None:
        return {"error": "두 파일 모두 Backlog Shipment Report 형식이어야 합니다."}

    prev_open = [o for o in prev_all if o["open"]]
    cur_open = [o for o in cur_all if o["open"]]
    today = _dt.now().date()
    res = crd_board.compare_backlog(prev_open, cur_open, today=today)

    return {
        "changed": [_bl_ser_card(c) for c in res["changed"]],
        "slipped": [_bl_ser_card(c) for c in res["slipped"]],
        "new": [_bl_ser_card(c) for c in res["new"]],
        "gone_count": res["gone_count"],
        "summary": res["summary"],
        "today": today.isoformat(),
    }


@app.post("/api/crd-board/compare/export")
async def crd_board_compare_export(prev: UploadFile = File(...), current: UploadFile = File(...)):
    """현재 백록 전체 행 + 이전 MAD·GAP 열 xlsx (전체 백록 라인 비교 — 요청서 N7 코멘트).
    GAP = 현재 MAD - 이전 MAD. 이전 백록에 없던 SO 는 N/A."""
    from datetime import datetime as _dt
    cur_bytes = await current.read()
    prev_orders = _parse_backlog_orders(await prev.read())
    hdr, rows = _bl_read_raw_sheet(cur_bytes)
    if hdr is None or prev_orders is None:
        return {"error": "두 파일 모두 Backlog Shipment Report 형식이어야 합니다."}

    prev_mad_by_so = {o["so"]: o["mad"] for o in prev_orders if o.get("so")}
    i_so = hdr.index("SO") if "SO" in hdr else None
    i_mad = hdr.index("MAD")
    stat = {"push": 0, "pull": 0, "na": 0}
    vals = []
    for r in rows:
        so = _bl_clean(r[i_so]) if (i_so is not None and i_so < len(r)) else None
        mad = _bl_cell_date(r[i_mad] if i_mad < len(r) else None)
        pmad = prev_mad_by_so.get(so)
        if pmad is None or mad is None:
            vals.append([pmad, None])       # 신규 SO 등 → GAP N/A
            stat["na"] += 1
        else:
            gap = (mad - pmad).days
            vals.append([pmad, gap])
            if gap > 0:
                stat["push"] += 1
            elif gap < 0:
                stat["pull"] += 1

    wb = _bl_export_workbook(hdr, rows, ["이전 MAD", "GAP"], vals)
    stamp = _dt.now().strftime("%Y%m%d_%H%M")
    return _bl_xlsx_response(wb, f"CRD변화_MAD비교_{stamp}.xlsx",
                             {"X-Row-Count": str(len(rows)),
                              "X-Push-Out": str(stat["push"]),
                              "X-Pull-In": str(stat["pull"]),
                              "X-Na-Count": str(stat["na"])})


# ==================== 마이크론 백로그 원본 변환 ====================
# 마이크론 담당자가 메일로 보내는 원본 → Backlog Shipment Report 재배열 + DBC·FSE·CUST 채움.
# 원본은 회차마다 열 순서·개수가 달라서(9/2 15열 / 9/7 18열) 위치가 아니라 헤더 이름으로 찾는다.
# 순수 로직은 backlog_convert.py (단위테스트 test_backlog_convert.py). 양쪽 백엔드 동일.

async def _bc_load(file, prev):
    """반환: (에러 dict|None, 원본 bytes, 원본 헤더, 원본 행, 이전 백록 행|None)"""
    import backlog_convert as bc
    contents = await file.read()
    src_h, src = bc.read_sheet(contents)
    if src_h is None:
        return ({"error": "Backlog 원본 형식이 아닙니다 (SO·MPN·QTY·CRD·MAD 컬럼 필요)."},
                None, None, None, None)
    prev_rows = None
    if prev is not None:
        pb = await prev.read()
        if pb:
            _, prev_rows = bc.read_sheet(pb)
            if prev_rows is None:
                return ({"error": "이전 백록이 Backlog Shipment Report 형식이 아닙니다."},
                        None, None, None, None)
    return None, contents, src_h, src, prev_rows


@app.post("/api/backlog-convert/preview")
async def backlog_convert_preview(file: UploadFile = File(...), prev: UploadFile = File(None)):
    """원본(+이전 백록) → 재배열 결과 미리보기. 행은 앞부분만, 확인 필요 목록은 전부."""
    import backlog_convert as bc
    err, contents, src_h, src, prev_rows = await _bc_load(file, prev)
    if err:
        return err
    res = bc.convert(src_h, src, prev_rows)
    return {
        "columns": res["columns"],
        "rows": res["rows"][:bc.PREVIEW_ROWS],
        "fills": res["fills"][:bc.PREVIEW_ROWS],
        "review": res["review"],
        "summary": res["summary"],
        "source_header": src_h,
        "sheet": bc.find_sheet_title(contents),
        "preview_rows": bc.PREVIEW_ROWS,
    }


@app.post("/api/backlog-convert/export")
async def backlog_convert_export(file: UploadFile = File(...), prev: UploadFile = File(None)):
    """전수 변환 xlsx. 확인 필요한 건이 있으면 '확인필요' 시트가 붙는다."""
    import backlog_convert as bc
    from datetime import datetime as _bc_dt
    from urllib.parse import quote
    err, contents, src_h, src, prev_rows = await _bc_load(file, prev)
    if err:
        return err
    res = bc.convert(src_h, src, prev_rows)
    out = io.BytesIO(bc.build_xlsx_bytes(res, bc.find_sheet_title(contents)))
    fname = f"Backlog Shipment Report - {_bc_dt.now().strftime('%y%m%d')}.xlsx"
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname),
            "X-Row-Count": str(res["summary"]["row_count"]),
            "X-Review-Count": str(res["summary"]["review_count"]),
            "X-Output-Columns": str(res["summary"]["output_columns"]),
        },
    )


# ==================== 거래명세서 ====================

import requests as http_requests

def _fetch_koreaexim_rate(yyyymmdd: str):
    """수출입은행 매매기준율(deal_bas_r) 조회. 영업일 아니면 None."""
    authkey = os.environ.get("KOREAEXIM_AUTHKEY")
    if not authkey:
        return None
    try:
        r = http_requests.get(
            "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON",
            params={"authkey": authkey, "searchdate": yyyymmdd, "data": "AP01"},
            timeout=10, verify=False,
        )
        d = r.json()
        if not isinstance(d, list) or not d:
            return None
        usd = [x for x in d if x.get("result") == 1 and (x.get("cur_unit") or "").strip() == "USD"]
        if not usd:
            return None
        rate_str = (usd[0].get("deal_bas_r") or "").replace(",", "")
        return round(float(rate_str), 2) if rate_str else None
    except Exception:
        return None


@app.get("/api/exchange-rate")
async def get_exchange_rate(date: str = ""):
    """USD/KRW 매매기준율 조회.
    1순위: 한국수출입은행 최초고시 (영업일 아니면 직전 영업일까지 최대 5일 소급)
    2순위: frankfurter.app (ECB 기준)
    3순위: open.er-api (실시간) — 폴백
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")

    # 1. KoreaExim 매매기준율 (공식)
    try:
        d = pd.Timestamp(target_date)
        for back in range(6):
            yyyymmdd = (d - pd.Timedelta(days=back)).strftime("%Y%m%d")
            r = _fetch_koreaexim_rate(yyyymmdd)
            if r:
                return {"rate": r, "source": "koreaexim", "label": "수출입은행 최초고시", "date": target_date}
    except Exception:
        pass

    # 2. frankfurter
    try:
        resp = http_requests.get(f"https://api.frankfurter.app/{target_date}?from=USD&to=KRW", timeout=8)
        rate = round(float(resp.json()["rates"]["KRW"]), 2)
        return {"rate": rate, "source": "frankfurter", "label": "ECB 기준", "date": target_date}
    except Exception:
        pass

    # 3. 실시간 폴백
    try:
        resp = http_requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        rate = round(float(resp.json()["rates"]["KRW"]), 2)
        return {"rate": rate, "source": "exchangerate-api", "label": "실시간", "date": target_date}
    except Exception:
        return {"rate": 1400, "source": "fallback", "label": "기본값", "date": target_date}


def _detect_invoice_currency(items):
    """전체 아이템 통화 판별 → 'KRW' | 'USD' | 'BOTH'.
    - 모든 아이템이 KRW: 'KRW'
    - 환율(rate)이 하나라도 있음: 'BOTH' (USD + ₩환산 둘 다 표시)
    - 그 외 (USD only): 'USD'
    """
    if not items:
        return "BOTH"
    has_krw = False
    has_rate = False
    has_usd = False
    for it in items:
        cur = (it.get("currency") or "").upper()
        if cur == "KRW":
            has_krw = True
        else:
            has_usd = True
        if it.get("rate"):
            has_rate = True
    if has_krw and not has_usd:
        return "KRW"
    if has_rate:
        return "BOTH"
    return "USD"


@app.post("/api/invoice/upload-preview")
async def invoice_upload_preview(file: UploadFile = File(...)):
    """거래명세서 업로드 데이터(xlsx) → 문서번호별 거래명세서 미리보기.

    파일에 적힌 값과 셀 서식(업체별 소수점 자릿수)을 그대로 들고 온다.
    서버가 환율을 다시 곱하거나 반올림하지 않는다.
    """
    from starlette.concurrency import run_in_threadpool
    contents = await file.read()
    return await run_in_threadpool(parse_invoice_upload, contents)


@app.post("/api/invoice/upload-zip")
async def invoice_upload_zip(request: Request):
    """거래명세서 여러 건을 엑셀+PDF 로 묶어 ZIP 으로 내려준다."""
    from starlette.concurrency import run_in_threadpool
    from urllib.parse import quote
    data = await request.json()
    invoices = data.get("invoices") or []
    if not invoices:
        return JSONResponse({"error": "거래명세서가 없습니다."}, status_code=400)
    zip_bytes = await run_in_threadpool(build_invoice_zip_bytes, invoices)
    fname = quote(f"거래명세서_{datetime.now().strftime('%Y-%m-%d')}.zip")
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f"attachment; filename=invoices.zip; filename*=UTF-8''{fname}"},
    )


@app.post("/api/invoice/generate")
async def generate_invoice(data: dict):
    xlsx_bytes = _build_invoice_xlsx_bytes(data)
    date_str = data.get("date", "")
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=invoice_{date_str}.xlsx"},
    )

@app.post("/api/invoice/generate-pdf")
async def generate_invoice_pdf(data: dict):
    pdf_bytes = _build_invoice_pdf_bytes(data)
    date_str = data.get("date", "")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{date_str}.pdf"},
    )


# ==================== 1실 영업실적 변환 ====================

SALES_REPORT_DATA_DIRECT = [
    "FAMILY", "DID", "MPN", "QTY", "DCPL", "AMOUNT",
    "Quoted", "Amount", "Quote Creation", "DNNo.",
    "수입신고일", "수입환율", "FSE", "End customer",
]
SALES_REPORT_LOOKUP = ["한글업체명", "거래처코드"]
SALES_REPORT_DATA_TAIL = [
    "구분", "SP ($)", "Sales Amt ($)", "매출환율",
    "SP (KRW)", "Sales Amt(KRW)",
    "GP($)", "GP%($)", "GP(KRW)", "GP%(KRW)",
    "Delivery(Actual Ship date)", "계산서발행일자", "Month",
]
SALES_REPORT_OUT_COLS = SALES_REPORT_DATA_DIRECT + SALES_REPORT_LOOKUP + SALES_REPORT_DATA_TAIL
SALES_REPORT_DATE_COLS = {"수입신고일", "Delivery(Actual Ship date)", "계산서발행일자"}
SALES_REPORT_DEC3_COLS = {"GP%($)", "GP%(KRW)"}
SALES_REPORT_INT_COLS = {"SP (KRW)"}


def _build_customer_lookup(xls):
    """업체코드 시트(있으면)에서 요약/한글업체명 → (한글업체명, 거래처코드) 맵 빌드."""
    cust_sheet = None
    for s in xls.sheet_names:
        low = s.replace(" ", "").lower()
        if "업체" in s or "코드" in s or "거래처" in low or "customer" in low:
            cust_sheet = s
            break
    if cust_sheet is None and len(xls.sheet_names) > 1:
        cust_sheet = xls.sheet_names[1]
    if cust_sheet is None:
        return {}

    try:
        df = pd.read_excel(xls, sheet_name=cust_sheet, header=0)
    except Exception:
        return {}

    lookup = {}
    for _, r in df.iterrows():
        summary = r.get("요약")
        korean = r.get("한글업체명")
        code = r.get("거래처코드")
        kor_clean = clean_value(korean)
        # 거래처코드: 정수면 int, 문자열이면 그대로
        code_clean = clean_value(code)
        if isinstance(code_clean, float) and code_clean.is_integer():
            code_clean = int(code_clean)
        if summary is not None and not (isinstance(summary, float) and pd.isna(summary)):
            k = str(summary).strip().lower()
            if k:
                lookup[k] = (kor_clean, code_clean)
        if korean is not None and not (isinstance(korean, float) and pd.isna(korean)):
            k = str(korean).strip().lower()
            if k:
                lookup.setdefault(k, (kor_clean, code_clean))
    return lookup


@app.post("/api/sales-report/preview")
async def sales_report_preview(file: UploadFile = File(...)):
    """영업실적 데이터 양식 → 보고 양식(29열) 변환 미리보기."""
    contents = await file.read()
    try:
        xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")
    except Exception as e:
        return {"error": f"엑셀 읽기 실패: {e}"}
    if not xls.sheet_names:
        return {"error": "시트가 없습니다."}

    # 첫 시트를 데이터 시트로 사용
    data_sheet = xls.sheet_names[0]
    try:
        df = pd.read_excel(xls, sheet_name=data_sheet, header=0)
    except Exception as e:
        return {"error": f"데이터 시트 읽기 실패: {e}"}

    customer_lookup = _build_customer_lookup(xls)

    rows = []
    for _, row in df.iterrows():
        if row.isna().all():
            continue
        rec = {}
        # 직접 컬럼들
        for col in SALES_REPORT_DATA_DIRECT:
            rec[col] = clean_value(row.get(col))
        for col in SALES_REPORT_DATA_TAIL:
            rec[col] = clean_value(row.get(col))

        # Amount 자동 계산 (비어있으면 QTY × Quoted)
        if rec.get("Amount") in (None, "", 0):
            qty = to_float(rec.get("QTY"))
            quoted = to_float(rec.get("Quoted"))
            if qty is not None and quoted is not None:
                rec["Amount"] = round(qty * quoted, 2)

        # 한글업체명/거래처코드 조회 (End customer 기준)
        end_cust = rec.get("End customer")
        kor_name, kor_code = (None, None)
        if end_cust:
            key = str(end_cust).strip().lower()
            matched = customer_lookup.get(key)
            if matched:
                kor_name, kor_code = matched
        rec["한글업체명"] = kor_name
        rec["거래처코드"] = kor_code

        if not rec.get("FAMILY") and not rec.get("MPN") and not rec.get("DID"):
            continue
        rows.append(rec)
    return {"columns": SALES_REPORT_OUT_COLS, "rows": rows, "total": len(rows)}


def _try_parse_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, (datetime,)):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d")
    except Exception:
        return None


@app.post("/api/sales-report/export")
async def sales_report_export(request: Request):
    """미리보기 결과를 보고 양식 엑셀로 다운로드 (29열)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    data = await request.json()
    rows = data.get("rows", [])

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    header_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    bold = Font(bold=True)
    for j, h in enumerate(SALES_REPORT_OUT_COLS):
        cell = ws.cell(row=1, column=j + 1, value=h)
        cell.fill = header_fill
        cell.font = bold
        cell.alignment = Alignment(horizontal="center")

    for i, r in enumerate(rows, start=2):
        for j, col in enumerate(SALES_REPORT_OUT_COLS):
            v = r.get(col)
            if col in SALES_REPORT_DATE_COLS:
                d = _try_parse_date(v)
                if d:
                    cell = ws.cell(row=i, column=j + 1, value=d)
                    cell.number_format = "yyyy-mm-dd"
                    continue
            if col in SALES_REPORT_INT_COLS and isinstance(v, (int, float)):
                cell = ws.cell(row=i, column=j + 1, value=round(v))
                cell.number_format = "0"
                continue
            cell = ws.cell(row=i, column=j + 1, value=v)
            if col in SALES_REPORT_DEC3_COLS and isinstance(v, (int, float)):
                cell.number_format = "0%"

    widths = [
        12, 8, 24, 8, 9, 12,   # FAMILY DID MPN QTY DCPL AMOUNT
        8, 10, 14, 12, 13, 11, # Quoted Amount QuoteCreation DNNo. 수입신고일 수입환율
        9, 22, 28, 11,         # FSE End customer 한글업체명 거래처코드
        14, 9, 13, 10,         # 구분 SP($) Sales Amt($) 매출환율
        11, 16, 11, 9,         # SP(KRW) Sales Amt(KRW) GP($) GP%($)
        14, 9, 22, 14, 9,      # GP(KRW) GP%(KRW) Delivery 계산서발행일자 Month
    ]
    for j, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(j + 1)].width = w

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    from urllib.parse import quote
    fname = f"영업실적_보고_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    fname_enc = quote(fname)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=sales_report.xlsx; filename*=UTF-8''{fname_enc}"},
    )


# ==================== 1실 발주요청서 변환 ====================

@app.post("/api/po-report/preview")
async def po_report_preview(file: UploadFile = File(...)):
    """발주요청서 데이터 양식 → 보고 양식(표1·표2)."""
    contents = await file.read()
    return build_po_report_preview(contents)


@app.post("/api/po-report/export")
async def po_report_export(request: Request):
    """변환 결과를 엑셀(표1/표2/표3)로 다운로드."""
    from urllib.parse import quote
    data = await request.json()
    output = io.BytesIO(build_po_report_xlsx_bytes(data))
    fname = f"발주요청서_보고_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    fname_enc = quote(fname)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=po_report.xlsx; filename*=UTF-8''{fname_enc}"})
@app.post("/api/sales-summary/aggregate")
async def sales_summary_aggregate(files: list[UploadFile] = File(...)):
    """벤더별 영업실적 파일 다중 업로드 → 통합."""
    all_rows = []
    file_info = []
    for f in files:
        contents = await f.read()
        try:
            xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")
        except Exception as e:
            file_info.append({"name": f.filename, "vendor": "ERROR", "rows": 0, "error": str(e)[:100]})
            continue
        vendor = _detect_vendor(f.filename, xls)
        if vendor == "KEC":
            rows = _parse_kec(xls)
        elif vendor in ("DELTA", "RAMXEED(FUJITSU)"):
            rows = _parse_standard(xls, vendor)
        else:
            file_info.append({"name": f.filename, "vendor": vendor, "rows": 0, "error": "벤더 인식 실패"})
            continue
        all_rows.extend(rows)
        file_info.append({"name": f.filename, "vendor": vendor, "rows": len(rows)})

    # 벤더 정렬: DELTA → RAMXEED(FUJITSU) → KEC → 기타
    _vendor_order = {"DELTA": 0, "RAMXEED(FUJITSU)": 1, "KEC": 2}
    all_rows.sort(key=lambda r: _vendor_order.get(r.get("Vendor"), 99))

    vendors = {}
    for r in all_rows:
        v = r["Vendor"]
        if v not in vendors:
            vendors[v] = {"Sales$": 0, "GP$": 0, "SalesKRW": 0, "GPKRW": 0, "count": 0}
        vendors[v]["Sales$"] += to_float(r.get("Sales Amt ($)")) or 0
        vendors[v]["GP$"] += to_float(r.get("GP($)")) or 0
        vendors[v]["SalesKRW"] += to_float(r.get("Sales Amt(KRW)")) or 0
        vendors[v]["GPKRW"] += to_float(r.get("GP(KRW)")) or 0
        vendors[v]["count"] += 1
    summary = []
    for v, agg in vendors.items():
        summary.append({
            "Vendor": v,
            "Sales Total ($)": round(agg["Sales$"], 2),
            "GP Total ($)": round(agg["GP$"], 2),
            "Rate ($) (%)": round(agg["GP$"] / agg["Sales$"], 4) if agg["Sales$"] else 0,
            "Sales Total (KRW)": round(agg["SalesKRW"], 0),
            "GP Total (KRW)": round(agg["GPKRW"], 0),
            "Rate (KRW) (%)": round(agg["GPKRW"] / agg["SalesKRW"], 4) if agg["SalesKRW"] else 0,
            "건수": agg["count"],
        })
    summary.sort(key=lambda x: x["Vendor"])
    total_sales = sum(r["Sales Total ($)"] for r in summary)
    total_gp = sum(r["GP Total ($)"] for r in summary)
    total_sales_krw = sum(r["Sales Total (KRW)"] for r in summary)
    total_gp_krw = sum(r["GP Total (KRW)"] for r in summary)
    summary.append({
        "Vendor": "SUM",
        "Sales Total ($)": round(total_sales, 2),
        "GP Total ($)": round(total_gp, 2),
        "Rate ($) (%)": round(total_gp / total_sales, 4) if total_sales else 0,
        "Sales Total (KRW)": round(total_sales_krw, 0),
        "GP Total (KRW)": round(total_gp_krw, 0),
        "Rate (KRW) (%)": round(total_gp_krw / total_sales_krw, 4) if total_sales_krw else 0,
        "건수": sum(s.get("건수", 0) for s in summary if s["Vendor"] != "SUM"),
    })

    dates = sorted([r["수입신고일"] for r in all_rows if r.get("수입신고일")])
    date_min = dates[0] if dates else ""
    date_max = dates[-1] if dates else ""
    month_counts = {}
    for d in dates:
        m = d[:7] if d else ""
        if m:
            month_counts[m] = month_counts.get(m, 0) + 1
    main_month = max(month_counts, key=month_counts.get) if month_counts else ""

    return {
        "files": file_info,
        "rows": all_rows,
        "summary": summary,
        "date_min": date_min,
        "date_max": date_max,
        "main_month": main_month,
        "total_rows": len(all_rows),
    }


@app.post("/api/sales-summary/export")
async def sales_summary_export(request: Request):
    """집계 결과를 라인별 상세(DATA) 시트 단일로 다운로드."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    data = await request.json()
    rows = data.get("rows", [])
    date_min = data.get("date_min", "")
    date_max = data.get("date_max", "")
    main_month = data.get("main_month", "")

    if date_min and date_max:
        d1 = date_min[5:7] + "." + date_min[8:10]
        d2 = date_max[5:7] + "." + date_max[8:10]
        data_sheet_name = f"DATA({d1}~{d2})"
    else:
        data_sheet_name = "DATA"

    wb = Workbook()
    header_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    header_font = Font(name="맑은 고딕", size=9, bold=True)
    body_font = Font(name="맑은 고딕", size=9)
    center = Alignment(horizontal="center", vertical="center")

    ws = wb.active
    ws.title = data_sheet_name
    for j, h in enumerate(SALES_AGG_COLS):
        c = ws.cell(row=1, column=j + 1, value=h)
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
    for i, r in enumerate(rows, start=2):
        for j, col in enumerate(SALES_AGG_COLS):
            cell = ws.cell(row=i, column=j + 1, value=r.get(col))
            cell.font = body_font
            if col in ("GP%($)", "GP%(KRW)"):
                cell.number_format = "0.00%"
            elif col == "U/P":
                cell.number_format = "0.000"
            elif col in ("AMOUNT", "수입환율", "Sales Amt ($)", "GP($)"):
                cell.number_format = "0.00"
    for j in range(len(SALES_AGG_COLS)):
        ws.column_dimensions[get_column_letter(j + 1)].width = 14
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    from urllib.parse import quote
    fname = f"매출현황_{main_month or '주간'}_영업4실_{datetime.now().strftime('%y%m%d')}.xlsx"
    fname_enc = quote(fname)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=sales_summary.xlsx; filename*=UTF-8''{fname_enc}"},
    )


# ==================== 공용 헬퍼 ====================

def _norm_str(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        if v.is_integer():
            return str(int(v))
    s = str(v).strip()
    if s.lower() in ("nan", "none"):
        return ""
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _parse_date(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, pd.Timestamp):
        return v.date() if hasattr(v, "date") else v
    try:
        ts = pd.to_datetime(v)
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None




# ==================== 출고내역 자동완성 (POS Report Fill) ====================

@app.post("/api/pos-report/fill")
async def pos_report_fill(
    raw_data: UploadFile = File(...),
    template: UploadFile = File(...),
    code_mapping: UploadFile = File(None),
):
    """RAW DATA + 템플릿 → 템플릿 '출고내역' 시트 E~M열 자동 채움.
    code_mapping (선택): 업체코드 매칭 시트가 들어있는 별도 파일 (예: RAWDATA.xlsx).
    제공하면 RAW의 업체코드 매칭 대신 이 파일의 매핑을 사용.
    """
    from openpyxl import load_workbook
    from datetime import date as dt_date

    raw_bytes = await raw_data.read()
    code_map_bytes = await code_mapping.read() if code_mapping else None
    try:
        raw_xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine="openpyxl")
    except Exception as e:
        # .xls(구버전), 파일 손상, 암호 걸림 등 대응
        msg = str(e)
        if "not a zip" in msg.lower() or "BadZipFile" in msg:
            try:
                raw_xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine="xlrd")
            except Exception as e2:
                return {"error": (
                    f"RAW DATA 파일을 읽을 수 없습니다 ({raw_data.filename}). "
                    f"올바른 .xlsx 파일인지 확인해주세요.\n"
                    f"가능한 원인: 옛날 .xls 형식 / 파일 손상 / 암호 걸림 / 확장자만 xlsx인 다른 형식"
                )}
        else:
            return {"error": f"RAW DATA 파일 열기 실패: {msg}"}

    def find_sheet(kws):
        for s in raw_xls.sheet_names:
            low = s.replace(" ", "").lower()
            for kw in kws:
                if kw.replace(" ", "").lower() in low:
                    return s
        return None

    sh_dc = find_sheet(["단가1", "수입가"])
    sh_qtn = find_sheet(["단가2", "QTN"])
    sh_asd = find_sheet(["단가3", "ASD"])

    # 업체코드 매칭: code_mapping 파일 우선, 없으면 raw에서 검색
    map_xls = None
    if code_map_bytes:
        try:
            map_xls = pd.ExcelFile(io.BytesIO(code_map_bytes), engine="openpyxl")
        except Exception as e:
            return {"error": f"매핑 파일 열기 실패: {e}"}
    else:
        map_xls = raw_xls

    sh_map = None
    for s in map_xls.sheet_names:
        try:
            df_test = pd.read_excel(map_xls, sheet_name=s, header=1, nrows=1)
            cols = [str(c).strip() for c in df_test.columns]
            has_code = any(c == "코드" or c == "업체코드" for c in cols)
            has_name = any(c == "거래처명" for c in cols)
            has_eng = any("지역명" in c or "영문" in c for c in cols)
            if has_code and has_name and has_eng:
                sh_map = s
                break
        except Exception:
            continue
    if not sh_map:
        # find_sheet 호환을 위해 같은 워크북 내에서 검색
        for s in map_xls.sheet_names:
            low = s.replace(" ", "").lower()
            if "마이크로칩업체명" in low or "업체코드매칭" in low:
                sh_map = s
                break

    missing = [n for n, v in [("단가1", sh_dc), ("단가2(QTN)", sh_qtn),
                              ("단가3(ASD)", sh_asd), ("업체코드매칭", sh_map)] if not v]
    if missing:
        return {"error": f"RAW DATA에서 시트 못 찾음: {', '.join(missing)}"}

    # DC 맵
    df_dc = pd.read_excel(raw_xls, sheet_name=sh_dc, header=0)
    dc_map = {}
    for _, row in df_dc.iterrows():
        dev = _norm_str(row.get("Device") or row.iloc[0])
        pr = to_float(row.get("DC") or (row.iloc[1] if df_dc.shape[1] > 1 else None))
        if dev and pr is not None:
            dc_map[dev.upper()] = pr

    # 업체코드 → 거래처명 / 영문이름(지역포함) / 영문이름(지역제외)
    df_map = pd.read_excel(map_xls, sheet_name=sh_map, header=1)
    code_to_name, code_to_eng, code_to_plain = {}, {}, {}
    for _, row in df_map.iterrows():
        code = _norm_str(row.get("코드") or row.iloc[0])
        name = _norm_str(row.get("거래처명") or (row.iloc[1] if df_map.shape[1] > 1 else None))
        eng = _norm_str(
            row.get("지역명포함이름") or row.get("고객사 영문이름")
            or (row.iloc[2] if df_map.shape[1] > 2 else None)
        )
        plain = _norm_str(row.get("지역명제외이름") or (row.iloc[3] if df_map.shape[1] > 3 else None))
        if code:
            if name: code_to_name[code] = name
            if eng: code_to_eng[code] = eng
            if plain: code_to_plain[code] = plain

    # ASD
    df_asd = pd.read_excel(raw_xls, sheet_name=sh_asd, header=0)
    asd_map = {}
    for _, row in df_asd.iterrows():
        part = _norm_str(row.get("PART#") or row.iloc[0])
        pr = to_float(row.get("PRICE") or (row.iloc[1] if df_asd.shape[1] > 1 else None))
        sd = _parse_date(row.iloc[2] if df_asd.shape[1] > 2 else None)
        ed = _parse_date(row.iloc[3] if df_asd.shape[1] > 3 else None)
        if part and pr is not None:
            existing = asd_map.get(part.upper())
            if not existing or (sd and existing[1] and sd > existing[1]):
                asd_map[part.upper()] = (pr, sd, ed)

    # QTN: (업체코드, MPN) 키로 그룹 (정답 수식과 동일한 매칭 룰)
    # 영문이름 → 모든 가능한 업체코드 (다중 매핑 — 동일 영문이름이 여러 코드에 등장하는 경우)
    from collections import defaultdict
    eng_to_codes = defaultdict(list)
    # 매핑 시트 전체 다시 순회 — code_to_eng로 압축됐기 때문에 원본 df_map에서 빌드
    for _, mr in df_map.iterrows():
        code = _norm_str(mr.get("코드") or mr.iloc[0])
        eng = _norm_str(
            mr.get("지역명포함이름") or mr.get("고객사 영문이름")
            or (mr.iloc[2] if df_map.shape[1] > 2 else None)
        )
        plain = _norm_str(mr.get("지역명제외이름") or (mr.iloc[3] if df_map.shape[1] > 3 else None))
        if code and eng:
            eng_to_codes[eng.upper().strip()].append(code)
        if code and plain:
            eng_to_codes[plain.upper().strip()].append(code)

    df_qtn = pd.read_excel(raw_xls, sheet_name=sh_qtn, header=1)
    qtn_by_key = {}  # (code, MPN_upper) → list
    for _, row in df_qtn.iterrows():
        mpn = _norm_str(row.get("MPN"))
        cust = _norm_str(row.get("End Customer") or row.get("고객사 영문이름"))
        if not mpn:
            continue
        # 영문이름 → 모든 가능한 코드 (정확 매칭만, 부분 매칭 제거 — EXP는 정확 매칭만 사용)
        codes = []
        if cust:
            cust_norm = cust.upper().strip()
            codes = list(eng_to_codes.get(cust_norm, []))
        if not codes:
            continue
        rec = {
            "quote": _norm_str(row.get("Quote Item #") or row.get("Quote #")),
            "remains": to_float(row.get("Remains")) or 0,
            "start": _parse_date(row.get("StartDate") or row.get("시작일자")),
            "end": _parse_date(row.get("Item Expire Date") or row.get("유효일자")),
            "price": to_float(row.get("매입가")) or 0,
            "method": _norm_str(row.get("Quote Method")).upper(),
        }
        # 모든 가능한 코드 키에 행 추가 (중복 제거)
        for code in set(codes):
            key = (code, mpn.upper())
            qtn_by_key.setdefault(key, []).append(rec)

    # 템플릿 열기 (서식 보존)
    template_bytes = await template.read()
    try:
        wb = load_workbook(io.BytesIO(template_bytes))
    except Exception as e:
        msg = str(e)
        if "not a zip" in msg.lower() or "BadZipFile" in msg:
            return {"error": (
                f"템플릿 파일을 읽을 수 없습니다 ({template.filename}). "
                f"올바른 .xlsx 파일인지 확인해주세요. (.xls 옛 형식은 미지원 — Excel에서 .xlsx로 다시 저장하세요)"
            )}
        return {"error": f"템플릿 열기 실패: {msg}"}
    sheet_name = None
    for s in wb.sheetnames:
        if "출고내역" in s.replace(" ", ""):
            sheet_name = s
            break
    if not sheet_name:
        return {"error": "템플릿에 '출고내역' 시트가 없습니다."}
    ws = wb[sheet_name]

    DATA_START = 3  # 1행: 안내, 2행: 헤더, 3행~: 데이터
    MAX_ROW = max(ws.max_row, 5000)

    # 헤더 (E~M) 작성
    headers_e_m = {
        5: "QTN#",
        6: "QTN 매입가",
        7: "가장저렴한단가",
        8: "ASD 혹은 QTN",
        9: "유효기간\n종료일자",
        10: "유효수량",
        11: "총출고수량",
        12: "체크결과",
        13: "고객사이름",
    }
    for col, val in headers_e_m.items():
        ws.cell(row=2, column=col).value = val

    # 1차 패스: (품번 + 업체코드) 조합별 총 출고수량 (K열용)
    part_code_total = {}
    for r in range(DATA_START, MAX_ROW + 1):
        b = ws.cell(row=r, column=2).value
        c = ws.cell(row=r, column=3).value
        d = ws.cell(row=r, column=4).value
        if c and d is not None:
            p = _norm_str(c).upper()
            cd = _norm_str(b)
            qq = to_float(d) or 0
            if p:
                key = (p, cd)
                part_code_total[key] = part_code_total.get(key, 0) + qq

    filled_count = 0

    # 2차 패스: 자동 채움
    for r in range(DATA_START, MAX_ROW + 1):
        a = ws.cell(row=r, column=1).value
        b = ws.cell(row=r, column=2).value
        c = ws.cell(row=r, column=3).value
        d = ws.cell(row=r, column=4).value
        if a is None and b is None and c is None and d is None:
            break
        if not (a and c):
            continue

        date_req = _parse_date(a) or dt_date.today()
        code_str = _norm_str(b)
        part_upper = _norm_str(c).upper()
        qty = to_float(d) or 0

        cust_name = code_to_name.get(code_str, "")
        cust_eng = code_to_eng.get(code_str, "")
        cust_plain = code_to_plain.get(code_str, "")

        dc_price = dc_map.get(part_upper)

        # ASD 조회
        asd_entry = asd_map.get(part_upper)
        asd_price = None
        asd_status = "none"
        if asd_entry:
            pr, sd, ed = asd_entry
            if sd and date_req < sd:
                asd_status = "not_started"
            elif ed and date_req > ed:
                asd_status = "expired"
            else:
                asd_price = pr
                asd_status = "valid"

        # QTN 조회 (업체코드+MPN 직접 매칭, 정답 수식과 동일)
        all_qtns = qtn_by_key.get((code_str, part_upper), [])

        # EXP 룰: 만료/qty_over 행은 매칭 대상에서 제외, 시트 행 순서 첫 매칭
        eligible = [q for q in all_qtns
                    if not (q["end"] and date_req > q["end"])  # 만료 제외
                    and qty <= (q["remains"] or 0)]              # 잔량 부족 제외
        qtn_chosen = eligible[0] if eligible else None
        qtn_status = "none"
        if qtn_chosen:
            if qtn_chosen["start"] and date_req < qtn_chosen["start"]:
                qtn_status = "before_start"
            else:
                qtn_status = "valid"

        # 가장저렴한단가 = QTN(valid+before_start) / ASD(valid) 중 최저가 (DC는 매입가라 제외)
        candidates = []
        if qtn_status in ("valid", "before_start"):
            candidates.append(("QTN", qtn_chosen["price"]))
        if asd_status == "valid":
            candidates.append(("ASD", asd_price))

        best_src, best_price = (None, None)
        if candidates:
            best_src, best_price = min(candidates, key=lambda x: x[1])

        # E QTN#, F QTN매입가 — valid + before_start (출고일자 조정)만 채움
        # 만료/수량초과는 EXP에서 비움
        if qtn_chosen and qtn_status in ("valid", "before_start"):
            ws.cell(row=r, column=5).value = qtn_chosen["quote"] or None
            ws.cell(row=r, column=6).value = qtn_chosen["price"]
        # G 가장저렴한단가, H 단가소스 (매칭 실패 시에도 "없음" 명시)
        if best_price is not None:
            ws.cell(row=r, column=7).value = best_price
            ws.cell(row=r, column=8).value = best_src
        else:
            ws.cell(row=r, column=8).value = "없음"
        # I 유효기간 종료일자, J 유효수량 — 단가소스가 valid QTN일 때만 (출고일자 조정 케이스 제외)
        if best_src == "QTN" and qtn_chosen and qtn_status == "valid":
            if qtn_chosen["end"]:
                ws.cell(row=r, column=9).value = qtn_chosen["end"]
            ws.cell(row=r, column=10).value = qtn_chosen["remains"]
        # K 총출고수량 (시트 전체에서 같은 품번+업체코드 조합의 D열 합)
        total_q = part_code_total.get((part_upper, code_str))
        if total_q:
            ws.cell(row=r, column=11).value = total_q
        # L 체크결과
        if not cust_name:
            check = "거래처매칭 실패"
        elif best_price is None:
            if qtn_status == "expired":
                check = "QTN만료"
            elif qtn_status == "qty_over":
                check = "수량초과"
            else:
                check = "없음"
        elif best_src == "ASD":
            check = "ASD적용"
        elif best_src == "QTN" and qtn_status == "before_start":
            check = "출고일자 조정"
        else:
            check = "정상"
        ws.cell(row=r, column=12).value = check
        # M 고객사이름 (매칭 실패면 빈칸)
        if cust_name:
            ws.cell(row=r, column=13).value = cust_name

        filled_count += 1

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    fname = f"POS_Report_filled_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={fname}",
            "X-Filled-Rows": str(filled_count),
        },
    )


# ==================== 5실 POS Report 자동완성 v2 (POS_Report 시트 채우기) ====================

# ==================== POS Report 자동 생성 (규칙 엔진) ====================

@app.post("/api/pos-auto/run")
async def pos_auto_run(
    rawdata: UploadFile = File(...),
    crm: UploadFile = File(None),
    address_master: UploadFile = File(None),
    approved_mapping: UploadFile = File(None),
    start_date: str = Form(...),
    end_date: str = Form(...),
    mode: str = Form("draft"),
):
    """RAWDATA(+CRM 고객사, 주소 마스터) → POS 27열 + 검증·예외·원장 시트 워크북.
    로직은 CLI(pos_auto) 와 동일. 원본 파일은 읽기만 한다."""
    import base64
    import json as _json
    from urllib.parse import quote as _quote

    from pos_auto.service import run_pos

    async def _read(f):
        return await f.read() if f is not None else None

    try:
        data, fname, summary = run_pos(
            await rawdata.read(), start_date, end_date,
            crm=await _read(crm), address_master=await _read(address_master),
            approved_mapping=await _read(approved_mapping), mode=mode,
        )
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        import traceback
        return {"error": f"실행 실패: {type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-1200:]}

    meta_b64 = base64.b64encode(
        _json.dumps(summary, ensure_ascii=False, default=str).encode("utf-8")).decode()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=pos_result.xlsx; "
                                   f"filename*=UTF-8''{_quote(fname)}",
            "X-Result-Json-B64": meta_b64,
        },
    )


@app.post("/api/pos-report/build-v2")
async def pos_report_build_v2(
    raw_data: UploadFile = File(...),
    template: UploadFile = File(...),
):
    """RAW DATA + POS Report 템플릿(POS_TEST 양식) → POS_Report 시트 27열 자동 채움.

    매칭 룰 (README 기반):
      - 출고내역 A:D = 출고일자, 업체코드, 품번, 출고수량
      - 단가2(QTN): (영문업체명, MPN, 출고일자가 시작/만료일 사이) → Quote#, 매입가, 잔량
      - 단가3(ASD): PART# + 출고일자 유효기간 → ASD 단가
      - Disti Purchase Cost = QTN vs ASD 더 저렴
      - 업체별매입가매출가: (업체코드, MPN) → 매출가
    """
    from openpyxl import load_workbook
    from datetime import date as dt_date
    from urllib.parse import quote as urlq

    raw_bytes = await raw_data.read()
    template_bytes = await template.read()

    # RAW DATA 열기 (확장자 가짜 xlsx 허용)
    try:
        raw_xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine="openpyxl")
    except Exception:
        try:
            raw_xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine="xlrd")
        except Exception as e:
            return {"error": f"RAW DATA 파일 열기 실패: {e}. .xlsx로 다시 저장하세요."}

    def find_sheet(kws):
        for s in raw_xls.sheet_names:
            low = s.replace(" ", "").replace(",", "").lower()
            for kw in kws:
                if kw.replace(" ", "").lower() in low:
                    return s
        return None

    sh_qtn = find_sheet(["단가2(QTN)", "qtn"])
    sh_asd = find_sheet(["단가3(ASD)", "asd"])
    sh_price = find_sheet(["업체별매입가매출가", "매출가"])

    # 업체코드 시트는 "지역명포함이름" 컬럼이 있는 시트로 고정 (단순 '업체코드' 시트 회피)
    sh_code = None
    for s in raw_xls.sheet_names:
        try:
            df_test = pd.read_excel(raw_xls, sheet_name=s, header=1, nrows=1)
            cols = [str(c) for c in df_test.columns]
            if any("지역명포함" in c for c in cols) or any("영문" in c.lower() for c in cols):
                sh_code = s
                break
        except Exception:
            continue
    # fallback: 키워드 매칭
    if not sh_code:
        sh_code = find_sheet(["마이크로칩업체명", "업체코드매칭", "업체코드"])
    missing = [n for n, v in [("QTN", sh_qtn), ("ASD", sh_asd), ("업체코드", sh_code)] if not v]
    if missing:
        return {"error": f"RAW DATA 필수 시트 없음: {', '.join(missing)}"}

    # === 인덱스 빌드 ===
    # QTN: (영문업체명 upper, MPN upper) → list of {quote, price, start, end, remains, method, end_customer}
    qtn_idx = {}
    df_qtn = pd.read_excel(raw_xls, sheet_name=sh_qtn, header=1)
    for _, row in df_qtn.iterrows():
        mpn = _norm_str(row.get("MPN"))
        end_customer = _norm_str(row.get("End Customer"))
        if not mpn or not end_customer:
            continue
        rec = {
            "quote": _norm_str(row.get("Quote Item #") or row.get("Quote #")),
            "price": to_float(row.get("매입가")) or 0,
            "resale_price": to_float(row.get("제안매출가")),
            "start": _parse_date(row.get("StartDate")),
            "end": _parse_date(row.get("Item Expire Date")),
            "remains": to_float(row.get("Remains")) or 0,
            "method": _norm_str(row.get("Quote Method")).upper(),
            "end_customer": end_customer,
        }
        key = (end_customer.upper(), mpn.upper())
        qtn_idx.setdefault(key, []).append(rec)

    # ASD: PART# upper → (price, start, end)
    asd_idx = {}
    df_asd = pd.read_excel(raw_xls, sheet_name=sh_asd, header=0)
    for _, row in df_asd.iterrows():
        part = _norm_str(row.get("PART#"))
        price = to_float(row.get("PRICE"))
        if not part or price is None:
            continue
        sd = _parse_date(row.get("validity start date ASD_2"))
        ed = _parse_date(row.get("validity end date ASD_2"))
        existing = asd_idx.get(part.upper())
        if not existing or (sd and existing[1] and sd > existing[1]):
            asd_idx[part.upper()] = (price, sd, ed)

    # 업체코드 → {거래처명, 영문이름(지역포함), 영문이름(지역제외)}
    code_idx = {}
    df_code = pd.read_excel(raw_xls, sheet_name=sh_code, header=1)
    for _, row in df_code.iterrows():
        code = _norm_str(row.get("코드") or row.iloc[0])
        if not code:
            continue
        code_idx[code] = {
            "name": _norm_str(row.get("거래처명") or (row.iloc[1] if df_code.shape[1] > 1 else None)),
            "eng_full": _norm_str(row.get("지역명포함이름") or (row.iloc[2] if df_code.shape[1] > 2 else None)),
            "eng_plain": _norm_str(row.get("지역명제외이름") or (row.iloc[3] if df_code.shape[1] > 3 else None)),
        }

    # 업체별매입가매출가: (업체코드, MPN upper) → 매출가
    resale_idx = {}
    if sh_price:
        try:
            df_price = pd.read_excel(raw_xls, sheet_name=sh_price, header=0)
            for _, row in df_price.iterrows():
                code = _norm_str(row.get("업체코드") or row.iloc[0])
                mpn = _norm_str(row.get("MPN"))
                resale = to_float(row.get("매출가"))
                if code and mpn:
                    resale_idx[(code, mpn.upper())] = resale
        except Exception:
            pass

    # === 템플릿 열기 ===
    try:
        wb = load_workbook(io.BytesIO(template_bytes))
    except Exception as e:
        return {"error": f"템플릿 파일 열기 실패: {e}"}

    pos_sheet_name = None
    ship_sheet_name = None
    for s in wb.sheetnames:
        s_clean = s.replace(" ", "").replace("_", "").lower()
        if "posreport" in s_clean:
            pos_sheet_name = s
        elif "출고내역" in s.replace(" ", ""):
            ship_sheet_name = s
    if not pos_sheet_name or not ship_sheet_name:
        return {"error": "템플릿에 'POS_Report' 또는 '출고내역' 시트가 없습니다."}

    ship_ws = wb[ship_sheet_name]
    pos_ws = wb[pos_sheet_name]

    # POS_Report 데이터 영역 클리어 (수식 제거 — 깨진 #REF! 포함)
    for r in range(2, pos_ws.max_row + 1):
        for c in range(1, 28):
            pos_ws.cell(row=r, column=c).value = None

    # === 출고내역 → POS_Report 매칭 채움 ===
    filled = 0
    pos_r = 2  # POS_Report 데이터 시작 행 (R1 = 헤더)
    for r in range(3, ship_ws.max_row + 1):  # 출고내역 R3부터
        ship_date = ship_ws.cell(row=r, column=1).value
        code = ship_ws.cell(row=r, column=2).value
        part = ship_ws.cell(row=r, column=3).value
        qty = ship_ws.cell(row=r, column=4).value
        if ship_date is None and code is None and part is None and qty is None:
            break
        if not (ship_date and part):
            continue

        date_req = _parse_date(ship_date) or dt_date.today()
        code_str = _norm_str(code) if code else ""
        part_upper = _norm_str(part).upper()
        qty_num = to_float(qty) or 0

        # 업체코드 → 영문이름
        cust = code_idx.get(code_str, {})
        eng_full = cust.get("eng_full", "")
        eng_plain = cust.get("eng_plain", "")
        cust_name = cust.get("name", "")

        # ASD 매칭
        asd_entry = asd_idx.get(part_upper)
        asd_price = None
        if asd_entry:
            pr, sd, ed = asd_entry
            if (not sd or date_req >= sd) and (not ed or date_req <= ed):
                asd_price = pr

        # QTN 매칭 (영문이름 풀 또는 부분 매칭)
        qtn_chosen = None
        candidates_qtn = []
        for (cust_k, mpn_k), recs in qtn_idx.items():
            if mpn_k != part_upper:
                continue
            if eng_full and cust_k == eng_full.upper():
                candidates_qtn.extend(recs)
            elif eng_plain and eng_plain.upper() in cust_k:
                candidates_qtn.extend(recs)
        # 유효 기간 + 잔량 만족하는 것 우선
        valid_qtns = [
            q for q in candidates_qtn
            if (not q["start"] or date_req >= q["start"])
            and (not q["end"] or date_req <= q["end"])
            and qty_num <= (q["remains"] or 0)
        ]
        if valid_qtns:
            qtn_chosen = min(valid_qtns, key=lambda x: x["price"])
        elif candidates_qtn:
            # 차선책: 유효기간 무시하고 가장 저렴
            qtn_chosen = min(candidates_qtn, key=lambda x: x["price"])

        # 매입가 = QTN vs ASD 더 저렴
        purchase_cost = None
        quote_num = None
        if qtn_chosen and asd_price is not None:
            if qtn_chosen["price"] <= asd_price:
                purchase_cost = qtn_chosen["price"]
                quote_num = qtn_chosen["quote"]
            else:
                purchase_cost = asd_price
        elif qtn_chosen:
            purchase_cost = qtn_chosen["price"]
            quote_num = qtn_chosen["quote"]
        elif asd_price is not None:
            purchase_cost = asd_price

        # 매출가 (업체별매입가매출가에서)
        resale_price = resale_idx.get((code_str, part_upper))
        if resale_price is None and qtn_chosen:
            resale_price = qtn_chosen.get("resale_price")

        # POS_Report 27열 채움
        pos_ws.cell(row=pos_r, column=2).value = part                  # MPN
        pos_ws.cell(row=pos_r, column=3).value = ship_date              # Ship Date
        pos_ws.cell(row=pos_r, column=4).value = qty_num                # Shipped Quantity
        if resale_price is not None:
            pos_ws.cell(row=pos_r, column=5).value = resale_price       # Resale Price (ASP)
        pos_ws.cell(row=pos_r, column=6).value = "USD"                  # Resale Currency Code
        if purchase_cost is not None:
            pos_ws.cell(row=pos_r, column=7).value = purchase_cost      # Disti Purchase Cost
            pos_ws.cell(row=pos_r, column=8).value = purchase_cost      # Adjusted Cost
        if asd_price is not None:
            pos_ws.cell(row=pos_r, column=9).value = asd_price          # DPA Price
        if quote_num:
            pos_ws.cell(row=pos_r, column=10).value = quote_num         # Microchip Quote Number
        if eng_full:
            pos_ws.cell(row=pos_r, column=11).value = eng_full          # Purchasing Customer Name
            # 도시명 추출 (예: "42dot - Seoul" → "Seoul")
            if " - " in eng_full:
                pos_ws.cell(row=pos_r, column=12).value = eng_full.split(" - ")[-1]
        pos_ws.cell(row=pos_r, column=15).value = "KR"                  # Purchasing Customer Country Code
        if qtn_chosen and qtn_chosen.get("end_customer"):
            ec = qtn_chosen["end_customer"]
            pos_ws.cell(row=pos_r, column=16).value = ec
            if " - " in ec:
                pos_ws.cell(row=pos_r, column=17).value = ec.split(" - ")[-1]
        pos_ws.cell(row=pos_r, column=20).value = "KR"                  # End Customer Country Code
        if code_str:
            pos_ws.cell(row=pos_r, column=24).value = code_str          # Purchasing Customer Number

        pos_r += 1
        filled += 1

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    base, ext = os.path.splitext(template.filename or "result.xlsx")
    fname = f"{base}_filled{ext or '.xlsx'}"
    fname_enc = urlq(fname)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=result.xlsx; filename*=UTF-8''{fname_enc}",
            "X-Filled-Rows": str(filled),
            "Access-Control-Expose-Headers": "X-Filled-Rows, Content-Disposition",
        },
    )


# ==================== 4실 출고요청 → 거래명세서 ====================

def _parse_yymmdd_sheet(sheet_name: str):
    """'260305' → '2026-03-05'"""
    s = str(sheet_name).strip()
    if len(s) == 6 and s.isdigit():
        try:
            return f"20{s[:2]}-{s[2:4]}-{s[4:6]}"
        except Exception:
            pass
    return None


@app.post("/api/shipping-request/parse")
async def shipping_request_parse(file: UploadFile = File(...)):
    """출고요청내역 → (출고일, 고객사, 통화)별 그룹핑."""
    contents = await file.read()
    try:
        xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")
    except Exception as e:
        return {"error": f"엑셀 읽기 실패: {e}"}

    rate_cache = {}
    groups = {}

    for sheet in xls.sheet_names:
        ship_date = _parse_yymmdd_sheet(sheet)
        if not ship_date:
            continue
        try:
            df = pd.read_excel(xls, sheet_name=sheet, header=1)
        except Exception:
            continue
        for _, row in df.iterrows():
            customer = _norm_str(row.get("Customer"))
            mpn = _norm_str(row.get("MPN"))
            qty = to_float(row.get("Q'ty"))
            if not customer or not mpn or not qty:
                continue
            sales = _norm_str(row.get("Sales"))
            vendor = _norm_str(row.get("Vendor"))
            currency = _norm_str(row.iloc[14]) if len(row) > 14 else None
            if not currency or currency.upper() not in ("KRW", "USD"):
                currency = "KRW"
            currency = currency.upper()
            # 컬럼 P(15)=입고가, Q(16)=판매가 — 거래명세서는 판매가 기준
            price = to_float(row.iloc[16]) if len(row) > 16 else None

            key = (ship_date, customer, currency)
            if key not in groups:
                groups[key] = {
                    "date": ship_date,
                    "customer": customer,
                    "currency": currency,
                    "sales": sales,
                    "vendor": vendor,
                    "rate": None,
                    "items": [],
                    "total_qty": 0,
                    "total_krw": 0,
                    "total_usd": 0,
                }

            item = {
                "part": mpn,
                "qty": qty,
                "price": price or 0,
                "currency": currency,
                "date": ship_date,
            }
            if currency == "USD":
                amount_usd = round((price or 0) * qty, 2)
                item["amount_usd"] = amount_usd
                item["price_krw"] = None
                item["amount_krw"] = None
                item["rate"] = None
                groups[key]["total_usd"] += amount_usd
            else:
                amount_krw = round((price or 0) * qty, 0)
                item["amount_usd"] = None
                item["price_krw"] = price or 0
                item["amount_krw"] = amount_krw
                item["rate"] = None
                groups[key]["total_krw"] += amount_krw

            groups[key]["items"].append(item)
            groups[key]["total_qty"] += qty

    out = sorted(groups.values(), key=lambda g: (g["date"], g["customer"], g["currency"]))
    for g in out:
        g["item_count"] = len(g["items"])
        g["total_usd"] = round(g["total_usd"], 2)
        g["total_krw"] = round(g["total_krw"], 0)

    return {
        "groups": out,
        "total_groups": len(out),
        "rate_cache": rate_cache,
    }


# ==================== 수불부 품목 필터 (4실) ====================

_SUBUL_ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-/]+$")
_SUBUL_RANGE_RE = re.compile(r"([A-Z]+)(\d+):([A-Z]+)(\d+)")


def _subul_load_items(inv_bytes):
    """InventoryRecipt 품목 리스트에서 품목코드 집합을 읽는다.
    .xls(Salesforce HTML) 우선, 실패 시 진짜 xlsx 로 폴백. 'Item' 열 자동 탐색."""
    import openpyxl
    items = set()
    try:
        tables = pd.read_html(io.BytesIO(inv_bytes), encoding="utf-8")
        for t in tables:
            col = None
            cols = ["".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
            for i, c in enumerate(cols):
                if "Item" in c:
                    col = t.columns[i]
                    break
            cand = [col] if col is not None else list(t.columns)
            for c in cand:
                for v in t[c].tolist():
                    s = str(v).strip()
                    if _SUBUL_ITEM_RE.match(s) and s not in ("Item", "Total", "nan"):
                        items.add(s)
    except Exception:
        pass
    if items:
        return items
    # 진짜 xlsx 폴백
    wb = openpyxl.load_workbook(io.BytesIO(inv_bytes), read_only=True, data_only=True)
    ws = wb.active
    for row in ws.iter_rows(values_only=True):
        for v in row:
            s = str(v).strip() if v is not None else ""
            if _SUBUL_ITEM_RE.match(s) and s not in ("Item", "Total"):
                items.add(s)
    return items


@app.post("/api/subul-filter/process")
async def subul_filter_process(
    inventory: UploadFile = File(...),
    subul: UploadFile = File(...),
):
    """InventoryRecipt 품목 리스트에 있는 품목 행만 남기고, KEC 수불부에서
    리스트에 없는 품목 행을 삭제한다. 원본 서식·헤더·합계행은 유지하고
    해당 데이터 행만 삭제. 매칭은 품목코드 완전일치."""
    import base64
    import json as _json2
    import openpyxl

    inv_bytes = await inventory.read()
    sub_bytes = await subul.read()

    # [1] 품목 리스트
    try:
        items = _subul_load_items(inv_bytes)
    except Exception as e:
        return {"error": f"품목 리스트 읽기 실패 ({inventory.filename}): {e}"}
    if not items:
        return {"error": f"품목 리스트에서 품목코드를 찾지 못했습니다 ({inventory.filename})."}

    # [2] 수불부 열기 (서식 보존 = data_only 안 함)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(sub_bytes))
    except Exception as e:
        return {"error": f"수불부 읽기 실패 ({subul.filename}): {e}"}
    ws = wb.active

    # 품목(DESCRIP) 열 찾기 — 없으면 D열(4) 기본
    descrip_col = 4
    found_col = False
    for r in range(1, min(15, ws.max_row) + 1):
        for c in range(1, min(20, ws.max_column) + 1):
            v = ws.cell(row=r, column=c).value
            if v is not None and "DESCRIP" in str(v).upper():
                descrip_col = c
                found_col = True
                break
        if found_col:
            break

    # 데이터 시작 행 찾기 — 품목코드가 처음 등장하는 행
    start_row = 8
    for r in range(1, min(30, ws.max_row) + 1):
        v = ws.cell(row=r, column=descrip_col).value
        s = str(v).strip() if v is not None else ""
        if _SUBUL_ITEM_RE.match(s) and "DESCRIP" not in s.upper():
            start_row = r
            break

    # 삭제 대상 수집 (빈/합계행은 유지)
    # 매칭은 품목코드 완전일치. 끝자리가 다르면(예: /PS vs /PSU) 다른 품목이므로
    # 완전일치가 아닌 행은 삭제한다.
    to_delete = []
    deleted_items = set()
    kept = 0
    for r in range(start_row, ws.max_row + 1):
        v = ws.cell(row=r, column=descrip_col).value
        s = str(v).strip() if v is not None else ""
        if s == "":
            continue
        if s in items:
            kept += 1
        else:
            to_delete.append(r)
            deleted_items.add(s)

    # 인접한 삭제행을 연속 블록으로 묶어 batch 삭제한다.
    # 행별 delete_rows 는 호출마다 아래쪽 셀 전체를 이동(_move_cell) → O(n²) 라
    # 대용량 수불부(3천행)에서 80초+ 걸려 nginx 60초 타임아웃 → 504. 블록 batch 로 ~6배 단축.
    _rows = sorted(to_delete)
    _blocks = []  # (start_row, count)
    _i = 0
    while _i < len(_rows):
        _j = _i
        while _j + 1 < len(_rows) and _rows[_j + 1] == _rows[_j] + 1:
            _j += 1
        _blocks.append((_rows[_i], _rows[_j] - _rows[_i] + 1))
        _i = _j + 1
    for _start, _count in sorted(_blocks, reverse=True):  # 아래(높은 행)부터 삭제해 인덱스 보존
        ws.delete_rows(_start, _count)

    # 행 삭제 후 합계행 수식 범위 보정 (openpyxl 자동 보정 안 함 → 순환참조 방지)
    for row in ws.iter_rows():
        for cell in row:
            f = cell.value
            if not isinstance(f, str) or not f.startswith("=") or not _SUBUL_RANGE_RE.search(f):
                continue
            target_end = cell.row - 1

            def _repl(m, _ds=start_row, _te=target_end):
                c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
                if r1 <= _ds and r2 >= _ds:
                    return f"{c1}{_ds}:{c2}{_te}"
                return m.group(0)

            new_f = _SUBUL_RANGE_RE.sub(_repl, f)
            if new_f != f:
                cell.value = new_f

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    meta = {
        "kept": kept,
        "deleted": len(to_delete),
        "inventory_items": len(items),
        "descrip_col": openpyxl.utils.get_column_letter(descrip_col),
        "start_row": start_row,
        "deleted_items": sorted(deleted_items)[:50],
    }
    meta_b64 = base64.b64encode(_json2.dumps(meta, ensure_ascii=False).encode("utf-8")).decode("ascii")

    src = os.path.splitext(subul.filename or "수불부.xlsx")[0]
    from urllib.parse import quote
    fname = quote(f"{src}_filtered.xlsx")
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{fname}",
            "X-Result-Json-B64": meta_b64,
        },
    )


# ==================== 자재 (AI 에이전트 기반 출고 자동등록 — 배치) ====================

import json as _json

@app.post("/api/jaejae/init-mark")
async def jaejae_init_mark(file: UploadFile = File(...)):
    """첫 사용 시: shipping management 기존 모든 데이터 행에 'AI처리됨' 마커.
    이후 신규 입력 행만 자동등록 대상이 됨.
    """
    from openpyxl import load_workbook as _lw
    from urllib.parse import quote
    contents = await file.read()
    try:
        wb = _lw(io.BytesIO(contents))
        marked = mark_all_existing_as_processed(wb)
        out_buf = io.BytesIO()
        wb.save(out_buf)
        out_bytes = out_buf.getvalue()
    except Exception as e:
        return {"error": f"초기화 실패: {e}"}

    base, ext = os.path.splitext(file.filename or "result.xlsx")
    fname = f"{base}_초기화됨{ext or '.xlsx'}"
    fname_enc = quote(fname)
    return StreamingResponse(
        io.BytesIO(out_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=result.xlsx; filename*=UTF-8''{fname_enc}",
            "X-Marked-Count": str(marked),
            "Access-Control-Expose-Headers": "X-Marked-Count, Content-Disposition",
        },
    )


@app.post("/api/jaejae/process-batch")
async def jaejae_process_batch(file: UploadFile = File(...)):
    """(레거시) 업로드 파일 드라이런 — 미리보기만."""
    contents = await file.read()
    try:
        result = compute_changes_dryrun(contents)
    except Exception as e:
        return {"error": f"실패: {e}"}
    return result


@app.post("/api/jaejae/process-direct")
async def jaejae_process_direct(save: bool = False):
    """xlwings로 현재 활성 Excel 워크북을 직접 수정."""
    try:
        from jaejae_xl import process as xl_process
        result = xl_process(book_path=None, save=save, verbose=False)
    except Exception as e:
        import traceback
        return {"error": f"xlwings 실패: {e}", "trace": traceback.format_exc()[-1000:]}
    return result


@app.post("/api/microchip-match/build-sheet")
async def microchip_build_sheet(file: UploadFile = File(...)):
    """업로드 파일에 '출고기준(백록매칭)' 시트를 생성/덮어써서 반환.
    원본 시트(백록260324, 출고내역, FAB2 등)는 그대로 보존.
    """
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter
    from urllib.parse import quote

    contents = await file.read()
    sheet_name, columns, records = parse_excel(contents)
    if not records:
        return {"error": "파싱 결과 없음. 파일에 백록/출고내역 시트가 있는지 확인."}

    wb = load_workbook(io.BytesIO(contents))
    target_sheet = "출고기준(백록매칭)"
    if target_sheet in wb.sheetnames:
        del wb[target_sheet]
    ws = wb.create_sheet(target_sheet, 0)  # 첫 번째 위치

    # 그룹 헤더 (1행)
    ws.cell(row=1, column=1, value="식별 정보")
    ws.cell(row=1, column=10, value="출하이력")
    ws.cell(row=1, column=16, value="BLOG 2026(CRD기준)")
    bold = Font(bold=True, size=11)
    sky = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    head_fill = PatternFill(start_color="87CEEB", end_color="87CEEB", fill_type="solid")
    center = Alignment(horizontal="center", vertical="center")
    for col_idx in (1, 10, 16):
        c = ws.cell(row=1, column=col_idx)
        c.font = bold; c.fill = sky; c.alignment = center

    # 컬럼 헤더 (2행)
    for j, col in enumerate(columns, start=1):
        c = ws.cell(row=2, column=j, value=col)
        c.font = bold; c.fill = head_fill; c.alignment = center

    # 데이터
    for i, rec in enumerate(records, start=3):
        for j, col in enumerate(columns, start=1):
            ws.cell(row=i, column=j, value=rec.get(col))

    # 컬럼 너비
    widths = {1: 12, 2: 24, 3: 8, 4: 22, 5: 22, 6: 22, 7: 22, 8: 10, 9: 8,
              10: 10, 11: 10, 12: 10, 13: 10, 14: 12, 15: 12, 16: 12,
              17: 8, 18: 8, 19: 8, 20: 8, 21: 8, 22: 8, 23: 8, 24: 8, 25: 8, 26: 8, 27: 26}
    for col_idx, w in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(columns))}{len(records)+2}"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    base, ext = os.path.splitext(file.filename or "result.xlsx")
    fname = f"{base}_매칭완료{ext or '.xlsx'}"
    fname_enc = quote(fname)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=result.xlsx; filename*=UTF-8''{fname_enc}",
            "X-Records-Count": str(len(records)),
            "Access-Control-Expose-Headers": "X-Records-Count, Content-Disposition",
        },
    )


@app.post("/api/microchip-match/ai-suggest")
async def microchip_ai_suggest(file: UploadFile = File(...), max_ai_rows: int = 30):
    """마이크로칩 매칭 — 하이브리드 AI Agent.
    1단계: 정확 믹스# 매칭 (결정론) / 2단계: rapidfuzz 후보 좁힘 (도구) / 3단계: Claude tool-use Agent 판단.
    """
    contents = await file.read()
    try:
        from microchip_agent import hybrid_match
        result = hybrid_match(contents, max_ai_rows=max_ai_rows)
    except Exception as e:
        import traceback
        return {"error": f"실패: {e}", "trace": traceback.format_exc()[-1000:]}
    return result


@app.post("/api/jaejae/undo-row")
async def jaejae_undo_row(payload: dict):
    """행별 되돌리기. payload = {snapshot: {...}, book: "파일명.xlsx"} (book은 옵션)."""
    try:
        from jaejae_xl import undo_row_snapshot
        snapshot = payload.get("snapshot") or {}
        book = payload.get("book")
        return undo_row_snapshot(snapshot, book_name=book)
    except Exception as e:
        import traceback
        return {"error": f"undo 실패: {e}", "trace": traceback.format_exc()[-1000:]}


@app.post("/api/inventory-analysis")
async def inventory_analysis(file: UploadFile = File(...)):
    """4실 자재 재고 분석.
    - 시트 'May inventory' (또는 'inventory' 포함): 현재고
    - 시트 'shipping management' (또는 'shipping' 포함): 출고 이력
    응답: items[], months[] (전체 기간)
    - ABC: 최근 6개월 누적 판매량 기준 파레토 70/20/10
    - 월평균: 최근 6개월
    """
    try:
        contents = await file.read()
        bio = io.BytesIO(contents)
        try:
            xl = pd.ExcelFile(bio)
        except Exception as e:
            return {"error": f"파일 열기 실패: {e}"}

        # 시트 자동 탐지
        inv_sheet = None
        ship_sheet = None
        for s in xl.sheet_names:
            ls = s.lower()
            if "inventory" in ls and inv_sheet is None:
                inv_sheet = s
            if "shipping" in ls and ship_sheet is None:
                ship_sheet = s
        if not inv_sheet:
            return {"error": f"'inventory' 시트를 찾을 수 없습니다. 시트 목록: {xl.sheet_names}"}
        if not ship_sheet:
            return {"error": f"'shipping' 시트를 찾을 수 없습니다. 시트 목록: {xl.sheet_names}"}

        # 재고: 헤더 자동 탐색 (Part# 셀이 있는 행)
        df_inv_raw = pd.read_excel(bio, sheet_name=inv_sheet, header=None)
        inv_header_row = None
        for i in range(min(10, len(df_inv_raw))):
            row_vals = [str(v).strip().lower() for v in df_inv_raw.iloc[i].tolist() if v is not None]
            if any("part" in v for v in row_vals) and any("ty" in v or "qty" in v for v in row_vals):
                inv_header_row = i
                break
        if inv_header_row is None:
            return {"error": f"'{inv_sheet}' 시트에서 Part# 헤더를 찾을 수 없습니다."}
        bio.seek(0)
        df_inv = pd.read_excel(bio, sheet_name=inv_sheet, header=inv_header_row)

        # 컬럼 표준화
        def find_col(df, patterns):
            for c in df.columns:
                cl = str(c).strip().lower()
                for p in patterns:
                    if p in cl:
                        return c
            return None
        inv_part_col = find_col(df_inv, ["part#", "part #", "p/n", "part"])
        inv_qty_col = find_col(df_inv, ["q'ty", "qty", "quantity"])
        inv_price_col = find_col(df_inv, ["매입가", "매입단가", "단가", "unit price", "u/p", "price"])
        inv_sr_col = find_col(df_inv, ["sr#", "sr #", "sr no.", "sr no"])
        if not inv_part_col or not inv_qty_col:
            return {"error": f"재고 시트의 Part#/Q'ty 컬럼 식별 실패. 컬럼: {list(df_inv.columns)}"}

        # 출고: 첫 행이 헤더
        bio.seek(0)
        df_ship = pd.read_excel(bio, sheet_name=ship_sheet)
        ship_date_col = find_col(df_ship, ["date"])
        ship_part_col = find_col(df_ship, ["part#", "part #", "p/n", "part"])
        ship_qty_col = find_col(df_ship, ["q'ty", "qty", "quantity"])
        if not (ship_date_col and ship_part_col and ship_qty_col):
            return {"error": f"출고 시트 컬럼 식별 실패. 컬럼: {list(df_ship.columns)}"}

        # PART# 정규화
        def norm_pn(v):
            if v is None: return ""
            s = str(v).strip()
            if s in (".", "-", ""): return ""
            return s.upper().replace(" ", "")

        def norm_sr(v):
            if v is None: return ""
            s = str(v).strip()
            if s in (".", "-", "", "nan", "NaN", "None"): return ""
            return s

        # 재고 집계 — (SR#, Part#) 단위로 구분 (같은 Part# 라도 SR# 다르면 별도 행)
        inv_map = {}  # (sr, norm_pn) -> {sr, pn(원형), stock, value}
        for _, r in df_inv.iterrows():
            pn = norm_pn(r.get(inv_part_col))
            if not pn: continue
            sr = norm_sr(r.get(inv_sr_col)) if inv_sr_col is not None else ""
            key = (sr, pn)
            qv = r.get(inv_qty_col)
            try:
                q = float(qv) if qv is not None and str(qv).strip() not in (".", "", "-") else 0
                if not (q == q):  # NaN check
                    q = 0
            except Exception:
                q = 0
            if key not in inv_map:
                inv_map[key] = {"sr": sr, "pn": str(r.get(inv_part_col)).strip(),
                                "stock": 0, "value": 0.0}
            inv_map[key]["stock"] += q
            # 매입가는 행마다 다를 수 있어 행별(재고×매입가)로 누적
            if inv_price_col is not None:
                pv = to_float(r.get(inv_price_col))
                if pv is not None:
                    inv_map[key]["value"] += q * pv

        # 출고 집계: pn -> {monthly: {ym: qty}, last: date, total: qty}
        ship_map = {}
        for _, r in df_ship.iterrows():
            pn = norm_pn(r.get(ship_part_col))
            if not pn: continue
            d = r.get(ship_date_col)
            try:
                if isinstance(d, str):
                    d = pd.to_datetime(d, errors="coerce")
                if pd.isna(d): continue
                d = pd.Timestamp(d)
            except Exception:
                continue
            qv = r.get(ship_qty_col)
            try:
                q = float(qv) if qv is not None and str(qv).strip() not in (".", "", "-") else 0
                if not (q == q):  # NaN check
                    q = 0
            except Exception:
                q = 0
            if q <= 0: continue
            ym = f"{d.year:04d}-{d.month:02d}"
            if pn not in ship_map:
                ship_map[pn] = {"monthly": {}, "last": None, "total": 0, "pn": str(r.get(ship_part_col)).strip()}
            ship_map[pn]["monthly"][ym] = ship_map[pn]["monthly"].get(ym, 0) + q
            if ship_map[pn]["last"] is None or d > ship_map[pn]["last"]:
                ship_map[pn]["last"] = d
            ship_map[pn]["total"] += q

        # 전체 월 리스트 (최소~최대)
        all_months = set()
        for v in ship_map.values():
            all_months.update(v["monthly"].keys())
        months_sorted = sorted(all_months)

        # 최근 6개월 식별
        recent6 = months_sorted[-6:] if len(months_sorted) >= 6 else months_sorted

        # PART# 통합 (재고 또는 출고에 등장) — ABC/판매지표는 Part# 단위
        inv_pns = {pn for (_sr, pn) in inv_map.keys()}
        all_pns = inv_pns | set(ship_map.keys())

        # 6개월 누적 판매량 (ABC 기준)
        recent_total = {}
        for pn in all_pns:
            sm = ship_map.get(pn, {}).get("monthly", {})
            recent_total[pn] = sum(sm.get(m, 0) for m in recent6)

        # ABC 분류 (파레토 70/20/10)
        sorted_by_total = sorted(all_pns, key=lambda p: recent_total[p], reverse=True)
        grand = sum(recent_total.values())
        abc_map = {}
        cum = 0
        for pn in sorted_by_total:
            if grand <= 0:
                abc_map[pn] = "C"
                continue
            cum += recent_total[pn]
            ratio = cum / grand
            if ratio <= 0.70:
                abc_map[pn] = "A"
            elif ratio <= 0.90:
                abc_map[pn] = "B"
            else:
                abc_map[pn] = "C"

        # 활동등급 산정 (재고계수 기반). 반환: (grade, coef|None)
        def _activity_grade(stock, mavg, total6):
            if total6 <= 0:                      # 6개월 무판매 → 비유동
                return "E", None
            coef = (stock / mavg) if mavg > 0 else None
            if coef is None or coef > 100:       # 재고 과다 → 비유동
                return "E", coef
            if coef <= 6:
                return "A", coef
            if coef <= 10:
                return "B", coef
            if coef <= 15:
                return "C", coef
            return "D", coef                     # 15 < coef <= 100

        # 표시 키: 재고의 모든 (SR#, Part#) + 출고에만 있는 Part#(SR# 공란)
        keys = list(inv_map.keys())
        for pn in ship_map.keys():
            if pn not in inv_pns:
                keys.append(("", pn))

        # items 빌드 — 행 = (SR#, Part#). 재고·매입가·재고금액은 그 행, 판매지표는 Part# 공유.
        items = []
        for (sr, pn) in keys:
            inv = inv_map.get((sr, pn), {})
            ship = ship_map.get(pn, {})
            stock = inv.get("stock", 0)
            stock_value = inv.get("value", 0.0)
            avg_price = (stock_value / stock) if stock else 0.0
            display_pn = inv.get("pn") or ship.get("pn") or pn
            monthly = ship.get("monthly", {})
            recent6_qty = [monthly.get(m, 0) for m in recent6]
            avg = (sum(recent6_qty) / len(recent6)) if recent6 else 0
            total6 = recent_total.get(pn, 0)
            months_with_sales = sum(1 for q in recent6_qty if q > 0)
            grade, coef = _activity_grade(stock, avg, total6)  # 재고계수는 이 행(로트)의 재고 기준
            liquidity = "비유동" if grade == "E" else "유동"
            ai_candidate = total6 > 0 and months_with_sales <= 1  # 들쭉날쭉 저판매
            last = ship.get("last")
            items.append({
                "sr": sr,
                "pn": display_pn,
                "stock": round(stock, 2),
                "avg_price": round(avg_price, 4),
                "stock_value": round(stock_value, 2),
                "monthly_avg": round(avg, 2),
                "stock_coef": round(coef, 2) if coef is not None else None,
                "activity_grade": grade,
                "liquidity": liquidity,
                "months_with_sales": months_with_sales,
                "ai_candidate": ai_candidate,
                "ai_reason": None,
                "last_sale": last.strftime("%Y-%m-%d") if last is not None else None,
                "recommended": round(avg * 3, 2),
                "abc": abc_map.get(pn, "C"),
                "recent_total": round(total6, 2),
                "monthly": [{"ym": m, "qty": float(monthly.get(m, 0))} for m in months_sorted],
            })
        items.sort(key=lambda x: x["recent_total"], reverse=True)

        # 활동등급별 롤업 (부품수 · 재고금액 합계)
        grade_rollup = {g: {"count": 0, "value": 0.0} for g in "ABCDE"}
        for x in items:
            g = x["activity_grade"]
            grade_rollup[g]["count"] += 1
            grade_rollup[g]["value"] += x["stock_value"]
        for g in grade_rollup:
            grade_rollup[g]["value"] = round(grade_rollup[g]["value"], 2)
        liquid_value = round(sum(grade_rollup[g]["value"] for g in "ABCD"), 2)
        nonliquid_value = grade_rollup["E"]["value"]

        return {
            "items": items,
            "months": months_sorted,
            "recent_months": recent6,
            "inv_sheet": inv_sheet,
            "ship_sheet": ship_sheet,
            "summary": {
                "total_pns": len(items),
                "with_stock": sum(1 for x in items if x["stock"] > 0),
                "with_history": sum(1 for x in items if x["recent_total"] > 0),
                "total_stock_value": round(sum(x["stock_value"] for x in items), 2),
                "has_price": inv_price_col is not None,
                "a_count": sum(1 for x in items if x["abc"] == "A"),
                "b_count": sum(1 for x in items if x["abc"] == "B"),
                "c_count": sum(1 for x in items if x["abc"] == "C"),
                "grade_rollup": grade_rollup,
                "liquid_value": liquid_value,
                "nonliquid_value": nonliquid_value,
                "ai_candidates": sum(1 for x in items if x["ai_candidate"]),
            },
        }
    except Exception as e:
        import traceback
        return {"error": f"분석 실패: {e}", "trace": traceback.format_exc()[-1500:]}


@app.post("/api/inventory-analysis/export")
async def inventory_analysis_export(request: Request):
    """재고 분석 결과를 분류별(ABC A/B/C · 재고부족) 시트로 나눠 엑셀 다운로드.
    body: { items: [...] }  (프론트의 data.items 그대로 전달)
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    data = await request.json()
    items = data.get("items", []) or []

    COLS = [
        ("sr", "SR#"),
        ("pn", "P/N"),
        ("stock", "현재고"),
        ("avg_price", "매입가"),
        ("stock_value", "재고금액"),
        ("monthly_avg", "월평균 판매"),
        ("last_sale", "최근 판매일"),
        ("recommended", "적정재고"),
        ("shortage", "부족수량"),
        ("abc", "ABC"),
        ("activity_grade", "활동등급"),
    ]

    def _num(v):
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def is_short(it):
        rec = _num(it.get("recommended"))
        return rec > 0 and _num(it.get("stock")) < rec

    def shortage(it):
        s = _num(it.get("recommended")) - _num(it.get("stock"))
        return round(s, 2) if is_short(it) else 0

    # 활동등급(A~E) 기준 시트 분할 — 화면의 활동등급과 동일. (구: ABC A/B/C 라 D,E 누락됐음)
    groups = [
        ("활동A", [x for x in items if x.get("activity_grade") == "A"]),
        ("활동B", [x for x in items if x.get("activity_grade") == "B"]),
        ("활동C", [x for x in items if x.get("activity_grade") == "C"]),
        ("활동D", [x for x in items if x.get("activity_grade") == "D"]),
        ("활동E(비유동)", [x for x in items if x.get("activity_grade") == "E"]),
        ("재고부족", [x for x in items if is_short(x)]),
    ]

    header_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    short_fill = PatternFill(start_color="FFE1E4", end_color="FFE1E4", fill_type="solid")
    header_font = Font(name="맑은 고딕", size=9, bold=True)
    body_font = Font(name="맑은 고딕", size=9)
    center = Alignment(horizontal="center", vertical="center")
    widths = [13, 34, 12, 11, 15, 13, 13, 12, 12, 7, 9]

    wb = Workbook()
    wb.remove(wb.active)  # 기본 시트 제거
    for title, rows in groups:
        ws = wb.create_sheet(title=f"{title}({len(rows)})"[:31])
        for j, (_k, label) in enumerate(COLS):
            c = ws.cell(row=1, column=j + 1, value=label)
            c.fill = header_fill
            c.font = header_font
            c.alignment = center
        for i, it in enumerate(rows, start=2):
            short = is_short(it)
            vals = {
                "sr": it.get("sr") or "",
                "pn": it.get("pn"),
                "stock": _num(it.get("stock")),
                "avg_price": _num(it.get("avg_price")),
                "stock_value": _num(it.get("stock_value")),
                "monthly_avg": _num(it.get("monthly_avg")),
                "last_sale": it.get("last_sale") or "—",
                "recommended": _num(it.get("recommended")),
                "shortage": shortage(it),
                "abc": it.get("abc"),
                "activity_grade": it.get("activity_grade"),
            }
            for j, (k, _label) in enumerate(COLS):
                cell = ws.cell(row=i, column=j + 1, value=vals[k])
                cell.font = body_font
                if k in ("stock", "monthly_avg", "recommended", "shortage"):
                    cell.number_format = "#,##0.##"
                if k == "avg_price":
                    cell.number_format = "#,##0.####"
                if k == "stock_value":
                    cell.number_format = "#,##0"
                if k in ("abc", "activity_grade"):
                    cell.alignment = center
                if short and k == "shortage":
                    cell.fill = short_fill
        for j, w in enumerate(widths):
            ws.column_dimensions[get_column_letter(j + 1)].width = w
        if rows:
            ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"

    if not wb.sheetnames:
        wb.create_sheet("데이터없음")["A1"] = "데이터 없음"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    from urllib.parse import quote
    fname = f"재고분석_분류별_영업4실_{datetime.now().strftime('%y%m%d')}.xlsx"
    fname_enc = quote(fname)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=inventory_analysis.xlsx; filename*=UTF-8''{fname_enc}"},
    )


@app.post("/api/inventory-analysis/ai-classify")
async def inventory_analysis_ai_classify(request: Request):
    """저판매 애매 부품(ai_candidate)을 Claude로 '유동 vs 비유동' 판정.
    body: { items: [{pn, stock, monthly_avg, stock_coef, months_with_sales, monthly:[{ym,qty}]}] }
    응답: { results: [{pn, liquidity('유동'|'비유동'), reason}], source }
    키 없거나 실패 시 규칙 fallback(전부 비유동/E)로 안전 반환.
    """
    body = await request.json()
    cands = body.get("items", []) or []
    if not cands:
        return {"results": [], "source": "none"}

    MAX_CAND = 80
    dropped = max(0, len(cands) - MAX_CAND)
    cands = cands[:MAX_CAND]

    def _fallback(reason="규칙판정(저판매 — AI 미사용)"):
        return {
            "results": [{"pn": c.get("pn"), "liquidity": "비유동", "reason": reason} for c in cands],
            "source": "rule_fallback",
            "dropped": dropped,
        }

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {**_fallback("규칙판정(ANTHROPIC_API_KEY 없음)"), "error": "ANTHROPIC_API_KEY 미설정 — 규칙으로 비유동 처리"}

    # 후보 요약 (최근 6개월 판매 패턴 포함)
    lines = []
    for c in cands:
        recent = c.get("monthly", [])[-6:]
        pat = ",".join(str(int(m.get("qty", 0))) for m in recent)
        lines.append(
            f"- PN={c.get('pn')} | 현재고={c.get('stock')} | 월평균판매={c.get('monthly_avg')} "
            f"| 재고계수={c.get('stock_coef')} | 최근6개월판매월수={c.get('months_with_sales')} | 최근6개월판매={pat}"
        )
    listing = "\n".join(lines)

    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pn": {"type": "string"},
                        "liquidity": {"type": "string", "enum": ["유동", "비유동"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["pn", "liquidity", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }

    prompt = (
        "너는 반도체 유통사 재고 분석가다. 아래는 '판매가 매우 저조해(6개월 중 1달만 판매 등) 자동 분류가 애매한' 부품 목록이다.\n"
        "각 부품을 다음 둘 중 하나로 판정하라:\n"
        "- '비유동': 사실상 죽은 재고(악성). 판매가 거의 없고 앞으로도 소진 가망이 낮음.\n"
        "- '유동': 비록 6개월 중 가끔만 나갔지만, 한 번에 큰 수량이 나가는 등 실제로는 정상 회전하는 재고.\n"
        "판단 기준: 최근 판매 패턴(가끔 큰 수량 = 유동 가능성 ↑ / 찔끔 한두 개 = 비유동), 재고계수(높을수록 비유동), 월평균.\n"
        "각 부품마다 한국어로 한 줄 사유를 붙여라.\n\n"
        f"부품 목록:\n{listing}\n\n"
        "반드시 입력의 모든 PN에 대해 결과를 반환하라."
    )

    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=8000,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        import json as _json
        parsed = _json.loads(text)
        results = parsed.get("results", [])
        # 입력에 있는데 응답에서 빠진 PN은 비유동으로 보강
        seen = {r.get("pn") for r in results}
        for c in cands:
            if c.get("pn") not in seen:
                results.append({"pn": c.get("pn"), "liquidity": "비유동", "reason": "AI 무응답 — 규칙 보강"})
        return {"results": results, "source": "ai", "dropped": dropped}
    except anthropic.AuthenticationError:
        return {**_fallback("규칙판정(API 키 인증 실패)"), "error": "API 키 인증 실패"}
    except Exception as e:
        return {**_fallback(f"규칙판정(AI 호출 실패)"), "error": f"AI 호출 실패: {e}"}


# ==================== 부품 라벨 검수 (label-inspector 통합) ====================
# label_app.py = label-inspector/app.py 를 모듈로 재사용(서버부는 __main__ 가드라 미실행).
# /materials(React)는 이 /label 페이지를 iframe 으로 띄운다. fetch 경로(/inspect·/master)는
# 원본 페이지 그대로라 루트에 동일 경로로 등록한다.

@app.get("/label", response_class=HTMLResponse)
async def label_page():
    import label_app
    return HTMLResponse(label_app.PAGE)


@app.get("/master")
async def label_master_status():
    import label_app
    return {"parts": len(label_app.BY_PART), "mobis": len(label_app.BY_MOBIS)}


@app.post("/inspect")
async def label_inspect(request: Request):
    # 블로킹(PIL + Claude 비전 API 대기)이라 스레드풀로 — main_aws.py 와 동일.
    from starlette.concurrency import run_in_threadpool
    import label_app, dataurl
    payload = await request.json()
    try:
        raw_b64 = dataurl.to_b64(payload["image"])   # data URL 접두 제거
        return await run_in_threadpool(label_app.inspect_one, raw_b64)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})


@app.post("/master")
async def label_master_upload(request: Request):
    import label_app, dataurl
    payload = await request.json()
    try:
        os.makedirs(label_app.DATA_DIR, exist_ok=True)
        with open(label_app.MASTER_PATH, "wb") as f:
            f.write(dataurl.to_bytes(payload["file"]))
        label_app.reload_master()
        if not label_app.BY_PART:
            raise ValueError("마스터를 읽었지만 부품이 0건입니다. 'Apr inventory' 시트가 있는 파일인지 확인하세요.")
        return {"ok": True, "parts": len(label_app.BY_PART), "mobis": len(label_app.BY_MOBIS)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})


# ==================== 자재 라벨 생성 (label-maker 통합) ====================
# labelmaker_app.py = label-maker/label_maker_web.py 를 모듈로 재사용.
# /materials(React) 의 '라벨 생성' 탭이 이 /labelmaker 페이지를 iframe 으로 띄운다.
# 페이지 안의 fetch 경로는 /labelmaker/* 로 맞춰 두었다 (루트 경로 충돌 방지).

@app.get("/labelmaker", response_class=HTMLResponse)
async def labelmaker_page():
    import labelmaker_app
    return HTMLResponse(labelmaker_app.PAGE)


@app.post("/labelmaker/upload")
async def labelmaker_upload(request: Request):
    """Mobis 출고내역 엑셀 업로드 → LOT 파싱 (암호화 파일 자동 복호화)."""
    # 블로킹(20만 LOT 파싱 ~26초)이라 스레드풀로 — main_aws.py 와 동일.
    from starlette.concurrency import run_in_threadpool
    import labelmaker_app, dataurl
    payload = await request.json()
    try:
        n_lots, _ = await run_in_threadpool(
            labelmaker_app.ingest,
            dataurl.to_bytes(payload["file"]),
            payload.get("password") or labelmaker_app.DEFAULT_PW)
        return {"ok": True, "lots": n_lots, "materials": labelmaker_app.MATERIALS}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500,
                            content={"error": f"{type(e).__name__}: {e}"})


@app.post("/labelmaker/lots")
async def labelmaker_lots(request: Request):
    import labelmaker_app
    payload = await request.json()
    try:
        ls, total = labelmaker_app.lots_for(payload["mobis_id"])
        return {"lots": ls, "total": total, "shown": len(ls)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500,
                            content={"error": f"{type(e).__name__}: {e}"})




@app.post("/labelmaker/generate", response_class=HTMLResponse)
async def labelmaker_generate(request: Request):
    """선택한 LOT + 수량 → 라벨 HTML (새 창에서 인쇄)."""
    import labelmaker_app, labelmaker_core as LM
    import datetime as _dt
    payload = await request.json()
    today = _dt.datetime.now().strftime("%Y%m%d")
    fields = []
    for it in payload["items"]:
        if not str(it.get("qty") or "").strip():        # 수량은 파일(엑셀 수량 열)에서만 온다
            return JSONResponse(status_code=400,
                                content={"error": f"수량이 없는 LOT 입니다: {it.get('lot')}"})
        fields.append(dict(
            material=it.get("material_code") or LM.material_code(it.get("mobis_id", "")),
            serial=LM.serial_from(it["datecode"], it["lot"]),
            qty=it["qty"], maker="SJYV",
            vpn=it["vpn"], msl=str(it.get("msl") or labelmaker_app.DEFAULT_MSL), lot=it["lot"],
            stock_day=today))
    return HTMLResponse(LM.render_page(fields))


# 프론트엔드 정적 파일 서빙 (배포용)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=os.path.join(STATIC_DIR, "static")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
