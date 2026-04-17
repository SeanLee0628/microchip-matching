from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import pandas as pd
import io
import os
import math
import uuid
from datetime import datetime

from database import engine, get_db, Base
from models import MicrochipMatching, FIELD_MAP, REVERSE_MAP
from models_ublox import UbloxBacklog, UBLOX_COLUMN_MAP, UBLOX_DISPLAY_COLUMNS
from models_sales import SalesPerformance as SalesModel, SALES_FIELD_MAP, SALES_REVERSE_MAP

# 테이블 생성
Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 열 인덱스 → 컬럼명 고정 매핑 (원본 시트 구조 기준)
COLUMN_INDEX_MAP = {
    0: "고객코드",
    1: "믹스#",
    2: "Sales",
    3: "고객",
    4: "END",
    5: "PURCHASING",
    6: "PART#",
    7: "FAB2",
    8: "LT",
    9: "2023년",
    10: "2024년",
    11: "2025년",
    12: "2026년",
    13: "23~25추이",
    14: "25-26(w/BL)",
    15: "BLOG TTL",
    16: "3월",
    17: "4월",
    18: "5월",
    19: "6월",
    20: "7월",
    21: "8월",
    22: "9월",
    23: "10월",
    24: "11월",
    25: "12월",
    26: "믹스#(customer&part)",
}

COLUMNS = list(COLUMN_INDEX_MAP.values())


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


def parse_excel(contents: bytes):
    """엑셀 파싱 → (sheet_name, columns, records)"""
    xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")

    target_sheet = None
    for name in xls.sheet_names:
        if "출고기준" in name or "백록매칭" in name:
            target_sheet = name
            break

    if not target_sheet:
        return None, None, None

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)

    header_row = None
    for i in range(min(10, len(df))):
        row_values = [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]
        if "고객코드" in row_values:
            header_row = i
            break

    if header_row is None:
        return target_sheet, None, None

    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df = df.dropna(how="all").reset_index(drop=True)

    col_names = []
    for j in range(df.shape[1]):
        if j in COLUMN_INDEX_MAP:
            col_names.append(COLUMN_INDEX_MAP[j])
        else:
            col_names.append(f"col_{j}")
    df.columns = col_names

    final_columns = [c for c in COLUMNS if c in df.columns]
    df = df[final_columns]

    records = []
    for _, row in df.iterrows():
        record = {}
        for col in final_columns:
            record[col] = clean_value(row[col])
        if record.get("고객코드") is not None:
            records.append(record)

    return target_sheet, final_columns, records


def record_to_db_row(record: dict, batch_id: str) -> MicrochipMatching:
    """엑셀 레코드 → DB 모델 변환"""
    float_fields = {
        "LT", "2023년", "2024년", "2025년", "2026년", "23~25추이",
        "25-26(w/BL)", "BLOG TTL", "3월", "4월", "5월", "6월",
        "7월", "8월", "9월", "10월", "11월", "12월",
    }

    kwargs = {"upload_batch": batch_id}
    for excel_col, db_field in FIELD_MAP.items():
        val = record.get(excel_col)
        if excel_col in float_fields:
            kwargs[db_field] = to_float(val)
        else:
            kwargs[db_field] = str(val) if val is not None else None

    return MicrochipMatching(**kwargs)


def db_row_to_record(row: MicrochipMatching) -> dict:
    """DB 모델 → 엑셀 레코드 변환"""
    record = {}
    for excel_col, db_field in FIELD_MAP.items():
        val = getattr(row, db_field)
        record[excel_col] = val
    return record


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    target_sheet, final_columns, records = parse_excel(contents)

    if target_sheet is None:
        return {"error": "출고기준(백록매칭) 시트를 찾을 수 없습니다."}
    if records is None:
        return {"error": "헤더 행을 찾을 수 없습니다."}

    # DB 저장: 믹스# (고객코드+PART#) 기준으로 중복 체크
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    inserted = 0
    updated = 0

    for r in records:
        key = r.get("믹스#")
        if not key:
            continue

        existing = db.query(MicrochipMatching).filter(
            MicrochipMatching.믹스 == key
        ).first()

        if existing:
            # 기존 데이터 → 최신 값으로 업데이트
            float_fields = {
                "LT", "2023년", "2024년", "2025년", "2026년", "23~25추이",
                "25-26(w/BL)", "BLOG TTL", "3월", "4월", "5월", "6월",
                "7월", "8월", "9월", "10월", "11월", "12월",
            }
            for excel_col, db_field in FIELD_MAP.items():
                val = r.get(excel_col)
                if excel_col in float_fields:
                    setattr(existing, db_field, to_float(val))
                else:
                    setattr(existing, db_field, str(val) if val is not None else None)
            existing.upload_batch = batch_id
            updated += 1
        else:
            # 새로운 데이터 → 추가
            db.add(record_to_db_row(r, batch_id))
            inserted += 1

    db.commit()

    # 전체 데이터 반환
    all_rows = db.query(MicrochipMatching).order_by(MicrochipMatching.id).all()
    all_records = [db_row_to_record(row) for row in all_rows]

    return {
        "sheet_name": target_sheet,
        "columns": final_columns,
        "data": all_records,
        "total_rows": len(all_records),
        "saved_to_db": True,
        "batch_id": batch_id,
        "inserted": inserted,
        "updated": updated,
    }


@app.delete("/api/data")
async def reset_data(db: Session = Depends(get_db)):
    """DB 전체 초기화"""
    count = db.query(MicrochipMatching).count()
    db.query(MicrochipMatching).delete()
    db.commit()
    return {"deleted": count}


@app.get("/api/data")
async def get_data(db: Session = Depends(get_db)):
    """DB에서 저장된 데이터 조회"""
    rows = db.query(MicrochipMatching).order_by(MicrochipMatching.id).all()

    if not rows:
        return {"data": [], "columns": COLUMNS, "total_rows": 0}

    records = [db_row_to_record(r) for r in rows]
    batch_id = rows[0].upload_batch
    uploaded_at = rows[0].uploaded_at.strftime("%Y-%m-%d %H:%M") if rows[0].uploaded_at else None

    return {
        "sheet_name": "마이크로칩(매칭)",
        "columns": COLUMNS,
        "data": records,
        "total_rows": len(records),
        "batch_id": batch_id,
        "uploaded_at": uploaded_at,
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

        # 필터 설정
        ws.auto_filter.ref = ws.dimensions

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=microchip_matching_export.xlsx"},
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
