from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import pandas as pd
import io
import os
import math
import json
import re
import uuid
import time as _time
from datetime import datetime

from dynamo import DTable, create_tables
import crd_board

try:
    create_tables()
except Exception as e:
    print(f"Table creation skipped: {e}")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Result-Json-B64", "Content-Disposition"],
)

ublox_tb = DTable("ublox")
sales_tb = DTable("sales")
auo_tb = DTable("auo")
micron_tb = DTable("micron")

# ==================== 유틸 ====================

def clean_value(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v

def to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return None

def safe_str(v):
    if v is None:
        return None
    s = str(v)
    return None if s in ("nan", "None", "") else s


# ==================== Microchip 매칭 ====================

COLUMNS = [
    "고객코드", "믹스#", "Sales", "고객", "END", "PURCHASING", "PART#", "FAB2", "LT",
    "2023년", "2024년", "2025년", "2026년", "23~25추이", "25-26(w/BL)", "BLOG TTL",
    "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월",
    "믹스#(customer&part)",
]
MONTH_COLUMNS = ["3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]


def _s(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _code_str(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() or None


def _parse_snapshot_date(sheet_name: str):
    m = re.search(r"(\d{6})$", sheet_name)
    if not m:
        return None
    s = m.group(1)
    try:
        return pd.Timestamp(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def _bucket_month(crd, snapshot_date):
    if not isinstance(crd, pd.Timestamp) or pd.isna(crd):
        return None
    if snapshot_date and crd < snapshot_date:
        if snapshot_date.year == 2026 and 3 <= snapshot_date.month <= 12:
            return snapshot_date.month
        return None
    if crd.year == 2026 and 3 <= crd.month <= 12:
        return crd.month
    return None


def build_matching_records(contents: bytes, cutoff_date=None):
    """백록YYMMDD + 출고내역 + FAB2 → 출고기준(백록매칭) 포맷.
    cutoff_date: 당해년도 출하 합산 기준. None이면 오늘 기준 당월 1일.
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

    # 당해년도 출하는 "전월 말까지" 누적만 합산 (영업 마감 패턴, 99% 일치 검증됨)
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
            mix = _s(row.get("믹스#"))
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
            if isinstance(date, pd.Timestamp) and not pd.isna(date):
                ship_date = date.date()
                if ship_date < month_cutoff:
                    year = ship_date.year
                    ship_agg[mix]["yearly"][year] = ship_agg[mix]["yearly"].get(year, 0) + qty

    bl_agg = {}
    if backlog_sheet:
        df = pd.read_excel(xls, sheet_name=backlog_sheet, header=0)
        for _, row in df.iterrows():
            mix = _s(row.get("믹스"))
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
                if bl_agg[mix]["LT"] is None and lt is not None:
                    bl_agg[mix]["LT"] = lt
            if month:
                bl_agg[mix]["monthly"][month] = bl_agg[mix]["monthly"].get(month, 0) + qty

    # 원본 수식: =XLOOKUP(PART#, FAB2!A:A, FAB2!G:G) — Remark 컬럼만, 첫 매칭만
    fab2_map = {}
    if fab2_sheet:
        df = pd.read_excel(xls, sheet_name=fab2_sheet, header=0)
        for _, row in df.iterrows():
            pn = _s(row.get("PN"))
            if pn and pn not in fab2_map:
                fab2_map[pn] = _s(row.get("Remark")) or "-"

    records = []
    for mix in set(ship_agg) | set(bl_agg):
        ship = ship_agg.get(mix, {})
        bl = bl_agg.get(mix, {})
        yearly = ship.get("yearly", {})
        monthly = bl.get("monthly", {})

        part_no = ship.get("PART#") or bl.get("PART#")
        end = ship.get("END") or bl.get("END")

        blog_ttl = sum(monthly.values()) if monthly else 0
        y2025 = yearly.get(2025)
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
            "2023년": yearly.get(2023),
            "2024년": yearly.get(2024),
            "2025년": yearly.get(2025),
            "2026년": yearly.get(2026),
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
    target_sheet, final_columns, records = build_matching_records(contents)

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


# ==================== u-blox 백로그 ====================

UBLOX_DISPLAY_COLUMNS = [
    "Order Name", "Order No", "PO No Line Item", "Invoice Number", "Reference",
    "Account Name", "Account Number", "Reporting uB Office", "Order Status",
    "Type Number", "Frame Order", "Order Date", "Request Date", "Delivery Date",
    "전일 Delivery Date", "DELINQ", "Qty Ordered", "Qty Invoiced",
    "Price per unit", "Total Value", "End Customer", "End Customer No",
    "Project Owner", "Project Owner No",
]

UBLOX_COL_MAP = {
    0: "Order Name", 1: "Order No", 2: "PO No Line Item", 3: "Invoice Number",
    4: "Reference", 5: "Account Name", 6: "Account Number", 7: "Reporting uB Office",
    8: "Order Status", 9: "Type Number", 10: "Frame Order", 11: "Order Date",
    12: "Request Date", 13: "Delivery Date", 14: "전일 Delivery Date",
    15: "DELINQ", 16: "Qty Ordered", 17: "Qty Invoiced", 18: "Price per unit",
    19: "Total Value", 20: "End Customer", 21: "End Customer No",
    22: "Project Owner", 23: "Project Owner No",
}
FLOAT_COLS = {"DELINQ", "Qty Ordered", "Qty Invoiced", "Price per unit", "Total Value"}


@app.post("/api/ublox/upload")
async def upload_ublox(file: UploadFile = File(...)):
    contents = await file.read()
    xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")
    df = pd.read_excel(xls, sheet_name=0, header=None)

    header_row = 0
    for i in range(min(5, len(df))):
        if "Order Name" in [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]:
            header_row = i
            break

    df = df.iloc[header_row + 1:].reset_index(drop=True).dropna(how="all").reset_index(drop=True)

    records = []
    for _, row in df.iterrows():
        rec = {}
        has_data = False
        for idx, col_name in UBLOX_COL_MAP.items():
            if idx >= len(row):
                continue
            v = clean_value(row.iloc[idx])
            if col_name in FLOAT_COLS:
                rec[col_name] = to_float(v)
            elif isinstance(v, pd.Timestamp):
                rec[col_name] = v.strftime("%Y-%m-%d")
            else:
                rec[col_name] = safe_str(v)
            if v is not None:
                has_data = True
        if has_data and rec.get("Order Name"):
            records.append(rec)

    # 버전 관리
    all_existing = ublox_tb.scan_all()
    version_nums = set()
    for item in all_existing:
        try:
            version_nums.add(int(item.get("upload_version", "0")))
        except ValueError:
            pass
    prev_version_num = max(version_nums) if version_nums else 0
    new_version_num = prev_version_num + 1
    prev_version = str(prev_version_num) if prev_version_num > 0 else None
    new_version = str(new_version_num)

    # 전일 비교
    prev_map = {}
    if prev_version and prev_version != "0":
        prev_items = ublox_tb.query("upload_version", prev_version)
        for item in prev_items:
            d = json.loads(item.get("data", "{}"))
            prev_map[d.get("Order Name", "")] = d

    # 저장 (order_name + 행번호로 unique SK)
    for i, r in enumerate(records):
        ublox_tb.put({
            "upload_version": new_version,
            "order_name": f"{r.get('Order Name', '')}_{i}",
            "data": json.dumps(r, ensure_ascii=False, default=str),
        })

    # 변경 비교
    for r in records:
        change = {"type": None, "changed_fields": []}
        oname = r.get("Order Name", "")
        if oname not in prev_map:
            if prev_map:
                change["type"] = "new"
        else:
            prev = prev_map[oname]
            changed = [c for c in ["Delivery Date", "Qty Ordered", "Price per unit", "Order Status"]
                       if str(r.get(c, "")) != str(prev.get(c, ""))]
            if changed:
                change["type"] = "modified"
                change["changed_fields"] = changed
            del prev_map[oname]
        r["_change"] = change

    deleted = []
    for oname, prev in prev_map.items():
        prev["_change"] = {"type": "deleted", "changed_fields": []}
        deleted.append(prev)

    return {
        "columns": UBLOX_DISPLAY_COLUMNS, "data": records, "deleted": deleted,
        "total_rows": len(records), "has_prev": prev_version is not None and prev_version != "0",
        "version": int(new_version), "upload_date": str(datetime.now().date()),
    }


@app.get("/api/ublox/data")
async def get_ublox_data():
    all_items = ublox_tb.scan_all()
    if not all_items:
        return {"data": [], "columns": UBLOX_DISPLAY_COLUMNS, "total_rows": 0}

    version_nums = sorted(set(int(item.get("upload_version", "0")) for item in all_items))
    latest = str(version_nums[-1])
    prev = str(version_nums[-2]) if len(version_nums) > 1 else None

    latest_items = [json.loads(i.get("data", "{}")) for i in all_items if i.get("upload_version") == latest]
    prev_map = {}
    if prev:
        for i in all_items:
            if i.get("upload_version") == prev:
                d = json.loads(i.get("data", "{}"))
                prev_map[d.get("Order Name", "")] = d

    deleted = []
    for r in latest_items:
        change = {"type": None, "changed_fields": []}
        oname = r.get("Order Name", "")
        if prev_map:
            if oname not in prev_map:
                change["type"] = "new"
            else:
                p = prev_map[oname]
                changed = [c for c in ["Delivery Date", "Qty Ordered", "Price per unit", "Order Status"]
                           if str(r.get(c, "")) != str(p.get(c, ""))]
                if changed:
                    change["type"] = "modified"
                    change["changed_fields"] = changed
                del prev_map[oname]
        r["_change"] = change

    for oname, p in prev_map.items():
        p["_change"] = {"type": "deleted", "changed_fields": []}
        deleted.append(p)

    return {
        "columns": UBLOX_DISPLAY_COLUMNS, "data": latest_items, "deleted": deleted,
        "total_rows": len(latest_items), "upload_date": str(datetime.now().date()),
        "version": int(latest), "has_prev": prev is not None,
    }


@app.get("/api/ublox/search/{type_number}")
async def search_ublox(type_number: str):
    all_items = ublox_tb.scan_all()
    versions = sorted(set(i.get("upload_version", "0") for i in all_items))
    if not versions:
        return {"data": [], "summary": None}
    latest = versions[-1]
    records = []
    for i in all_items:
        if i.get("upload_version") != latest:
            continue
        d = json.loads(i.get("data", "{}"))
        if type_number.lower() in str(d.get("Type Number", "")).lower():
            records.append(d)

    total_qty = sum(to_float(r.get("Qty Ordered")) or 0 for r in records)
    total_value = sum(to_float(r.get("Total Value")) or 0 for r in records)
    customers = list(set(r.get("End Customer") for r in records if r.get("End Customer")))

    return {
        "columns": UBLOX_DISPLAY_COLUMNS, "data": records, "total_rows": len(records),
        "summary": {"type_number": type_number, "total_qty": total_qty,
                     "total_value": total_value, "order_count": len(records), "customers": customers},
    }


@app.delete("/api/ublox/data")
async def reset_ublox():
    ublox_tb.delete_all()
    return {"deleted": "all"}


# ==================== 영업실적 ====================

FIXED_EXCHANGE_RATE = 1400
SALES_COLUMNS = [
    "구분", "MPN", "QTY", "DCPL($)", "매입금액($)", "SP($)", "매출금액($)",
    "매출환율", "SP(KRW)", "매출금액(KRW)", "GP($)", "GP%($)", "GP(KRW)", "GP%(KRW)",
    "담당자", "납품처", "거래처코드", "출고일자", "입고일", "Month",
]


def parse_remark(remark):
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
    if not lot_no or str(lot_no) == "nan":
        return None
    parts = str(lot_no).split("_")
    if not parts:
        return None
    ds = parts[0].strip()
    if len(ds) == 6 and ds.isdigit():
        return f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"
    return None


@app.post("/api/sales/upload")
async def upload_sales(file: UploadFile = File(...)):
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents), header=0)
    except Exception:
        df = pd.read_excel(io.BytesIO(contents), header=0, engine="xlrd")

    records = []
    for _, row in df.iterrows():
        qty = to_float(row.get("출고수량"))
        if not qty or qty == 0:
            continue
        dcpl, sp = parse_remark(row.get("비고(내역)"))
        inbound_date = parse_lot_date(row.get("LOT No."))
        foreign_price = to_float(row.get("외화단가"))
        if foreign_price and foreign_price > 0:
            sp = foreign_price
        exch_rate = to_float(row.get("환율"))
        if not exch_rate or exch_rate <= 1:
            exch_rate = FIXED_EXCHANGE_RATE

        buy_amt = round(dcpl * qty, 2) if dcpl else None
        sell_amt = round(sp * qty, 2) if sp else None
        sp_krw = round(sp * exch_rate, 2) if sp else None
        sell_amt_krw = round(sell_amt * exch_rate, 2) if sell_amt else None
        gp_usd = round(sell_amt - buy_amt, 2) if sell_amt and buy_amt else None
        gp_pct = round(gp_usd / sell_amt * 100, 2) if gp_usd and sell_amt else None
        gp_krw = round(gp_usd * exch_rate, 2) if gp_usd else None
        gp_pct_krw = round(gp_krw / sell_amt_krw * 100, 2) if gp_krw and sell_amt_krw else None

        ship_date = row.get("출고일자")
        ship_date = ship_date.strftime("%Y-%m-%d") if isinstance(ship_date, pd.Timestamp) else safe_str(ship_date)
        month_val = safe_str(row.get("출고년월"))
        if month_val:
            month_val = month_val.replace("/", "")
        vendor = safe_str(row.get("품목군")) or ""

        records.append({
            "구분": vendor, "MPN": safe_str(row.get("품번")) or "",
            "QTY": qty, "DCPL($)": dcpl, "매입금액($)": buy_amt,
            "SP($)": sp, "매출금액($)": sell_amt, "매출환율": exch_rate,
            "SP(KRW)": sp_krw, "매출금액(KRW)": sell_amt_krw,
            "GP($)": gp_usd, "GP%($)": gp_pct, "GP(KRW)": gp_krw, "GP%(KRW)": gp_pct_krw,
            "담당자": safe_str(row.get("담당자")) or "", "납품처": safe_str(row.get("고객")) or "",
            "거래처코드": safe_str(row.get("고객코드")) or "", "출고일자": ship_date,
            "입고일": inbound_date, "Month": month_val,
        })

    # DB 저장
    sales_tb.delete_all()
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i, r in enumerate(records):
        sales_tb.put({
            "batch_id": batch_id, "item_id": str(i),
            "data": json.dumps(r, ensure_ascii=False, default=str),
        })

    total_sales = sum(r.get("매출금액($)") or 0 for r in records)
    total_buy = sum(r.get("매입금액($)") or 0 for r in records)
    total_gp = sum(r.get("GP($)") or 0 for r in records)
    total_sales_krw = sum(r.get("매출금액(KRW)") or 0 for r in records)
    total_gp_krw = sum(r.get("GP(KRW)") or 0 for r in records)

    return {
        "columns": SALES_COLUMNS, "data": records, "total_rows": len(records),
        "summary": {
            "total_sales_usd": round(total_sales, 2), "total_buy_usd": round(total_buy, 2),
            "total_gp_usd": round(total_gp, 2),
            "total_gp_pct": round(total_gp / total_sales * 100, 2) if total_sales else 0,
            "total_sales_krw": round(total_sales_krw, 2), "total_gp_krw": round(total_gp_krw, 2),
            "total_gp_pct_krw": round(total_gp_krw / total_sales_krw * 100, 2) if total_sales_krw else 0,
        }, "saved_to_db": True,
    }


@app.get("/api/sales/data")
async def get_sales_data():
    items = sales_tb.scan_all()
    if not items:
        return {"data": [], "columns": SALES_COLUMNS, "total_rows": 0}
    records = [json.loads(i.get("data", "{}")) for i in items]
    total_sales = sum(r.get("매출금액($)") or 0 for r in records)
    total_buy = sum(r.get("매입금액($)") or 0 for r in records)
    total_gp = sum(r.get("GP($)") or 0 for r in records)
    total_sales_krw = sum(r.get("매출금액(KRW)") or 0 for r in records)
    total_gp_krw = sum(r.get("GP(KRW)") or 0 for r in records)
    return {
        "columns": SALES_COLUMNS, "data": records, "total_rows": len(records),
        "summary": {
            "total_sales_usd": round(total_sales, 2), "total_buy_usd": round(total_buy, 2),
            "total_gp_usd": round(total_gp, 2),
            "total_gp_pct": round(total_gp / total_sales * 100, 2) if total_sales else 0,
            "total_sales_krw": round(total_sales_krw, 2), "total_gp_krw": round(total_gp_krw, 2),
            "total_gp_pct_krw": round(total_gp_krw / total_sales_krw * 100, 2) if total_sales_krw else 0,
        },
    }


# ==================== 마이크론 재고 ====================

MICRON_COLUMNS = [
    "Status", "Type", "PO", "DID", "MPN", "CPN (MOBIS ID 포함)", "BOX_TYPE",
    "QTY", "DNNo.", "Ship Date", "MicronInvoice#", "수입면장번호", "BL번호",
    "수입신고일", "FSE", "End customer", "Date Code",
    "Booking Customer & FSE", "Qty_booking", "비고",
]

def _load_micron_all():
    items = micron_tb.scan_all()
    out = []
    for it in items:
        try:
            d = json.loads(it.get("data", "{}"))
        except Exception:
            continue
        # 정렬용 idx 보존
        try:
            d["_idx"] = int(it.get("idx", 0))
        except Exception:
            d["_idx"] = 0
        out.append(d)
    out.sort(key=lambda r: r.get("_idx", 0))
    return out


@app.post("/api/micron/upload")
async def upload_micron(file: UploadFile = File(...)):
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

    # 기존 DB 비우고 새로 저장
    micron_tb.delete_all()
    for idx, rec in enumerate(records):
        micron_tb.put({
            "item_id": str(rec["_id"]),
            "idx": idx,
            "data": json.dumps(rec, ensure_ascii=False, default=str),
        })

    return {"columns": MICRON_COLUMNS, "data": records, "total_rows": len(records)}


@app.get("/api/micron/data")
async def get_micron_data(status: str = "", did: str = "", mpn: str = "", notes_only: str = ""):
    all_items = _load_micron_all()
    filtered = all_items
    if status:
        filtered = [r for r in filtered if status in str(r.get("Status", ""))]
    if did:
        filtered = [r for r in filtered if did.lower() in str(r.get("DID", "")).lower()]
    if mpn:
        filtered = [r for r in filtered if mpn.lower() in str(r.get("MPN", "")).lower()]
    if notes_only == "true":
        filtered = [r for r in filtered if r.get("비고") and str(r.get("비고")) not in ("None", "", "nan")]
    return {"columns": MICRON_COLUMNS, "data": filtered, "total_rows": len(filtered)}


@app.get("/api/micron/summary/{did}")
async def micron_summary(did: str):
    all_items = _load_micron_all()
    items = [r for r in all_items if str(r.get("DID", "")).upper() == did.upper()]
    if not items:
        return {"error": "해당 DID 없음"}
    by_status = {}
    for r in items:
        s = str(r.get("Status", "기타"))
        if s not in by_status:
            by_status[s] = {"count": 0, "qty": 0}
        by_status[s]["count"] += 1
        by_status[s]["qty"] += to_float(r.get("QTY")) or 0
    total_qty = sum(v["qty"] for v in by_status.values())
    mpns = list(set(str(r.get("MPN", "")) for r in items))
    return {"did": did, "mpns": mpns, "total_qty": total_qty, "by_status": by_status, "items": items}


MICRON_EDITABLE = ("Status", "Booking Customer & FSE", "Qty_booking", "비고", "수입면장번호", "BL번호")


@app.post("/api/micron/update")
async def update_micron(request: Request):
    data = await request.json()
    item_id = data.get("_id")
    if item_id is None:
        return {"error": "ID 없음"}

    existing = micron_tb.table.get_item(Key={"item_id": str(item_id)}).get("Item")
    if not existing:
        return {"error": "not found"}
    try:
        rec = json.loads(existing.get("data", "{}"))
    except Exception:
        rec = {}

    for fld in MICRON_EDITABLE:
        if fld in data:
            rec[fld] = data[fld]

    micron_tb.put({
        "item_id": str(item_id),
        "idx": existing.get("idx", 0),
        "data": json.dumps(rec, ensure_ascii=False, default=str),
    })
    return {"updated": item_id}


@app.delete("/api/micron/data")
async def reset_micron():
    micron_tb.delete_all()
    return {"deleted": "all"}


# ==================== 1실 CRD 신호등 보드 ====================
# Backlog Shipment Report(오픈 주문, 행마다 CRD+MAD) 업로드 → MAD vs CRD 위험판정.
# 재고/영업실적/DynamoDB 조인 불필요 — 파일 하나로 끝(stateless).
# 순수 로직은 crd_board.py (단위테스트 test_crd_board.py).

def _bl_clean(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    s = str(v).strip()
    return s or None


def _parse_backlog_orders(contents):
    """Backlog Shipment Report 파싱. 필수 컬럼이 있는 시트를 자동 탐지(시트명 제각각 대응).
    반환: 주문 dict 리스트, 백로그 형식이 아니면 None."""
    try:
        xls = pd.ExcelFile(io.BytesIO(contents))
    except Exception:
        return None
    req = {"MPN", "DID", "CRD", "MAD", "QTY"}
    df = None
    for s in xls.sheet_names:
        d = pd.read_excel(xls, sheet_name=s, header=0)
        if req.issubset(set(d.columns)):
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
        orders.append({
            "so": _bl_clean(row.get("SO")),
            "did": did, "mpn": mpn,
            "customer": _bl_clean(row.get("End customer")),
            "qty": to_float(row.get("QTY")) or 0,
            "crd": _parse_date(row.get("CRD")),
            "mad": _parse_date(row.get("MAD")),
            "order_type": _bl_clean(row.get("ORDER_TYPE")),
            "fse": _bl_clean(row.get("FSE")),
            "open": _bl_clean(row.get("DELIVERY_NUMBER")) is None,
        })
    return orders


def _bl_ser_card(c):
    c = dict(c)
    for k in ("crd", "mad", "prev_mad"):
        if k in c and hasattr(c[k], "isoformat"):
            c[k] = c[k].isoformat()
    return c


@app.post("/api/crd-board")
async def crd_board_compute(file: UploadFile = File(...), buffer_days: int = 7):
    orders_all = _parse_backlog_orders(await file.read())
    if orders_all is None:
        return {"error": "Backlog Shipment Report 형식이 아닙니다 (MPN·DID·CRD·MAD·QTY 컬럼 필요)."}

    open_orders = [o for o in orders_all if o["open"]]
    shipped = len(orders_all) - len(open_orders)
    type_counts = {}
    for o in open_orders:
        type_counts[o["order_type"]] = type_counts.get(o["order_type"], 0) + 1

    today = datetime.now().date()
    cards = crd_board.classify_backlog(open_orders, today=today, buffer_days=buffer_days)

    board, summary = [], {"red": 0, "yellow": 0, "green": 0, "unknown": 0}
    for c in cards:
        board.append(_bl_ser_card(c))
        summary[c["risk"]] = summary.get(c["risk"], 0) + 1

    return {
        "board": board, "summary": summary,
        "part_summary": crd_board.summarize_by_part(cards),
        "open_count": len(open_orders), "shipped_skipped": shipped,
        "order_types": type_counts, "buffer_days": buffer_days,
        "today": today.isoformat(),
    }


@app.post("/api/crd-board/compare")
async def crd_board_compare(prev: UploadFile = File(...), current: UploadFile = File(...)):
    """이전·현재 백로그 두 파일 비교 → MAD 밀린(선적 지연) 주문 + 주간 움직임 요약."""
    prev_all = _parse_backlog_orders(await prev.read())
    cur_all = _parse_backlog_orders(await current.read())
    if prev_all is None or cur_all is None:
        return {"error": "두 파일 모두 Backlog Shipment Report 형식이어야 합니다."}

    prev_open = [o for o in prev_all if o["open"]]
    cur_open = [o for o in cur_all if o["open"]]
    today = datetime.now().date()
    res = crd_board.compare_backlog(prev_open, cur_open, today=today)

    return {
        "slipped": [_bl_ser_card(c) for c in res["slipped"]],
        "new": [_bl_ser_card(c) for c in res["new"]],
        "gone_count": res["gone_count"],
        "summary": res["summary"],
        "today": today.isoformat(),
    }


# ==================== AUO 백로그 ====================

AUO_STAGE_LABELS = {
    0: "발주 대기",
    1: "발주 완료",
    2: "유니트론 입고 단계",
    3: "고객 납품 단계",
    4: "계산서 발행 완료",
}

AUO_FIELDS = [
    "unitron_po_date", "unitron_po_no", "crd",
    "part_name", "auo_pn", "qty", "customer", "note",
    "au_ship_date", "au_invoice_no", "bl_no", "bl_date",
    "payment_date", "import_date", "import_no",
    "delivery_date", "tax_invoice_date",
    "unit_price", "amount",
]

AUO_COL_MAP = {
    15: "unitron_po_date",
    16: "unitron_po_no",
    38: "crd",
    17: "part_name",
    18: "auo_pn",
    19: "qty",
    12: "customer",
    13: "note",
    30: "au_ship_date",
    31: "au_invoice_no",
    33: "bl_no",
    32: "bl_date",
    34: "payment_date",
    28: "import_date",
    29: "import_no",
    10: "delivery_date",
    11: "tax_invoice_date",
    20: "unit_price",
    27: "amount",
}


def _fmt_date(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _auo_classify(rec):
    """5단계 판정"""
    if rec.get("tax_invoice_date"):
        return 4
    if rec.get("import_date"):
        return 3
    if rec.get("au_ship_date"):
        return 2
    if rec.get("unitron_po_date") or rec.get("unitron_po_no"):
        return 1
    return 0


def _auo_natural_key(rec):
    """dedup key"""
    inv = rec.get("au_invoice_no")
    pn = rec.get("auo_pn") or ""
    if inv:
        return f"inv:{inv}#{pn}"
    po = rec.get("unitron_po_no") or ""
    pod = rec.get("unitron_po_date") or ""
    qty = rec.get("qty") or ""
    return f"po:{po}#{pn}#{pod}#{qty}"


def _open_excel_any(contents: bytes):
    """업로드 엑셀을 형식 무관하게 연다.
    암호 파일이면 먼저 복호화 → .xlsx(openpyxl) → 구버전 .xls(xlrd) 순서로 시도.
    복호화/열기 실패하면 사용자용 ValueError 발생."""
    contents = _fs_decrypt(contents)  # 암호 걸린 파일이면 복호화(실패 시 ValueError)
    for eng in ("openpyxl", "xlrd"):
        try:
            return pd.ExcelFile(io.BytesIO(contents), engine=eng)
        except Exception:
            continue
    raise ValueError(
        "엑셀 파일을 열 수 없습니다. 유효한 .xlsx 파일이 아닐 수 있습니다 "
        "(구버전 .xls·손상·다른 형식). 엑셀에서 '다른 이름으로 저장 → Excel 통합 문서(*.xlsx)'로 "
        "저장한 뒤 다시 올려주세요."
    )


@app.post("/api/auo/upload")
async def upload_auo(file: UploadFile = File(...)):
    contents = await file.read()
    try:
        xls = _open_excel_any(contents)
    except ValueError as e:
        return {"error": str(e)}

    target = None
    for name in xls.sheet_names:
        if name.upper() == "AUO":
            target = name
            break
    if not target:
        return {"error": "AUO 시트를 찾을 수 없습니다."}

    df = pd.read_excel(xls, sheet_name=target, header=None)

    # 헤더 행 찾기 (Unitron PO# 컬럼에 "Unitron PO#" 있는 행)
    header_row = None
    for i in range(min(15, len(df))):
        row_strs = [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]
        if any("Unitron PO" in s for s in row_strs):
            header_row = i
            break
    if header_row is None:
        return {"error": "헤더 행을 찾을 수 없습니다."}

    records = []
    for _, row in df.iloc[header_row + 1:].iterrows():
        rec = {}
        has = False
        for col_idx, field in AUO_COL_MAP.items():
            if col_idx >= len(row):
                continue
            v = row.iloc[col_idx]
            if isinstance(v, pd.Timestamp) or "date" in field:
                rec[field] = _fmt_date(v)
            elif field in ("qty", "unit_price", "amount"):
                rec[field] = to_float(v)
            else:
                rec[field] = safe_str(v)
            if rec.get(field) not in (None, "", 0):
                has = True
        if not has:
            continue
        # 최소 식별 가능 정보
        if not (rec.get("unitron_po_no") or rec.get("au_invoice_no") or rec.get("auo_pn")):
            continue
        rec["stage"] = _auo_classify(rec)
        rec["natural_key"] = _auo_natural_key(rec)
        records.append(rec)

    # 기존 데이터 로드 (natural_key 기준 매핑)
    existing = auo_tb.scan_all()
    by_key = {}
    for item in existing:
        try:
            data = json.loads(item.get("data", "{}"))
        except Exception:
            continue
        nk = data.get("natural_key") or item.get("natural_key")
        if nk:
            by_key[nk] = item.get("row_id")

    inserted = 0
    updated = 0
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    for r in records:
        nk = r["natural_key"]
        existing_id = by_key.get(nk)
        row_id = existing_id or f"{batch_id}_{uuid.uuid4().hex[:8]}"
        r["row_id"] = row_id
        auo_tb.put({
            "bucket": "auo",
            "row_id": row_id,
            "stage": str(r["stage"]),
            "natural_key": nk,
            "data": json.dumps(r, ensure_ascii=False, default=str),
        })
        if existing_id:
            updated += 1
        else:
            inserted += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "total_rows": len(records),
        "stage_labels": AUO_STAGE_LABELS,
    }


@app.get("/api/auo/data")
async def get_auo():
    items = auo_tb.scan_all()
    rows = []
    for item in items:
        try:
            data = json.loads(item.get("data", "{}"))
        except Exception:
            continue
        data["row_id"] = item.get("row_id")
        data["stage"] = int(item.get("stage", data.get("stage", 0)))
        rows.append(data)

    # 단계별 그룹핑
    by_stage = {s: [] for s in range(5)}
    for r in rows:
        by_stage[r.get("stage", 0)].append(r)

    # 각 단계 내에서 PO 날짜/ship 날짜/invoice 날짜 역순
    for s, lst in by_stage.items():
        lst.sort(key=lambda r: (
            r.get("tax_invoice_date") or r.get("delivery_date")
            or r.get("import_date") or r.get("au_ship_date")
            or r.get("unitron_po_date") or ""
        ), reverse=True)

    return {
        "stages": [
            {
                "id": s,
                "label": AUO_STAGE_LABELS[s],
                "count": len(by_stage[s]),
                "qty": sum(to_float(r.get("qty")) or 0 for r in by_stage[s]),
                "amount": sum(to_float(r.get("amount")) or 0 for r in by_stage[s]),
                "rows": by_stage[s],
            }
            for s in range(5)
        ],
        "total_rows": len(rows),
    }


@app.post("/api/auo/manual-add")
async def auo_manual_add(request: Request):
    """발주 대기(단계 0) 수동 입력"""
    data = await request.json()
    rec = {f: data.get(f) for f in AUO_FIELDS}
    rec["stage"] = 0
    rec["natural_key"] = _auo_natural_key(rec) + "#manual"
    row_id = f"manual_{uuid.uuid4().hex[:8]}"
    rec["row_id"] = row_id
    auo_tb.put({
        "bucket": "auo",
        "row_id": row_id,
        "stage": "0",
        "natural_key": rec["natural_key"],
        "data": json.dumps(rec, ensure_ascii=False, default=str),
    })
    return {"row_id": row_id, "stage": 0}


@app.post("/api/auo/update")
async def auo_update(request: Request):
    """한 행의 필드 수정 (단계 자동 재판정)"""
    data = await request.json()
    row_id = data.get("row_id")
    if not row_id:
        return {"error": "row_id 필요"}
    existing = auo_tb.table.get_item(Key={"bucket": "auo", "row_id": row_id}).get("Item")
    if not existing:
        return {"error": "not found"}
    try:
        rec = json.loads(existing.get("data", "{}"))
    except Exception:
        rec = {}
    for f in AUO_FIELDS:
        if f in data:
            rec[f] = data[f]
    rec["stage"] = _auo_classify(rec)
    rec["row_id"] = row_id
    auo_tb.put({
        "bucket": "auo",
        "row_id": row_id,
        "stage": str(rec["stage"]),
        "natural_key": rec.get("natural_key") or _auo_natural_key(rec),
        "data": json.dumps(rec, ensure_ascii=False, default=str),
    })
    return {"row_id": row_id, "stage": rec["stage"]}


@app.delete("/api/auo/row/{row_id}")
async def auo_delete_row(row_id: str):
    auo_tb.table.delete_item(Key={"bucket": "auo", "row_id": row_id})
    return {"deleted": row_id}


@app.delete("/api/auo/data")
async def auo_reset():
    auo_tb.delete_all()
    return {"deleted": "all"}


# ==================== 거래명세서 일괄 생성 ====================

import requests as http_requests2


def _fetch_koreaexim_rate(yyyymmdd: str):
    """수출입은행 매매기준율(deal_bas_r) 조회. 영업일이 아니면 None."""
    authkey = os.environ.get("KOREAEXIM_AUTHKEY")
    if not authkey:
        return None
    try:
        r = http_requests2.get(
            "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON",
            params={"authkey": authkey, "searchdate": yyyymmdd, "data": "AP01"},
            timeout=4, verify=False,
        )
        d = r.json()
        if not isinstance(d, list) or not d:
            return None
        # result=1 인 행만 유효 데이터
        usd = [x for x in d if x.get("result") == 1 and (x.get("cur_unit") or "").strip() == "USD"]
        if not usd:
            return None
        rate_str = (usd[0].get("deal_bas_r") or "").replace(",", "")
        return round(float(rate_str), 2) if rate_str else None
    except Exception:
        return None


# 환율 외부조회 서킷브레이커: 한 번 실패하면 이 시각까지 외부조회 건너뛰고 즉시 폴백
_RATE_FALLBACK = 1400
_rate_ext_down_until = 0.0


def _fetch_historical_rate(date_str: str, cache: dict):
    """출고일자 기준 USD→KRW 매매기준율 조회 (날짜당 최대 ~5초, 실패 시 즉시 폴백).
    1순위: 수출입은행 → 2순위: frankfurter → 3순위: open.er-api → 폴백 1400
    외부조회가 한 번 실패하면 10분간 건너뛰어(서킷 오픈) 무한 대기를 막는다.
    """
    global _rate_ext_down_until
    if not date_str:
        return None
    if date_str in cache:
        return cache[date_str]

    # 서킷 오픈 상태면 기다리지 말고 즉시 폴백
    if _time.time() < _rate_ext_down_until:
        cache[date_str] = _RATE_FALLBACK
        return _RATE_FALLBACK

    deadline = _time.time() + 5  # 날짜당 외부조회 총 예산 5초
    rate = None

    # 1. 수출입은행 매매기준율 (최대 3일 소급)
    try:
        d = pd.Timestamp(date_str)
        for back in range(3):
            if _time.time() > deadline:
                break
            yyyymmdd = (d - pd.Timedelta(days=back)).strftime("%Y%m%d")
            rate = _fetch_koreaexim_rate(yyyymmdd)
            if rate:
                break
    except Exception:
        rate = None

    # 2. frankfurter 폴백
    if not rate and _time.time() < deadline:
        try:
            r = http_requests2.get(
                f"https://api.frankfurter.app/{date_str}?from=USD&to=KRW", timeout=3
            )
            rate = round(float(r.json()["rates"]["KRW"]), 2)
        except Exception:
            pass

    # 3. 실시간 환율 폴백
    if not rate and _time.time() < deadline:
        try:
            r = http_requests2.get("https://open.er-api.com/v6/latest/USD", timeout=3)
            rate = round(float(r.json()["rates"]["KRW"]), 2)
        except Exception:
            pass

    if not rate:
        # 외부조회 전부 실패 → 10분간 서킷 오픈
        rate = _RATE_FALLBACK
        _rate_ext_down_until = _time.time() + 600

    cache[date_str] = rate
    return rate


@app.post("/api/invoice-batch/preview")
async def invoice_batch_preview(file: UploadFile = File(...)):
    """출고기안 업로드 → 고객별 그룹핑 + 환율 적용. 블로킹 작업은 스레드풀에서 실행해 이벤트 루프를 막지 않는다."""
    from starlette.concurrency import run_in_threadpool
    contents = await file.read()
    return await run_in_threadpool(_invoice_batch_compute, contents)


def _invoice_batch_compute(contents: bytes):
    xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")

    target = None
    for s in xls.sheet_names:
        if "출고기안" in s:
            target = s
            break
    if not target:
        target = xls.sheet_names[0]

    df = pd.read_excel(xls, sheet_name=target, header=None)

    header_row = None
    for i in range(min(10, len(df))):
        vals = [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]
        if "고객" in vals and "품번" in vals:
            header_row = i
            break
    if header_row is None:
        return {"error": "헤더 행(고객/품번)을 찾을 수 없습니다."}

    raw_headers = [str(v).strip() if pd.notna(v) else f"_c{i}" for i, v in enumerate(df.iloc[header_row].values)]
    data_df = df.iloc[header_row + 1:].reset_index(drop=True).dropna(how="all")
    data_df.columns = raw_headers[:len(data_df.columns)]

    # 출고일자별 환율 캐시
    rate_cache = {}

    # 고객별 그룹핑
    groups = {}
    for _, row in data_df.iterrows():
        customer = str(row.get("고객", "") or "").strip()
        if not customer or customer.lower() == "nan":
            continue

        date = row.get("출고일자")
        date_str = ""
        if date is not None and str(date) != "nan":
            try:
                date_str = pd.to_datetime(date).strftime("%Y-%m-%d")
            except Exception:
                date_str = str(date)[:10]

        qty = to_float(row.get("수량")) or 0
        if qty == 0:
            continue
        price_usd = to_float(row.get("매출가")) or 0
        part = str(row.get("품번", "") or "").strip()

        # 출고일자 환율 조회
        rate = _fetch_historical_rate(date_str, rate_cache) or 1400

        if customer not in groups:
            groups[customer] = {
                "customer": customer,
                "items": [],
                "earliest_date": None,
                "담당자": str(row.get("담당자", "") or "").strip() or None,
            }

        groups[customer]["items"].append({
            "date": date_str,
            "part": part,
            "qty": qty,
            "price": price_usd,
            "rate": rate,
        })
        if not groups[customer]["earliest_date"] or (date_str and date_str < groups[customer]["earliest_date"]):
            groups[customer]["earliest_date"] = date_str

    # 각 그룹 합계 계산 (아이템별 환율 적용)
    for g in groups.values():
        total_usd = 0
        total_krw = 0
        for it in g["items"]:
            r = it["rate"]
            it["amount_usd"] = round(it["price"] * it["qty"], 2)
            it["price_krw"] = round(it["price"] * r, 2)
            it["amount_krw"] = round(it["amount_usd"] * r, 0)
            total_usd += it["amount_usd"]
            total_krw += it["amount_krw"]
        g["total_usd"] = round(total_usd, 2)
        g["total_krw"] = round(total_krw, 0)
        # 대표 환율: 가장 큰 출고 금액의 rate. 참고용.
        if g["items"]:
            g["rate"] = max(g["items"], key=lambda it: it["amount_usd"])["rate"]
        g["item_count"] = len(g["items"])

    return {
        "rate_source": "frankfurter.app (historical)",
        "rate_cache": rate_cache,
        "invoices": list(groups.values()),
        "customer_count": len(groups),
    }


@app.get("/api/auo/export")
async def auo_export():
    """AUO 백로그 전체를 원본 엑셀 형식으로 내보내기"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    rows = _auo_rows()
    rows.sort(key=lambda r: r.get("unitron_po_date") or "", reverse=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "AUO"

    # 원본 포맷 헤더 (컬럼 인덱스 = 엑셀 col 번호 0-기반)
    headers = {
        2: "AU Shipping / Invoice Date",
        3: "AU Invoice No.",
        6: "Tracking#\n(B/L NO.)",
        10: "Delivery Date \n(To Customer)",
        11: "계산서 Date",
        12: "UT to Customer\n고객사 창고",
        13: "참고1",
        15: "Unitron PO Date",
        16: "Unitron PO#",
        17: "Part Name",
        18: "( AUO P/N )",
        19: "Q'ty",
        20: "Net U/P ($)\n실제",
        27: "Net AMT ($)",
        28: "수입\n신고일/입고일",
        29: "수입신고번호",
        30: "AU Shipping / Invoice Date",
        31: "AU Invoice No.",
        32: "(B/L Date.)",
        33: "Tracking#\n(B/L NO.)",
        34: "T/T 결제일\nL/C OPEN일",
        38: "CRD\n(AUO발주서)",
    }

    field_map = {
        2: "au_ship_date",
        3: "au_invoice_no",
        6: "bl_no",
        10: "delivery_date",
        11: "tax_invoice_date",
        12: "customer",
        13: "note",
        15: "unitron_po_date",
        16: "unitron_po_no",
        17: "part_name",
        18: "auo_pn",
        19: "qty",
        20: "unit_price",
        27: "amount",
        28: "import_date",
        29: "import_no",
        30: "au_ship_date",
        31: "au_invoice_no",
        32: "bl_date",
        33: "bl_no",
        34: "payment_date",
        38: "crd",
    }

    header_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
    bold = Font(bold=True, size=10)
    wrap_center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 헤더 행: 원본과 동일하게 7행 (1-indexed)
    header_row = 7
    for col_idx, label in headers.items():
        cell = ws.cell(row=header_row, column=col_idx + 1)
        cell.value = label
        cell.font = bold
        cell.fill = header_fill
        cell.alignment = wrap_center
    ws.row_dimensions[header_row].height = 36

    # 데이터 행
    for i, r in enumerate(rows):
        xr = header_row + 1 + i
        for col_idx, field in field_map.items():
            val = r.get(field)
            if val in (None, "", "nan"):
                continue
            cell = ws.cell(row=xr, column=col_idx + 1)
            if field in ("qty",):
                try:
                    cell.value = float(val)
                    cell.number_format = "#,##0"
                except Exception:
                    cell.value = val
            elif field in ("unit_price", "amount"):
                try:
                    cell.value = float(val)
                    cell.number_format = "$#,##0.00"
                except Exception:
                    cell.value = val
            else:
                cell.value = val

    # 열 너비 (53개 칼럼)
    for col in range(1, 54):
        ws.column_dimensions[get_column_letter(col)].width = 14

    # 1~6행은 원본에 메모가 있던 영역 — 비워두고 freeze만 적용
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    fname = f"AUO_Backlog_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ==================== AUO 발주요청서 ====================

def _auo_rows():
    items = auo_tb.scan_all()
    out = []
    for it in items:
        try:
            d = json.loads(it.get("data", "{}"))
        except Exception:
            continue
        d["stage"] = int(it.get("stage", d.get("stage", 0)))
        out.append(d)
    return out


def _month_key(date_str):
    if not date_str:
        return None
    try:
        d = pd.Timestamp(date_str)
        return f"{d.year:04d}-{d.month:02d}"
    except Exception:
        return None


def _compute_po_preview(mpn: str, customer: str, today: pd.Timestamp):
    rows = _auo_rows()

    def match_mpn(r):
        q = mpn.strip().upper()
        return ((r.get("auo_pn") or "").strip().upper() == q or
                (r.get("part_name") or "").strip().upper() == q)

    def match_cust(r):
        rc = (r.get("customer") or "").strip()
        cq = customer.strip()
        if not rc or not cq:
            return False
        return rc == cq or cq in rc or rc in cq

    mpn_rows = [r for r in rows if match_mpn(r)]
    mpn_cust_rows = [r for r in mpn_rows if match_cust(r)]

    # Inventory Total: 단계 2+3 (AUO 선적 후 ~ 계산서 전)
    inv_qty = sum(to_float(r.get("qty")) or 0 for r in mpn_rows if r.get("stage") in (2, 3))

    # Customer PO Balance: 고객의 오픈 PO = MPN+고객, 단계 1~3 합계
    cpo_balance = sum(to_float(r.get("qty")) or 0 for r in mpn_cust_rows if r.get("stage") in (1, 2, 3))

    # Backlog Total: 단계 1만 (아직 AUO 출하 전)
    bl_qty = sum(to_float(r.get("qty")) or 0 for r in mpn_cust_rows if r.get("stage") == 1)

    # B/Price: 최근 unit_price (단계 무관, 최근 au_ship_date/po_date 기준)
    priced = [r for r in mpn_rows if to_float(r.get("unit_price"))]
    priced.sort(key=lambda r: r.get("au_ship_date") or r.get("unitron_po_date") or "", reverse=True)
    b_price = to_float(priced[0].get("unit_price")) if priced else 0

    # 월별 실제값 (과거/현재월용)
    # - 매입(actual_buy): import_date 월별 합계 = 유니트론이 실제 받은 수량
    # - 매출(actual_sell): tax_invoice_date 월별 합계 = 실제 매출 발생
    # - 매출계획 실제(actual_plan_sell): 매출 발생액을 매출계획 컬럼으로 대체
    # - 발주(actual_po_req): unitron_po_date 월별 합계 = 실제 AUO에 요청한 수량
    actual_buy = {}
    actual_sell = {}
    actual_po_req = {}
    for r in mpn_cust_rows:
        q = to_float(r.get("qty")) or 0
        imp_m = _month_key(r.get("import_date"))
        if imp_m:
            actual_buy[imp_m] = actual_buy.get(imp_m, 0) + q
        inv_m = _month_key(r.get("tax_invoice_date"))
        if inv_m:
            actual_sell[inv_m] = actual_sell.get(inv_m, 0) + q
        po_m = _month_key(r.get("unitron_po_date"))
        if po_m:
            actual_po_req[po_m] = actual_po_req.get(po_m, 0) + q

    monthly = dict(actual_sell)

    # Avg3M: 최근 3개월 평균
    last3_keys = []
    for i in range(1, 4):
        d = today - pd.DateOffset(months=i)
        last3_keys.append(f"{d.year:04d}-{d.month:02d}")
    avg3m = sum(monthly.get(k, 0) for k in last3_keys) / 3

    # 향후 매출계획: 백로그 납품일 분포 (우선순위: delivery_date > crd)
    future_by_month = {}
    for r in mpn_cust_rows:
        if r.get("stage") not in (1, 2, 3):
            continue
        dd = r.get("delivery_date") or r.get("crd")
        m = _month_key(dd)
        if not m:
            continue
        future_by_month[m] = future_by_month.get(m, 0) + (to_float(r.get("qty")) or 0)

    # 다음 3개월 + +3M (+4~+6월 합)
    # 과거/현재 월은 실제값(actual_sell) 사용, 미래는 backlog 분포 or avg3m
    today_key_local = f"{today.year:04d}-{today.month:02d}"
    plan_months = []
    for i in range(1, 4):
        d = today + pd.DateOffset(months=i)
        key = f"{d.year:04d}-{d.month:02d}"
        label = d.strftime("%b")
        if key <= today_key_local:
            planned = actual_sell.get(key, 0)
        else:
            planned = future_by_month.get(key, 0) or round(avg3m)
        plan_months.append({"key": key, "label": label, "qty": planned})

    plus_3m_qty = 0
    has_plus = False
    for i in range(4, 7):
        d = today + pd.DateOffset(months=i)
        key = f"{d.year:04d}-{d.month:02d}"
        if key in future_by_month:
            plus_3m_qty += future_by_month[key]
            has_plus = True

    # Aging: 단계 2+3 행의 AU 출하일 이후 경과 일수
    aging = {"1M": 0, "2M": 0, "3M_6M": 0}
    for r in mpn_rows:
        if r.get("stage") not in (2, 3):
            continue
        dd = r.get("au_ship_date") or r.get("import_date") or r.get("unitron_po_date")
        if not dd:
            continue
        try:
            days = (today - pd.Timestamp(dd)).days
        except Exception:
            continue
        q = to_float(r.get("qty")) or 0
        if days <= 30:
            aging["1M"] += q
        elif days <= 60:
            aging["2M"] += q
        elif days <= 180:
            aging["3M_6M"] += q

    return {
        "inventory_qty": inv_qty,
        "b_price": b_price,
        "inventory_amt": round(inv_qty * b_price, 2),
        "customer_po_balance": cpo_balance,
        "backlog_qty": bl_qty,
        "avg_3m": round(avg3m),
        "plan_months": plan_months,
        "plus_3m_qty": plus_3m_qty if has_plus else None,
        "aging": aging,
        "monthly_history": monthly,
        "actual_buy_by_month": actual_buy,
        "actual_sell_by_month": actual_sell,
        "actual_po_req_by_month": actual_po_req,
    }


@app.post("/api/po-request/preview")
async def po_request_preview(request: Request):
    data = await request.json()
    mpn = (data.get("mpn") or "").strip()
    customer = (data.get("customer") or "").strip()
    if not mpn or not customer:
        return {"error": "MPN과 고객이 필요합니다."}

    today_str = data.get("today")
    today = pd.Timestamp(today_str) if today_str else pd.Timestamp.today().normalize()

    preview = _compute_po_preview(mpn, customer, today)

    # 표3: MPN 월말 재고 예상
    # 표시할 3개월: 이번달, 다음달, 다다음달
    # 과거/현재 월은 실제 매입/매출 사용, 미래는 예측(Requested / plan_months)
    inv_start = preview["inventory_qty"]
    qty = to_float(data.get("qty")) or 0
    today_key = f"{today.year:04d}-{today.month:02d}"
    actual_buy = preview["actual_buy_by_month"]
    actual_sell = preview["actual_sell_by_month"]

    # Requested PO Q'ty: 과거/현재 월은 실제 발주량(actual_po_req) 사용, 미래는 계산값
    today_key_t = f"{today.year:04d}-{today.month:02d}"
    actual_po_req = preview["actual_po_req_by_month"]
    cur_inv = inv_start
    requested = []
    for pm in preview["plan_months"]:
        plan = pm["qty"]
        key = pm["key"]
        if key <= today_key_t:
            # 실제 이 달에 AUO에 발주한 수량
            need = actual_po_req.get(key, 0)
        else:
            # 미래: 부족분 계산
            need = max(0, plan - cur_inv)
        requested.append(need)
        cur_inv = cur_inv + need - plan

    # 표3는 이번달부터 3개월 (today, +1, +2)
    sim = {
        "start_month_label": f"{(today - pd.DateOffset(months=1)).month}월말",
        "inventory_start": inv_start,
        "months": [],
    }
    cur_inv = inv_start
    plan_map = {pm["key"]: pm["qty"] for pm in preview["plan_months"]}
    req_map = {pm["key"]: requested[i] for i, pm in enumerate(preview["plan_months"])}

    for i in range(3):
        d = today + pd.DateOffset(months=i)
        key = f"{d.year:04d}-{d.month:02d}"
        label = f"{d.month}월"
        is_past_or_current = key <= today_key

        if is_past_or_current:
            # 실제값 사용
            buy = actual_buy.get(key, 0)
            sell = actual_sell.get(key, 0)
            source = "actual"
        else:
            # 예측값 (요청량/매출계획)
            buy = req_map.get(key, 0)
            sell = plan_map.get(key, 0)
            source = "projected"

        end_inv = cur_inv + buy - sell
        sim["months"].append({
            "key": key,
            "label": label,
            "buy": buy,
            "sell": sell,
            "end_inv": end_inv,
            "source": source,
        })
        cur_inv = end_inv

    # 표1: 계산
    resale_price = to_float(data.get("resale_price")) or round(preview["b_price"] * 1.1, 2)
    quote_price = to_float(data.get("quote_price")) or resale_price
    buying_amt = round(preview["b_price"] * qty, 2)
    resale_amt = round(resale_price * qty, 2)
    gp_amt = round(resale_amt - buying_amt, 2)
    gp_pct = round(gp_amt / resale_amt * 100, 2) if resale_amt else 0

    # Customer PO Balance: 사용자가 입력했으면 그 값, 아니면 자동값
    cpo_manual = to_float(data.get("customer_po_balance"))
    cpo_value = cpo_manual if cpo_manual else preview["customer_po_balance"]

    return {
        "input": {
            "date": today.strftime("%Y-%m-%d"),
            "sales": data.get("sales", ""),
            "mpn": mpn,
            "package": data.get("package", "Pallet"),
            "qty": qty,
            "uni_crd": data.get("uni_crd", ""),
            "customer_delivery": data.get("customer_delivery", ""),
            "quote_price": quote_price,
            "resale_price": resale_price,
            "po_customer": data.get("po_customer") or customer,
            "real_end_customer": data.get("real_end_customer") or customer,
            "payment_term": data.get("payment_term", ""),
            "remark": data.get("remark", ""),
            "customer_po_balance": cpo_value,
            "customer": customer,
        },
        "table1": {
            "b_price": preview["b_price"],
            "resale_price": resale_price,
            "buying_amt": buying_amt,
            "resale_amt": resale_amt,
            "gp_amt": gp_amt,
            "gp_pct": gp_pct,
        },
        "table2": {
            "customer_po_balance": cpo_value,
            "inventory_qty": preview["inventory_qty"],
            "b_price": preview["b_price"],
            "inventory_amt": preview["inventory_amt"],
            "plan_months": preview["plan_months"],
            "plus_3m_qty": preview["plus_3m_qty"],
            "aging": preview["aging"],
            "backlog_qty": preview["backlog_qty"],
            "avg_3m": preview["avg_3m"],
            "requested_po_qty": requested,
        },
        "table3": sim,
    }


@app.post("/api/po-request/generate")
async def po_request_generate(request: Request):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    data = await request.json()
    inp = data.get("input", {})
    t1 = data.get("table1", {})
    t2 = data.get("table2", {})
    t3 = data.get("table3", {})

    wb = Workbook()
    ws = wb.active
    ws.title = "발주요청서"

    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    header_fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    sum_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    bold = Font(bold=True)

    def put(cell, val, fill=None, font=None, align=center, bd=border, fmt=None):
        cell.value = val
        if fill: cell.fill = fill
        if font: cell.font = font
        cell.alignment = align
        cell.border = bd
        if fmt: cell.number_format = fmt

    # === 표1: 발주 요청 (17 cols) ===
    t1_headers = [
        "Date", "담당 Sales", "MPN", "Package", "Qty", "UNI CRD", "고객사 납품일",
        "Quote Price", "Resale Price", "Buying AMT", "Resale AMT", "GP AMT", "GPM",
        "PO customer", "Real End Customer", "결재조건", "Remark",
    ]
    for j, h in enumerate(t1_headers):
        put(ws.cell(row=1, column=j+1), h, fill=header_fill, font=bold)
    t1_row = [
        inp.get("date"), inp.get("sales"), inp.get("mpn"), inp.get("package"),
        inp.get("qty"), inp.get("uni_crd"), inp.get("customer_delivery"),
        inp.get("quote_price"), inp.get("resale_price"),
        t1.get("buying_amt"), t1.get("resale_amt"), t1.get("gp_amt"), t1.get("gp_pct"),
        inp.get("po_customer"), inp.get("real_end_customer"),
        inp.get("payment_term"), inp.get("remark"),
    ]
    for j, v in enumerate(t1_row):
        fmt = None
        if j in (7, 8):
            fmt = "$#,##0.00"
        elif j in (9, 10):
            fmt = "$#,##0.00"
        elif j == 11:
            fmt = "$#,##0"
        elif j == 12:
            fmt = "0.00%"
            v = (v or 0) / 100
        elif j == 4:
            fmt = "#,##0"
        put(ws.cell(row=2, column=j+1), v, fmt=fmt)

    # Sum row
    sum_row = 3
    put(ws.cell(row=sum_row, column=2), "Sum", fill=sum_fill, font=bold)
    put(ws.cell(row=sum_row, column=5), inp.get("qty"), fill=sum_fill, font=bold, fmt="#,##0")
    put(ws.cell(row=sum_row, column=10), t1.get("buying_amt"), fill=sum_fill, font=bold, fmt="$#,##0.00")
    put(ws.cell(row=sum_row, column=11), t1.get("resale_amt"), fill=sum_fill, font=bold, fmt="$#,##0.00")
    put(ws.cell(row=sum_row, column=12), t1.get("gp_amt"), fill=sum_fill, font=bold, fmt="$#,##0")
    put(ws.cell(row=sum_row, column=13), (t1.get("gp_pct") or 0) / 100, fill=sum_fill, font=bold, fmt="0.00%")
    for c in range(1, 18):
        if not ws.cell(row=sum_row, column=c).value:
            put(ws.cell(row=sum_row, column=c), "", fill=sum_fill)

    # === 표2 (20 cols): plan 3달 + +3M + Aging 3개 + Backlog + Avg3M + Requested 3달 ===
    plan = t2.get("plan_months", [{}, {}, {}])
    req = t2.get("requested_po_qty", [0, 0, 0])
    while len(plan) < 3: plan.append({})
    while len(req) < 3: req.append(0)

    # 상위 병합 헤더
    t2_top = [None] * 20
    # 단일 컬럼들 (0~7): 아래와 동일
    # 8~11: 매출 계획 (month)
    # 12~14: Aging Inventory
    # 15: Backlog Total (Qty) Resale
    # 16: Avg3M Resale (Qty)
    # 17~19: Requested PO Q'ty
    t2_r1 = 6
    t2_r2 = 7
    t2_r3 = 8

    # 일반 단일 헤더 (row 6~7 병합)
    single_cols = {
        0: "Sales", 1: "Date", 2: "PART NO.", 3: "Customer",
        4: "Customer\nPO\nBalance", 5: "Inventory\nTotal (Qty)", 6: "B/Price", 7: "Inventory\nAMT",
        15: "Backlog\nTotal\n(Qty)", 16: "Avg3M\nResale\n(Qty)",
    }
    for col, h in single_cols.items():
        c1 = ws.cell(row=t2_r1, column=col+1)
        c1.value = h
        c1.fill = header_fill
        c1.font = bold
        c1.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c1.border = border
        ws.merge_cells(start_row=t2_r1, start_column=col+1, end_row=t2_r2, end_column=col+1)

    # 그룹 헤더
    def group(col_start, col_end, top_label, sub_labels):
        c = ws.cell(row=t2_r1, column=col_start+1)
        c.value = top_label
        c.fill = header_fill
        c.font = bold
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border
        ws.merge_cells(start_row=t2_r1, start_column=col_start+1, end_row=t2_r1, end_column=col_end+1)
        for i, sub in enumerate(sub_labels):
            sc = ws.cell(row=t2_r2, column=col_start+1+i)
            sc.value = sub
            sc.fill = header_fill
            sc.font = bold
            sc.alignment = Alignment(horizontal="center")
            sc.border = border

    group(8, 11, "매출 계획 (month)", [plan[0].get("label",""), plan[1].get("label",""), plan[2].get("label",""), "+3M"])
    group(12, 14, "Aging Inventory", ["1M", "2M", "3M~6M"])
    group(17, 19, "Requested PO Q'ty", [f"This Month\n({plan[0].get('label','')})", plan[1].get("label",""), plan[2].get("label","")])

    aging = t2.get("aging", {})
    t2_row = [
        inp.get("sales"), inp.get("date"), inp.get("mpn"), inp.get("customer"),
        inp.get("customer_po_balance"),
        t2.get("inventory_qty"), t2.get("b_price"), t2.get("inventory_amt"),
        plan[0].get("qty"), plan[1].get("qty"), plan[2].get("qty"), t2.get("plus_3m_qty"),
        aging.get("1M") or None, aging.get("2M") or None, aging.get("3M_6M") or None,
        t2.get("backlog_qty") or None, t2.get("avg_3m"),
        req[0], req[1], req[2],
    ]
    for j, v in enumerate(t2_row):
        fmt = None
        if j == 6: fmt = "$#,##0.00"
        elif j == 7: fmt = "$#,##0.00"
        elif j in (4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19): fmt = "#,##0"
        if v in (None, 0) and j in (11, 12, 13, 14, 15):
            v = "-"; fmt = None
        put(ws.cell(row=t2_r3, column=j+1), v, fmt=fmt)

    # Total 행
    t2_tot = 9
    put(ws.cell(row=t2_tot, column=4), "Total", fill=sum_fill, font=bold)
    put(ws.cell(row=t2_tot, column=6), t2.get("inventory_qty"), fill=sum_fill, font=bold, fmt="#,##0")
    put(ws.cell(row=t2_tot, column=8), t2.get("inventory_amt"), fill=sum_fill, font=bold, fmt="$#,##0.00")
    put(ws.cell(row=t2_tot, column=13), aging.get("1M") or "-", fill=sum_fill, font=bold, fmt="#,##0" if aging.get("1M") else None)
    put(ws.cell(row=t2_tot, column=14), aging.get("2M") or "-", fill=sum_fill, font=bold, fmt="#,##0" if aging.get("2M") else None)
    put(ws.cell(row=t2_tot, column=15), aging.get("3M_6M") or "-", fill=sum_fill, font=bold, fmt="#,##0" if aging.get("3M_6M") else None)
    for c in range(1, 21):
        cc = ws.cell(row=t2_tot, column=c)
        if cc.value is None:
            cc.value = ""
            cc.fill = sum_fill
            cc.border = border
            cc.alignment = center

    # === 표3: {MPN} 월말 재고 예상 ===
    t3_title_row = 11
    ws.cell(row=t3_title_row, column=1).value = f"{inp.get('mpn','')} 월말 재고 예상"
    ws.cell(row=t3_title_row, column=1).font = Font(bold=True, size=12)

    t3_r1 = 12
    months = t3.get("months", [])
    put(ws.cell(row=t3_r1, column=1), "구분", fill=header_fill, font=bold)
    put(ws.cell(row=t3_r1, column=2), f"재고({t3.get('start_month_label','')})", fill=header_fill, font=bold)
    for i, m in enumerate(months):
        put(ws.cell(row=t3_r1, column=3+i), m.get("label"), fill=header_fill, font=bold)

    put(ws.cell(row=t3_r1+1, column=1), "매입", font=bold)
    put(ws.cell(row=t3_r1+1, column=2), "")
    for i, m in enumerate(months):
        put(ws.cell(row=t3_r1+1, column=3+i), m.get("buy"), fmt="#,##0")

    put(ws.cell(row=t3_r1+2, column=1), "매출", font=bold)
    put(ws.cell(row=t3_r1+2, column=2), "")
    for i, m in enumerate(months):
        put(ws.cell(row=t3_r1+2, column=3+i), m.get("sell"), fmt="#,##0")

    put(ws.cell(row=t3_r1+3, column=1), "재고(월말)", font=bold, fill=sum_fill)
    put(ws.cell(row=t3_r1+3, column=2), t3.get("inventory_start"), fill=sum_fill, font=bold, fmt="#,##0")
    for i, m in enumerate(months):
        put(ws.cell(row=t3_r1+3, column=3+i), m.get("end_inv"), fill=sum_fill, font=bold, fmt="#,##0")

    # 열 너비
    for col in range(1, 21):
        letter = ws.cell(row=1, column=col).column_letter
        ws.column_dimensions[letter].width = 14

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    fname = f"PO_Request_{inp.get('mpn','')}_{inp.get('date','')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ==================== 1실 영업실적 변환 ====================

# INPUT (DATA 시트) 열 → OUTPUT 열 매핑
SALES_REPORT_MAP = [
    ("FAMILY", "FAMILY"),         # F → A
    ("DID", "DID"),               # G → B
    ("MPN", "MPN"),               # H → C
    ("QTY", "QTY"),               # J → D
    ("DCPL", "DCPL"),             # K → E
    ("AMOUNT", "AMOUNT"),         # L → F
    ("Quoted", "Quoted"),         # Y → G
    ("Amount", "Amount"),         # Z → H (자동계산 가능)
    ("Quote Creation", "Quote Creation"),  # AA → I
    ("DNNo.", "DNNo."),           # AH → J
    ("수입신고일", "수입신고일"),  # AM → K
    ("수입환율", "수입환율"),     # AN → L
    ("FSE", "FSE"),               # AU → M
    ("End customer", "End customer"),  # AV → N
]
SALES_REPORT_OUT_COLS = [out for _, out in SALES_REPORT_MAP]


def _to_clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v


@app.post("/api/sales-report/preview")
async def sales_report_preview(file: UploadFile = File(...)):
    """영업실적 데이터 양식 → 보고 양식으로 변환 (미리보기)."""
    contents = await file.read()
    # 엑셀 파일 먼저 연다 (엔진 명시 — EB에서 openpyxl 누락 시 진짜 에러를 드러냄)
    try:
        xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")
    except Exception as e:
        return {"error": f"엑셀 읽기 실패: {e}"}
    # 'DATA' 시트를 쓰되, 없으면 첫 시트로 폴백 (로컬 main.py 와 동일 동작)
    sheet = "DATA" if "DATA" in xls.sheet_names else (xls.sheet_names[0] if xls.sheet_names else None)
    if sheet is None:
        return {"error": "시트가 없습니다."}
    try:
        df = pd.read_excel(xls, sheet_name=sheet, header=0)
    except Exception as e:
        return {"error": f"데이터 시트 읽기 실패: {e}"}

    rows = []
    for _, row in df.iterrows():
        # 모든 셀이 빈 행은 건너뜀
        if row.isna().all():
            continue
        rec = {}
        for in_col, out_col in SALES_REPORT_MAP:
            rec[out_col] = _to_clean(row.get(in_col))
        # Amount(H) 자동 계산: 비어있으면 QTY × Quoted
        if rec.get("Amount") in (None, "", 0):
            qty = to_float(rec.get("QTY"))
            quoted = to_float(rec.get("Quoted"))
            if qty is not None and quoted is not None:
                rec["Amount"] = round(qty * quoted, 2)
        # 키 행 (FAMILY, MPN 모두 빈)은 건너뜀
        if not rec.get("FAMILY") and not rec.get("MPN") and not rec.get("DID"):
            continue
        rows.append(rec)
    return {"columns": SALES_REPORT_OUT_COLS, "rows": rows, "total": len(rows)}


@app.post("/api/sales-report/export")
async def sales_report_export(request: Request):
    """미리보기 결과를 OUTPUT 양식 엑셀로 다운로드."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
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
            ws.cell(row=i, column=j + 1, value=r.get(col))

    # 열 너비
    widths = [14, 10, 28, 10, 10, 12, 10, 10, 14, 14, 14, 11, 12, 24]
    for j, w in enumerate(widths):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(j + 1)].width = w

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    fname = f"영업실적_보고_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


# ==================== 1실 발주요청서 변환 ====================

PO_REPORT_T1_COLS = [
    "CPO", "Order Date", "Unitrontech CRD", "고객사 요청일",
    "담당 Sales", "End Customer", "DID", "MPN", "Package",
    "Qty", "DCPL", "Resale Price", "Resale AMT", "PO Customer",
]


def _fmt_korean_month(v):
    """date → '2026년 8월' 형식. 실패 시 원본 반환."""
    if v is None:
        return None
    try:
        d = pd.Timestamp(v)
        return f"{d.year}년 {d.month}월"
    except Exception:
        return str(v) if v else None


@app.post("/api/po-report/preview")
async def po_report_preview(file: UploadFile = File(...)):
    """발주요청서 데이터 양식 → 보고 양식 (표1, 표2)으로 변환."""
    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents), header=0)
    except Exception:
        return {"error": "엑셀을 읽을 수 없습니다."}

    # 표1: 행 단위 직접 매핑
    t1_rows = []
    for _, row in df.iterrows():
        if row.isna().all():
            continue
        po_date = _to_clean(row.get("PO Date"))
        srd = _to_clean(row.get("Customer SRD"))
        rec = {
            "CPO": _to_clean(row.get("CUST PO#")),
            "Order Date": po_date,
            "Unitrontech CRD": _to_clean(row.get("CRD")),
            "고객사 요청일": _fmt_korean_month(srd) if srd else None,
            "담당 Sales": _to_clean(row.get("FSE")),
            "End Customer": _to_clean(row.get("End customer")),
            "DID": _to_clean(row.get("DID")),
            "MPN": _to_clean(row.get("MPN")),
            "Package": _to_clean(row.get("BOX_TYPE")),
            "Qty": _to_clean(row.get("QTY")),
            "DCPL": _to_clean(row.get("DCPL")),
            "Resale Price": _to_clean(row.get("SP ($)")),
            "Resale AMT": _to_clean(row.get("Sales Amt ($)")),
            "PO Customer": _to_clean(row.get("PO Customer")),
        }
        if not rec.get("MPN") and not rec.get("DID"):
            continue
        # Resale AMT 비어있으면 자동계산
        if rec["Resale AMT"] in (None, 0, ""):
            qty = to_float(rec["Qty"])
            price = to_float(rec["Resale Price"])
            if qty is not None and price is not None:
                rec["Resale AMT"] = round(qty * price, 2)
        t1_rows.append(rec)

    # 표1 합계
    sum_qty = sum(to_float(r.get("Qty")) or 0 for r in t1_rows)
    sum_amt = sum(to_float(r.get("Resale AMT")) or 0 for r in t1_rows)

    # 표2: MPN별 집계
    t2_groups = {}
    for r in t1_rows:
        key = (r.get("MPN"), r.get("End Customer"))
        if key not in t2_groups:
            t2_groups[key] = {
                "Date": r.get("Order Date"),
                "Sales": r.get("담당 Sales"),
                "PART NO.": r.get("MPN"),
                "Customer": r.get("End Customer"),
                "기 수주": None,
                "신규 수주": 0,
                "Inventory": None,
                "Backlog": None,
                "매출 M": None,
                "매출 +1M": None,
                "매출 +2M": None,
                "매출 +3M": None,
                "매출 ~+4M": None,
                "Remarks": None,
            }
        t2_groups[key]["신규 수주"] += to_float(r.get("Qty")) or 0
    t2_rows = list(t2_groups.values())

    return {
        "t1_columns": PO_REPORT_T1_COLS,
        "t1_rows": t1_rows,
        "t1_sum": {"Qty": round(sum_qty), "Resale AMT": round(sum_amt, 2)},
        "t2_rows": t2_rows,
    }


@app.post("/api/po-report/export")
async def po_report_export(request: Request):
    """변환 결과를 엑셀(표1/표2/표3)로 다운로드."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    data = await request.json()
    t1_rows = data.get("t1_rows", [])
    t1_sum = data.get("t1_sum", {})
    t2_rows = data.get("t2_rows", [])

    wb = Workbook()

    # === 표1 ===
    ws1 = wb.active
    ws1.title = "표1"
    header_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
    sum_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center")

    for j, h in enumerate(PO_REPORT_T1_COLS):
        cell = ws1.cell(row=1, column=j + 1, value=h)
        cell.fill = header_fill
        cell.font = bold
        cell.alignment = center

    for i, r in enumerate(t1_rows, start=2):
        for j, col in enumerate(PO_REPORT_T1_COLS):
            ws1.cell(row=i, column=j + 1, value=r.get(col))

    sum_row = len(t1_rows) + 2
    ws1.cell(row=sum_row, column=10, value=t1_sum.get("Qty"))
    ws1.cell(row=sum_row, column=10).fill = sum_fill
    ws1.cell(row=sum_row, column=10).font = bold
    ws1.cell(row=sum_row, column=13, value=t1_sum.get("Resale AMT"))
    ws1.cell(row=sum_row, column=13).fill = sum_fill
    ws1.cell(row=sum_row, column=13).font = bold

    widths1 = [14, 12, 14, 14, 12, 18, 8, 28, 12, 10, 10, 12, 12, 18]
    for j, w in enumerate(widths1):
        ws1.column_dimensions[get_column_letter(j + 1)].width = w

    # === 표2 ===
    ws2 = wb.create_sheet("표2")
    t2_cols = ["Date", "Sales", "PART NO.", "Customer", "기 수주", "신규 수주",
               "Inventory", "Backlog",
               "매출 M", "매출 +1M", "매출 +2M", "매출 +3M", "매출 ~+4M", "Remarks"]
    for j, h in enumerate(t2_cols):
        cell = ws2.cell(row=1, column=j + 1, value=h)
        cell.fill = header_fill
        cell.font = bold
        cell.alignment = center
    for i, r in enumerate(t2_rows, start=2):
        for j, col in enumerate(t2_cols):
            ws2.cell(row=i, column=j + 1, value=r.get(col))
    widths2 = [12, 10, 28, 18, 10, 10, 10, 10, 10, 10, 10, 10, 12, 16]
    for j, w in enumerate(widths2):
        ws2.column_dimensions[get_column_letter(j + 1)].width = w

    # === 표3 (빈 템플릿) ===
    ws3 = wb.create_sheet("표3")
    ws3.cell(row=1, column=1, value="표3 (월별 수급 시뮬) — 사용자 수동 입력").font = bold
    ws3.cell(row=2, column=1, value="MPN별로 Apr~Feb 11개월의 Delivery / Inventory / Backlog / New PO / Balance 입력").font = Font(size=10, color="666666")

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    fname = f"발주요청서_보고_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


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
    groups = {}  # key=(date, customer, currency) → group dict

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
            # O열 = 통화 코드 (KRW / USD)
            currency = _norm_str(row.iloc[14]) if len(row) > 14 else None
            if not currency or currency.upper() not in ("KRW", "USD"):
                currency = "KRW"  # 기본
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
                # USD only: 환율/₩ 변환 없음
                amount_usd = round((price or 0) * qty, 2)
                item["amount_usd"] = amount_usd
                item["price_krw"] = None
                item["amount_krw"] = None
                item["rate"] = None
                groups[key]["total_usd"] += amount_usd
            else:
                # KRW: 단가가 KRW
                amount_krw = round((price or 0) * qty, 0)
                item["amount_usd"] = None
                item["price_krw"] = price or 0
                item["amount_krw"] = amount_krw
                item["rate"] = None
                groups[key]["total_krw"] += amount_krw

            groups[key]["items"].append(item)
            groups[key]["total_qty"] += qty

    # 정렬: 날짜 → 고객 → 통화
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
    meta_b64 = base64.b64encode(json.dumps(meta, ensure_ascii=False).encode("utf-8")).decode("ascii")

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


# ==================== 4실 주간 영업실적 취합 ====================

# 표준 14열
SALES_AGG_COLS = [
    "Vendor", "PN", "QTY", "U/P", "AMOUNT",
    "수입신고일", "수입환율", "FSE", "한글업체명", "거래처코드",
    "SP ($)", "Sales Amt ($)", "매출환율", "SP (KRW)", "Sales Amt(KRW)",
    "GP($)", "GP%($)", "GP(KRW)", "GP%(KRW)",
]


def _to_clean_v(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        try:
            return v.strftime("%Y-%m-%d")
        except Exception:
            return None
    return v


def _parse_kec(xls):
    """KEC 36열 → 표준 14열 매핑"""
    sh = None
    for s in xls.sheet_names:
        if "(본사)" in s or "본사" in s:
            sh = s
            break
    if not sh:
        # 가장 큰 시트 (헤더+데이터 포함)
        sh = max(xls.sheet_names, key=lambda x: pd.read_excel(xls, sheet_name=x, header=None).shape[0])
    df = pd.read_excel(xls, sheet_name=sh, header=1)
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("PN")):
            continue
        sales_usd = to_float(r.get("SalesAmt($)")) or 0
        buy_usd = to_float(r.get("BuyingAmt")) or 0
        sales_krw = to_float(r.get("SalesAmt(KRW)")) or 0
        buy_krw = to_float(r.get("BuyingAmt(KRW)")) or 0
        gp_usd = sales_usd - buy_usd
        gp_krw = sales_krw - buy_krw
        rec = {
            "Vendor": "KEC",
            "PN": _to_clean_v(r.get("PN")),
            "QTY": to_float(r.get("Q'TY")),
            "U/P": to_float(r.get("Buyingprice")),
            "AMOUNT": buy_usd,
            "수입신고일": _to_clean_v(r.get("입고일")),
            "수입환율": to_float(r.get(" 적용환율")) or to_float(r.get("적용환율")),
            "FSE": _to_clean_v(r.get("FSE")),
            "한글업체명": _to_clean_v(r.get("Customer (origin)")),
            "거래처코드": _to_clean_v(r.get("Customer#")),
            "SP ($)": to_float(r.get("SP($)")),
            "Sales Amt ($)": sales_usd,
            "매출환율": to_float(r.get("매출환율")),
            "SP (KRW)": to_float(r.get(" SP(KRW)")) or to_float(r.get("SP(KRW)")),
            "Sales Amt(KRW)": sales_krw,
            "GP($)": gp_usd,
            "GP%($)": (gp_usd / sales_usd) if sales_usd else 0,
            "GP(KRW)": gp_krw,
            "GP%(KRW)": (gp_krw / sales_krw) if sales_krw else 0,
        }
        rows.append(rec)
    return rows


def _parse_standard(xls, vendor_label):
    """DELTA/FUJITSU 표준 14열 포맷 → 표준화 + GP 계산"""
    # 첫 시트 사용 (데이터가 있는 시트)
    sh = xls.sheet_names[0]
    for s in xls.sheet_names:
        df_chk = pd.read_excel(xls, sheet_name=s, header=0, nrows=1)
        if "PN" in df_chk.columns or "DID" in df_chk.columns:
            sh = s
            break
    df = pd.read_excel(xls, sheet_name=sh, header=0)
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("PN")):
            continue
        amount = to_float(r.get("AMOUNT")) or 0
        sales_usd = to_float(r.get("Sales Amt ($)")) or 0
        sales_krw = to_float(r.get("Sales Amt(KRW)")) or 0
        # GP = Sales Amt - AMOUNT (buying)
        gp_usd = sales_usd - amount
        # AMOUNT는 USD 기준이지만 KRW Buying이 따로 없을 수도 있음.
        # KRW GP를 위해 AMOUNT * 매출환율을 buying_krw로 추정
        rate = to_float(r.get("매출환율")) or to_float(r.get("수입환율")) or 1400
        buy_krw_est = amount * rate
        gp_krw = sales_krw - buy_krw_est
        rec = {
            "Vendor": vendor_label,
            "PN": _to_clean_v(r.get("PN")),
            "QTY": to_float(r.get("QTY")),
            "U/P": to_float(r.get("U/P")),
            "AMOUNT": amount,
            "수입신고일": _to_clean_v(r.get("수입신고일")),
            "수입환율": to_float(r.get("수입환율")),
            "FSE": _to_clean_v(r.get("FSE")),
            "한글업체명": _to_clean_v(r.get("End customer")),
            "거래처코드": None,
            "SP ($)": to_float(r.get("SP ($)")),
            "Sales Amt ($)": sales_usd,
            "매출환율": rate,
            "SP (KRW)": to_float(r.get("SP (KRW)")),
            "Sales Amt(KRW)": sales_krw,
            "GP($)": round(gp_usd, 2),
            "GP%($)": round(gp_usd / sales_usd, 4) if sales_usd else 0,
            "GP(KRW)": round(gp_krw, 0),
            "GP%(KRW)": round(gp_krw / sales_krw, 4) if sales_krw else 0,
        }
        rows.append(rec)
    return rows


def _detect_vendor(filename: str, xls) -> str:
    """파일명/시트명/컬럼으로 벤더 자동 판단."""
    name = filename.lower()
    if "kec" in name:
        return "KEC"
    if "delta" in name:
        return "DELTA"
    if "fujitsu" in name or "ramxeed" in name:
        return "RAMXEED(FUJITSU)"
    # 시트명 또는 첫 시트 첫 행 vendor 컬럼 확인
    try:
        for sh in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sh, header=0, nrows=2)
            if "Vendor" in df.columns and len(df) > 0:
                v = str(df.iloc[0]["Vendor"]).upper()
                if "KEC" in v: return "KEC"
                if "DELTA" in v: return "DELTA"
                if "FUJITSU" in v or "RAMXEED" in v: return "RAMXEED(FUJITSU)"
            if "DID" in df.columns and len(df) > 0:
                d = str(df.iloc[0]["DID"]).upper()
                if "FUJITSU" in d or "RAMXEED" in d: return "RAMXEED(FUJITSU)"
            if "(본사)" in sh or "PODate" in df.columns:
                return "KEC"
    except Exception:
        pass
    return "UNKNOWN"


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

    # 벤더별 집계
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
    # SUM 행
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

    # 날짜 범위 (수입신고일 기준)
    dates = sorted([r["수입신고일"] for r in all_rows if r.get("수입신고일")])
    date_min = dates[0] if dates else ""
    date_max = dates[-1] if dates else ""
    # 주요 월
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
    from urllib.parse import quote

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
    fname = f"매출현황_{main_month or '주간'}_영업4실_{datetime.now().strftime('%y%m%d')}.xlsx"
    fname_enc = quote(fname)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=sales_summary.xlsx; filename*=UTF-8''{fname_enc}"})


# ==================== 단가 검증 ====================

def _norm_str(v):
    if v is None:
        return ""
    # float으로 온 코드 (예: 104901.0) → "104901"로 정규화
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        if v.is_integer():
            return str(int(v))
    s = str(v).strip()
    if s.lower() in ("nan", "none"):
        return ""
    # "104901.0" 같은 문자열도 정수화
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _parse_date(v):
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return v.date() if hasattr(v, "date") else v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


@app.post("/api/price-check/validate")
async def price_check_validate(file: UploadFile = File(...)):
    """단가 검증: 5개 시트 있는 엑셀 업로드 → 각 요청 행마다 DC/QTN/ASD 조회·검증 → 결과 반환."""
    from datetime import date as dt_date
    contents = await file.read()
    xls = pd.ExcelFile(io.BytesIO(contents), engine="openpyxl")

    # 시트명 찾기 (공백/특수문자 허용)
    def find_sheet(kws):
        for s in xls.sheet_names:
            low = s.replace(" ", "").lower()
            for kw in kws:
                if kw.replace(" ", "").lower() in low:
                    return s
        return None

    sh_input = find_sheet(["업체별", "사용여부"])
    sh_dc = find_sheet(["단가1", "수입가"])
    sh_qtn = find_sheet(["단가2", "QTN"])
    sh_asd = find_sheet(["단가3", "ASD"])
    sh_map = find_sheet(["업체코드", "매칭"])
    missing = [name for name, v in [
        ("업체별 파트 사용여부", sh_input),
        ("단가1(수입가)", sh_dc),
        ("단가2(QTN)", sh_qtn),
        ("단가3(ASD)", sh_asd),
        ("업체코드·매칭", sh_map),
    ] if not v]
    if missing:
        return {"error": f"시트 못 찾음: {', '.join(missing)}"}

    # 1. DC dict: device → price
    df_dc = pd.read_excel(xls, sheet_name=sh_dc, header=0)
    dc_map = {}
    for _, row in df_dc.iterrows():
        dev = _norm_str(row.get("Device") or row.iloc[0])
        pr = to_float(row.get("DC") or row.iloc[1] if df_dc.shape[1] > 1 else None)
        if dev and pr is not None:
            dc_map[dev.upper()] = pr

    # 2. 업체매칭 dict: code(str) → 영문이름(지역포함)
    df_map = pd.read_excel(xls, sheet_name=sh_map, header=1)
    code_to_name = {}  # includes region
    code_to_name_plain = {}  # no region
    for _, row in df_map.iterrows():
        code = _norm_str(row.get("코드") or row.iloc[0])
        eng = _norm_str(row.get("고객사 영문이름") or row.iloc[2] if df_map.shape[1] > 2 else None)
        plain = _norm_str(row.get("지역명제외이름") or row.iloc[3] if df_map.shape[1] > 3 else None)
        if code:
            if eng:
                code_to_name[code] = eng
            if plain:
                code_to_name_plain[code] = plain

    # 3. ASD dict: part# → (price, start_date, end_date)
    df_asd = pd.read_excel(xls, sheet_name=sh_asd, header=0)
    asd_map = {}
    for _, row in df_asd.iterrows():
        part = _norm_str(row.get("PART#") or row.iloc[0])
        pr = to_float(row.get("PRICE") or row.iloc[1] if df_asd.shape[1] > 1 else None)
        sd = _parse_date(row.iloc[2] if df_asd.shape[1] > 2 else None)
        ed = _parse_date(row.iloc[3] if df_asd.shape[1] > 3 else None)
        if part and pr is not None:
            # 여러 행 있으면 최신(가장 최근 start) 유지
            existing = asd_map.get(part.upper())
            if not existing or (sd and existing[1] and sd > existing[1]):
                asd_map[part.upper()] = (pr, sd, ed)

    # 4. QTN: group by (MPN upper, customer name) → list of quotes
    df_qtn = pd.read_excel(xls, sheet_name=sh_qtn, header=1)
    qtn_by_key = {}
    for _, row in df_qtn.iterrows():
        mpn = _norm_str(row.get("MPN"))
        cust = _norm_str(row.get("고객사 영문이름"))
        if not mpn or not cust:
            continue
        key = (mpn.upper(), cust)
        rec = {
            "quote": _norm_str(row.get("Quote #")),
            "remains": to_float(row.get("Remains")) or 0,
            "start": _parse_date(row.get("시작일자")),
            "end": _parse_date(row.get("유효일자")),
            "price": to_float(row.get("매입가")) or 0,
            "method": _norm_str(row.get("Quote Method")).upper(),
        }
        qtn_by_key.setdefault(key, []).append(rec)

    # 5. 입력 시트 파싱
    df_in = pd.read_excel(xls, sheet_name=sh_input, header=2)  # 헤더가 3행에 있음
    # 가능한 컬럼명 정규화
    rename_map = {}
    for col in df_in.columns:
        c = str(col).strip()
        if c in ("일자", "날짜"):
            rename_map[col] = "date"
        elif c in ("업체코드",):
            rename_map[col] = "cust_code"
        elif c in ("업체명",):
            rename_map[col] = "cust_name"
        elif c in ("파트명", "Part", "PART#"):
            rename_map[col] = "part"
        elif c in ("수량",):
            rename_map[col] = "qty"
    df_in = df_in.rename(columns=rename_map)

    results = []
    for _, row in df_in.iterrows():
        part_raw = _norm_str(row.get("part"))
        code = _norm_str(row.get("cust_code"))
        if not part_raw or not code:
            continue
        date_req = _parse_date(row.get("date")) or dt_date.today()
        qty = to_float(row.get("qty")) or 0
        cust_name = _norm_str(row.get("cust_name"))
        part_upper = part_raw.upper()

        # --- DC 조회 ---
        dc_price = dc_map.get(part_upper)

        # --- ASD 조회 ---
        asd_entry = asd_map.get(part_upper)
        asd_price = None
        asd_reason = None
        if asd_entry:
            pr, sd, ed = asd_entry
            if sd and date_req < sd:
                asd_reason = "ASD 시작일 이전"
            elif ed and date_req > ed:
                asd_reason = "ASD 만료"
            else:
                asd_price = pr

        # --- QTN 조회 ---
        qtn_price = None
        qtn_remains = None
        qtn_reason = None
        cust_eng = code_to_name.get(code) or ""
        cust_plain = code_to_name_plain.get(code) or ""

        # MPN으로 가능한 모든 quote (고객명 상관없이) 먼저 모으기
        all_for_mpn = []
        for (mpn_k, cust_k), quotes in qtn_by_key.items():
            if mpn_k != part_upper:
                continue
            # 고객 매칭: 정확 일치 or 지역명 제외 이름 포함
            if cust_k == cust_eng or (cust_plain and cust_plain.lower() in cust_k.lower()):
                all_for_mpn.extend(quotes)
        if all_for_mpn:
            # 방법별 우선순위: DPA 유효 → Budgetary 플래그
            valid_dpa = []
            has_non_dpa = False
            expired = False
            qty_over = False
            for q in all_for_mpn:
                if q["method"] != "DPA":
                    has_non_dpa = True
                    continue
                # 유효기간 체크
                if q["start"] and date_req < q["start"]:
                    continue
                if q["end"] and date_req > q["end"]:
                    expired = True
                    continue
                if qty > q["remains"]:
                    qty_over = True
                    continue
                valid_dpa.append(q)
            if valid_dpa:
                best_q = min(valid_dpa, key=lambda x: x["price"])
                qtn_price = best_q["price"]
                qtn_remains = best_q["remains"]
            elif has_non_dpa and not valid_dpa:
                qtn_reason = "❌ Budgetary (DPA 필요)"
            elif expired:
                qtn_reason = "QTN 만료"
            elif qty_over:
                qtn_reason = "수량 초과"
            else:
                qtn_reason = "QTN 조건 불충족"

        # --- 출고 가능 판단 ---
        # 규칙: QTN(DPA,유효,수량OK) 또는 ASD(유효기간OK) 중 하나 이상 있어야 출고 가능.
        # DC 단독은 참고용, 출고 근거 불충분.
        qtn_ok = qtn_price is not None
        asd_ok = asd_price is not None

        ok = False
        reason = ""
        best_price = None
        best_src = None
        if qtn_ok or asd_ok:
            # 승인 단가 존재 → DC 포함 최저가 선택
            candidates = []
            if dc_price is not None:
                candidates.append(("DC", dc_price))
            if qtn_ok:
                candidates.append(("QTN", qtn_price))
            if asd_ok:
                candidates.append(("ASD", asd_price))
            best_src, best_price = min(candidates, key=lambda x: x[1])
            ok = True
        else:
            # 사유 우선순위: Budgetary > QTN 만료 > 수량 초과 > ASD 만료 > 단가 없음
            if qtn_reason:
                reason = qtn_reason
            elif asd_reason:
                reason = asd_reason
            else:
                reason = "단가 없음"

        results.append({
            "date": str(date_req),
            "cust_code": code,
            "cust_name": cust_name or code_to_name_plain.get(code, ""),
            "part": part_raw,
            "qty": qty,
            "ok": ok,
            "status": "정상" if ok else "불가",
            "dc": dc_price,
            "qtn": qtn_price,
            "qtn_remains": qtn_remains,
            "asd": asd_price,
            "best_price": best_price,
            "best_source": best_src,
            "reason": reason or "",
            "qtn_reason": qtn_reason or "",
            "asd_reason": asd_reason or "",
        })

    return {
        "total": len(results),
        "ok_count": sum(1 for r in results if r["ok"]),
        "ng_count": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


@app.post("/api/price-check/export")
async def price_check_export(request: Request):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    data = await request.json()
    results = data.get("results", [])

    wb = Workbook()
    ws = wb.active
    ws.title = "단가 검증 결과"

    headers = ["일자", "업체코드", "업체명", "파트명", "수량", "출고가능",
               "DC", "QTN", "QTN잔여", "ASD", "최저가", "적용기준", "사유"]
    header_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
    bold = Font(bold=True)
    center = Alignment(horizontal="center")

    for j, h in enumerate(headers):
        cell = ws.cell(row=1, column=j + 1, value=h)
        cell.fill = header_fill
        cell.font = bold
        cell.alignment = center

    ng_fill = PatternFill(start_color="F4CCCC", end_color="F4CCCC", fill_type="solid")
    ok_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    for i, r in enumerate(results, start=2):
        vals = [
            r.get("date"), r.get("cust_code"), r.get("cust_name"),
            r.get("part"), r.get("qty"),
            r.get("status"),
            r.get("dc"), r.get("qtn"), r.get("qtn_remains"), r.get("asd"),
            r.get("best_price"), r.get("best_source"), r.get("reason"),
        ]
        fill = ng_fill if not r.get("ok") else ok_fill
        for j, v in enumerate(vals):
            cell = ws.cell(row=i, column=j + 1, value=v)
            cell.fill = fill

    for col_letter, w in zip("ABCDEFGHIJKLM", [12, 10, 24, 22, 9, 9, 9, 9, 10, 9, 10, 10, 28]):
        ws.column_dimensions[col_letter].width = w

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=price_check_result.xlsx"})


# ==================== 엑셀 내보내기 ====================

@app.post("/api/export")
async def export_excel(request: Request):
    from openpyxl.styles import PatternFill, Font, Alignment
    data = await request.json()
    rows = data.get("data", [])
    columns = data.get("columns", [])
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="마이크로칩(매칭)", index=False)
        ws = writer.sheets["마이크로칩(매칭)"]
        sky_blue = PatternFill(start_color="87CEEB", end_color="87CEEB", fill_type="solid")
        for col_idx in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = sky_blue
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        ws.auto_filter.ref = ws.dimensions
    output.seek(0)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=export.xlsx"})


# ==================== 환율 + 거래명세서 ====================

import requests as http_requests

@app.get("/api/exchange-rate")
async def get_exchange_rate(date: str = ""):
    """USD/KRW 매매기준율 조회.
    - date 미지정: 오늘 (영업일 아니면 직전 영업일 소급)
    1순위: 한국수출입은행 매매기준율 / 2순위: frankfurter / 3순위: open.er-api
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")

    # 1. KoreaExim (영업일 소급)
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
        resp = http_requests2.get(f"https://api.frankfurter.app/{target_date}?from=USD&to=KRW", timeout=8)
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


def _build_invoice_xlsx_bytes(data: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.drawing.image import Image as XlImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor


    items = data.get("items", [])
    customer = data.get("customer", "")
    date_str = data.get("date", "")
    rate = float(data.get("rate", 1400))

    wb = Workbook()
    ws = wb.active
    ws.title = "거래명세서"

    widths = {"A": 5, "B": 20, "C": 8, "D": 18, "E": 16, "F": 10, "G": 20, "H": 18}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    thin = Side(style="thin")
    thick = Side(style="medium")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    left_a = Alignment(horizontal="left", vertical="center")

    # 제목
    ws.merge_cells("A1:H1")
    ws["A1"] = "거래명세표"
    ws["A1"].font = Font(bold=True, size=36)
    ws["A1"].alignment = center
    ws.row_dimensions[1].height = 55
    ws.row_dimensions[2].height = 8

    # 공급자
    ws.merge_cells("A3:C3")
    ws["A3"] = "공 급 자"
    ws["A3"].font = Font(bold=True, size=10)
    ws["A3"].alignment = center
    ws["A3"].border = Border(left=thick, top=thick, bottom=thin)
    ws["B3"].border = Border(top=thick, bottom=thin)
    ws["C3"].border = Border(right=thick, top=thick, bottom=thin)

    info = [(4, "등록번호 : 229-81-00105"), (5, "상      호 : ㈜유니트론텍"),
            (6, "대표이사 : 남궁 선"), (7, "주 : 서울 강남구 영동대로 638(삼성동, 삼보빌딩 9층)"),
            (8, "업      태 : 도.소매"), (9, "종      목 :전자부품 외")]
    for row_num, val in info:
        ws.merge_cells(f"A{row_num}:C{row_num}")
        ws[f"A{row_num}"] = val
        ws[f"A{row_num}"].font = Font(size=8)
        ws[f"A{row_num}"].alignment = left_a
        if row_num == 9:
            ws[f"A{row_num}"].border = Border(left=thick, bottom=thick)
            ws[f"B{row_num}"].border = Border(bottom=thick)
            ws[f"C{row_num}"].border = Border(right=thick, bottom=thick)
        else:
            ws[f"A{row_num}"].border = Border(left=thick)
            ws[f"C{row_num}"].border = Border(right=thick)

    # 공급받는자
    ws.merge_cells("F3:H3")
    ws["F3"] = "공급받는자"
    ws["F3"].font = Font(bold=True, size=10)
    ws["F3"].alignment = center
    ws["F3"].border = Border(left=thick, top=thick, bottom=thin)
    ws["G3"].border = Border(top=thick, bottom=thin)
    ws["H3"].border = Border(right=thick, top=thick, bottom=thin)

    ws.merge_cells("F4:H9")
    ws["F4"] = customer
    ws["F4"].font = Font(bold=True, size=16)
    ws["F4"].alignment = center
    for rn in range(4, 10):
        for cl in ["F", "G", "H"]:
            l = thick if cl == "F" else Side()
            r = thick if cl == "H" else Side()
            b = thick if rn == 9 else Side()
            ws[f"{cl}{rn}"].border = Border(left=l, right=r, bottom=b)

    # 발행일 / 담당자 (오른쪽 상단)
    issue_date = data.get("issue_date", "")
    person = data.get("person_in_charge", "")
    if issue_date:
        ws["K5"] = "발행일"
        ws["K5"].font = Font(size=10)
        ws["K5"].alignment = center
        ws.merge_cells("L5:M5")
        ws["L5"] = issue_date
        ws["L5"].font = Font(size=10)
        ws["L5"].alignment = center
    if person:
        ws["K6"] = "담당자"
        ws["K6"].font = Font(size=10)
        ws["K6"].alignment = center
        ws.merge_cells("L6:M6")
        ws["L6"] = person
        ws["L6"].font = Font(size=10)
        ws["L6"].alignment = center

    ws["B12"] = date_str
    ws["B12"].font = Font(bold=True, size=10)

    # 헤더
    headers = ["No.", "Part #", "QTY", "U/PRICE ($)", "Amount ($)", "RATE", "U/PRICE (￦)", "AMOUNT (￦)"]
    header_fill = PatternFill(start_color="CCFF33", end_color="CCFF33", fill_type="solid")
    for j, h in enumerate(headers):
        cell = ws.cell(row=13, column=j+1)
        cell.value = h
        cell.font = Font(bold=True, size=9)
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border

    total_usd = total_krw = 0
    for i, item in enumerate(items):
        row = 14 + i
        qty = float(item.get("qty", 0))
        currency = (item.get("currency") or "USD").upper()
        if currency == "KRW":
            # 원화만
            price_krw = float(item.get("price_krw", item.get("price", 0)))
            amount_krw = float(item.get("amount_krw", round(qty * price_krw, 0)))
            total_krw += amount_krw
            vals = [i+1, item.get("part",""), int(qty), None, None, None, price_krw, int(amount_krw)]
            fmts = [None, None, None, None, None, None, '₩#,##0.00###', '₩#,##0']
        elif item.get("rate"):
            # 환율 명시된 USD: 양쪽 표시
            price_usd = float(item.get("price", 0))
            item_rate = float(item.get("rate"))
            amount_usd = round(qty * price_usd, 2)
            price_krw = round(price_usd * item_rate, 2)
            amount_krw = round(amount_usd * item_rate, 0)
            total_usd += amount_usd
            total_krw += amount_krw
            vals = [i+1, item.get("part",""), int(qty), price_usd, amount_usd, item_rate, price_krw, int(amount_krw)]
            fmts = [None, None, None, '$#,##0.00###', '$#,##0.00', '#,##0.00', '₩#,##0.00###', '₩#,##0']
        else:
            # USD only: $ 컬럼만, RATE/₩ 빈칸
            price_usd = float(item.get("price", 0))
            amount_usd = round(qty * price_usd, 2)
            total_usd += amount_usd
            vals = [i+1, item.get("part",""), int(qty), price_usd, amount_usd, None, None, None]
            fmts = [None, None, None, '$#,##0.00###', '$#,##0.00', None, None, None]
        for j, (v, fmt) in enumerate(zip(vals, fmts)):
            c = ws.cell(row=row, column=j+1, value=v)
            c.alignment = center
            c.border = border
            if fmt:
                c.number_format = fmt

    for i in range(len(items), 10):
        row = 14 + i
        ws.cell(row=row, column=1, value=i+1).alignment = center
        for j in range(1, 9):
            ws.cell(row=row, column=j).border = border

    sr = 24
    tax_usd = round(total_usd * 0.1, 2)
    tax_krw = round(total_krw * 0.1, 0)
    for label, uv, kv, off in [("소  계", total_usd, total_krw, 0), ("부가세", tax_usd, tax_krw, 1),
                                ("합  계", total_usd+tax_usd, total_krw+tax_krw, 2)]:
        r = sr + off
        fnt = Font(bold=True, size=9)
        ws.cell(row=r, column=4, value=label).font = fnt
        ws.cell(row=r, column=4).alignment = center
        ws.cell(row=r, column=5, value=uv).number_format = '$#,##0.00'
        ws.cell(row=r, column=5).alignment = center
        ws.cell(row=r, column=5).font = fnt
        ws.cell(row=r, column=7, value=label).font = fnt
        ws.cell(row=r, column=7).alignment = center
        ws.cell(row=r, column=8, value=int(kv)).number_format = '₩#,##0'
        ws.cell(row=r, column=8).alignment = center
        ws.cell(row=r, column=8).font = fnt
        for j in [4, 5, 7, 8]:
            ws.cell(row=r, column=j).border = border

    # 합계 아래 줄
    for j in range(1, 9):
        c = ws.cell(row=sr+2, column=j)
        c.border = Border(left=c.border.left, right=c.border.right, top=c.border.top, bottom=Side(style="medium"))

    ws.merge_cells("A27:H27")
    ws["A27"] = "  비  고 : 금일 최초고시 매매기준율 적용"
    ws["A27"].font = Font(size=9)

    ws.merge_cells("E30:H30")
    ws["E30"] = "인수자 :                                              (인)"
    for j in range(1, 9):
        ws.cell(row=30, column=j).border = Border(bottom=thin)

    ws["B31"] = "계좌정보"
    ws["B31"].font = Font(bold=True, size=9)
    ws["B32"] = "원화> 기업은행 528-002245-01011"
    ws["B32"].font = Font(size=9)
    ws["B33"] = "외화> 기업은행 528-002245-56-00013"
    ws["B33"].font = Font(size=9)

    stamp_path = os.path.join(os.path.dirname(__file__), "stamp.png")
    if os.path.exists(stamp_path):
        img = XlImage(stamp_path)
        img.width = 75
        img.height = 75
        m1 = AnchorMarker(col=2, colOff=300000, row=1, rowOff=50000)
        m2 = AnchorMarker(col=3, colOff=200000, row=4, rowOff=100000)
        img.anchor = TwoCellAnchor(_from=m1, to=m2)
        ws.add_image(img)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


@app.post("/api/invoice/generate")
async def generate_invoice(request: Request):
    data = await request.json()
    xlsx_bytes = _build_invoice_xlsx_bytes(data)
    date_str = data.get("date", "")
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=invoice_{date_str}.xlsx"},
    )


def _fmt_unit_price(v, sym):
    """단가 포맷: 소수점 2~5자리, trailing 0 제거 (단 최소 2자리 유지)."""
    if v is None:
        return ""
    s = f"{v:,.5f}"
    if "." in s:
        intpart, dec = s.split(".")
        dec = dec.rstrip("0")
        if len(dec) < 2:
            dec = (dec + "00")[:2]
        s = f"{intpart}.{dec}"
    return f"{sym}{s}"


def _build_invoice_pdf_bytes(data: dict) -> bytes:
    """reportlab으로 거래명세서 PDF 생성 (양식 PDF 기준 레이아웃)."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import Color, black
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    # 번들 폰트 우선 (PDF에 임베드 → 뷰어 의존성 제거, ₩ 확실히 렌더)
    KF = None
    bundled = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic.ttf")
    if os.path.exists(bundled):
        try:
            pdfmetrics.registerFont(TTFont("KoreanFont", bundled))
            KF = "KoreanFont"
        except Exception:
            pass
    # 시스템 NotoSansCJK 폴백
    if not KF:
        import glob
        candidates = []
        for ext in ("ttc", "otf", "ttf"):
            candidates.extend(glob.glob(f"/usr/share/fonts/**/NotoSans*CJK*.{ext}", recursive=True))
        for path in list(dict.fromkeys(candidates)):
            if path.endswith(".ttc"):
                for sub_idx in range(8):
                    try:
                        pdfmetrics.registerFont(TTFont("KoreanFont", path, subfontIndex=sub_idx))
                        if pdfmetrics.stringWidth("₩", "KoreanFont", 10) > 2:
                            KF = "KoreanFont"
                            break
                    except Exception:
                        continue
            else:
                try:
                    pdfmetrics.registerFont(TTFont("KoreanFont", path))
                    KF = "KoreanFont"
                except Exception:
                    pass
            if KF:
                break
    if not KF:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
            KF = "HYGothic-Medium"
        except Exception:
            KF = "Helvetica"
    KF_BOLD = KF
    WON = "₩"

    items = data.get("items", [])
    customer = data.get("customer", "")
    default_rate = float(data.get("rate", 1400))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    margin_x = 28
    margin_top = 25
    uw = W - 2 * margin_x
    grey = Color(0.88, 0.88, 0.88)

    c.setLineWidth(0.8)

    # === 9열 컬럼 너비 (pt, 긴 텍스트 맞춤) ===
    # A(No.)/B(Part#)/C(QTY)/D(U/P$)/E(Amt$)/F(Rate)/G(U/P₩)/H(Amt₩)/I(비고)
    # Part# 14자 수용 + 요약 라벨 "공급가액 합계(₩)" 수용
    # 넉넉한 패딩 (모든 셀 최소 10pt 이상 여유, Part#는 24pt+ 여유)
    # Part#: 100pt (14char 76+24pad) / 19char Part#는 auto-shrink로 대응
    # 요약 라벨은 8pt 폰트로 안전 수용
    # U/P 컬럼은 5자리 소수점까지 수용 (예: $0.00645) — D, G 확장
    col_w_pt = [22, 90, 43, 78, 54, 50, 84, 66, 38]
    assert sum(col_w_pt) == 525, f"col sum {sum(col_w_pt)}"
    scale = uw / sum(col_w_pt)
    col_w = [w * scale for w in col_w_pt]
    x_bounds = [margin_x]
    for w in col_w:
        x_bounds.append(x_bounds[-1] + w)
    # x_bounds[0..9]

    # === 레이아웃 높이 계획 ===
    title_h = 55
    info_row_h = 18  # A4-A9 각 행 높이 축소
    box_header_h = 28
    box_body_h = info_row_h * 6  # 6 info lines → 108
    box_total_h = box_header_h + box_body_h  # 136
    gap_before_table = 18
    table_header_h = 24
    data_row_h = 19
    n_data = max(24, len(items))  # 양식은 24행 (row 14-37)
    summary_row_h = 22
    summary_total_h = summary_row_h * 3 + 10  # 3 rows + bottom pad

    # === 제목 영역 ===
    title_y = H - margin_top - title_h + 18  # 베이스라인
    c.setFont(KF_BOLD, 34)
    c.drawCentredString(W / 2, title_y, "거래명세서")

    # 발행일 / 담당자 (제목 아래 우측)
    issue_date = data.get("issue_date", "")
    person = data.get("person_in_charge", "")
    extra_offset = 0
    if issue_date or person:
        c.setFont(KF, 9)
        extra_y = title_y - 22
        if issue_date:
            c.drawRightString(margin_x + uw, extra_y, f"발행일 : {issue_date}")
            extra_y -= 12
            extra_offset = 12
        if person:
            c.drawRightString(margin_x + uw, extra_y, f"담당자 : {person}")
            extra_offset = (extra_offset + 12) if issue_date else 12

    # === 공급자 / 공급받는자 박스 ===
    box_top = H - margin_top - title_h - 10 - extra_offset
    # 공급자: 컬럼 A~D (x_bounds[0] → x_bounds[4])
    # 공급받는자: 컬럼 F~I (x_bounds[5] → x_bounds[9])
    # 컬럼 E는 공백 gap
    sup_x = x_bounds[0]
    sup_w = x_bounds[4] - x_bounds[0]
    cust_x = x_bounds[5]
    cust_w = x_bounds[9] - x_bounds[5]

    # 공급자 박스
    c.setFillColor(grey)
    c.rect(sup_x, box_top - box_header_h, sup_w, box_header_h, stroke=1, fill=1)
    c.setFillColor(black)
    c.rect(sup_x, box_top - box_total_h, sup_w, box_total_h, stroke=1, fill=0)
    c.line(sup_x, box_top - box_header_h, sup_x + sup_w, box_top - box_header_h)
    c.setFont(KF_BOLD, 12)
    c.drawCentredString(sup_x + sup_w / 2, box_top - box_header_h + 10, "공 급 자")

    # 공급받는자 박스
    c.setFillColor(grey)
    c.rect(cust_x, box_top - box_header_h, cust_w, box_header_h, stroke=1, fill=1)
    c.setFillColor(black)
    c.rect(cust_x, box_top - box_total_h, cust_w, box_total_h, stroke=1, fill=0)
    c.line(cust_x, box_top - box_header_h, cust_x + cust_w, box_top - box_header_h)
    c.setFont(KF_BOLD, 12)
    c.drawCentredString(cust_x + cust_w / 2, box_top - box_header_h + 10, "공급받는자")

    # 공급자 정보 (6줄, 박스 안에 정확히 맞춤)
    info = [
        "등록번호 : 229-81-00105",
        "상      호 : ㈜유니트론텍",
        "대표이사 : 남궁 선",
        "주      소 : 서울 강남구 영동대로 638(삼성동, 삼보빌딩 9층)",
        "업      태 : 도.소매",
        "종      목 : 전자부품 외",
    ]
    c.setFont(KF, 8.5)
    info_top_y = box_top - box_header_h - 12
    for i, line in enumerate(info):
        c.drawString(sup_x + 6, info_top_y - i * info_row_h, line)

    # 도장 (공급자 박스 우측 상단, 텍스트와 안 겹치게)
    stamp_path = os.path.join(os.path.dirname(__file__), "stamp.png")
    if os.path.exists(stamp_path):
        try:
            ss = 42
            c.drawImage(stamp_path, sup_x + sup_w - ss - 12, box_top - box_header_h - 8 - ss,
                        ss, ss, mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

    # 공급받는자 이름 (중앙)
    c.setFont(KF_BOLD, 12)
    cust_center_y = box_top - box_header_h - box_body_h / 2 - 4
    c.drawCentredString(cust_x + cust_w / 2, cust_center_y, customer)

    # === 품목 테이블 ===
    table_top = box_top - box_total_h - gap_before_table
    headers = ["No.", "Part #", "QTY", "U/PRICE ($)", "Amount ($)", "RATE", f"U/PRICE ({WON})", f"AMOUNT ({WON})", "비고"]

    # 헤더 행
    c.setFillColor(grey)
    c.rect(margin_x, table_top - table_header_h, uw, table_header_h, stroke=1, fill=1)
    c.setFillColor(black)
    c.setFont(KF_BOLD, 9.5)
    for i, h in enumerate(headers):
        c.drawCentredString((x_bounds[i] + x_bounds[i + 1]) / 2, table_top - table_header_h + 8, h)
        if i > 0:
            c.line(x_bounds[i], table_top, x_bounds[i], table_top - table_header_h)

    # 데이터 행
    total_usd = 0
    total_krw = 0
    total_qty = 0
    c.setFont(KF, 9)
    data_top = table_top - table_header_h
    for i in range(n_data):
        row_btm = data_top - data_row_h * (i + 1)
        c.rect(margin_x, row_btm, uw, data_row_h, stroke=1, fill=0)
        for j in range(1, 9):
            c.line(x_bounds[j], row_btm, x_bounds[j], row_btm + data_row_h)

        if i < len(items):
            item = items[i]
            qty = float(item.get("qty", 0))
            currency = (item.get("currency") or "USD").upper()
            note = str(item.get("date", "") or "")
            if currency == "KRW":
                # 원화만
                price_krw = float(item.get("price_krw", item.get("price", 0)))
                amount_krw = float(item.get("amount_krw", round(qty * price_krw, 0)))
                total_krw += amount_krw
                total_qty += qty
                vals = [
                    str(i + 1), str(item.get("part", "")), f"{int(qty):,}",
                    "", "", "",
                    _fmt_unit_price(price_krw, WON), f"{WON}{int(amount_krw):,}", note,
                ]
            elif item.get("rate"):
                # 환율 있음: 양쪽
                price = float(item.get("price", 0))
                item_rate = float(item.get("rate"))
                amount_usd = round(qty * price, 2)
                price_krw = round(price * item_rate, 2)
                amount_krw = round(amount_usd * item_rate, 0)
                total_usd += amount_usd
                total_krw += amount_krw
                total_qty += qty
                vals = [
                    str(i + 1), str(item.get("part", "")), f"{int(qty):,}",
                    _fmt_unit_price(price, "$"), f"${amount_usd:,.2f}", f"{item_rate:,.2f}",
                    _fmt_unit_price(price_krw, WON), f"{WON}{int(amount_krw):,}", note,
                ]
            else:
                # USD only
                price = float(item.get("price", 0))
                amount_usd = round(qty * price, 2)
                total_usd += amount_usd
                total_qty += qty
                vals = [
                    str(i + 1), str(item.get("part", "")), f"{int(qty):,}",
                    _fmt_unit_price(price, "$"), f"${amount_usd:,.2f}", "",
                    "", "", note,
                ]
        else:
            vals = ["", "", "", "", "", "", "", "", ""]
        # 모든 셀: 내용이 셀 넓이를 초과하면 자동 축소 — 절대 셀 벗어나지 않음
        for j, v in enumerate(vals):
            if not v:
                continue
            s = str(v)
            cell_w = col_w[j] - 6  # 양쪽 3pt 여백
            text_w = pdfmetrics.stringWidth(s, KF, 9)
            fs = 9
            if text_w > cell_w:
                fs = max(5.5, 9 * cell_w / text_w)
            c.setFont(KF, fs)
            if j == 1:  # Part# 좌측정렬
                c.drawString(x_bounds[j] + 3, row_btm + 6, s)
            else:
                c.drawCentredString((x_bounds[j] + x_bounds[j + 1]) / 2, row_btm + 6, s)
        c.setFont(KF, 9)

    # === 요약 영역 (양식 파일 row 38-40 구조) ===
    # 행 38: [소계 A:B병합] [C=qty] [D=$라벨] [E:F=$값 병합] [G=₩라벨] [H:I=₩값 병합]
    # 행 39: [비고 A:C병합 (2행높이)] [D=$부가세라벨] [E:F=$부가세값] [G=₩부가세라벨] [H:I=₩부가세값]
    # 행 40: [(A:C는 39와 병합)] [D=총금액$ 라벨] [E:F=총금액$ 값] [G=총금액₩ 라벨] [H:I=총금액₩ 값]
    sum_top = data_top - data_row_h * n_data
    tax_usd = round(total_usd * 0.1, 2)
    tax_krw = round(total_krw * 0.1, 0)
    total_usd_sum = total_usd + tax_usd
    total_krw_sum = total_krw + tax_krw

    r38_top = sum_top
    r38_btm = r38_top - summary_row_h
    r39_top = r38_btm
    r39_btm = r39_top - summary_row_h
    r40_top = r39_btm
    r40_btm = r40_top - summary_row_h

    # ------- 행 38 (소계) -------
    # A:B 병합 "소 계"
    c.rect(x_bounds[0], r38_btm, x_bounds[2] - x_bounds[0], summary_row_h, stroke=1, fill=0)
    # C (qty)
    c.rect(x_bounds[2], r38_btm, x_bounds[3] - x_bounds[2], summary_row_h, stroke=1, fill=0)
    # D ($라벨)
    c.rect(x_bounds[3], r38_btm, x_bounds[4] - x_bounds[3], summary_row_h, stroke=1, fill=0)
    # E:F 병합 ($값)
    c.rect(x_bounds[4], r38_btm, x_bounds[6] - x_bounds[4], summary_row_h, stroke=1, fill=0)
    # G (₩라벨)
    c.rect(x_bounds[6], r38_btm, x_bounds[7] - x_bounds[6], summary_row_h, stroke=1, fill=0)
    # H:I 병합 (₩값)
    c.rect(x_bounds[7], r38_btm, x_bounds[9] - x_bounds[7], summary_row_h, stroke=1, fill=0)

    # ------- 행 39+40 (비고 왼쪽은 세로 병합) -------
    # A:C 병합, rows 39+40 세로 병합 (한 큰 셀)
    c.rect(x_bounds[0], r40_btm, x_bounds[3] - x_bounds[0], summary_row_h * 2, stroke=1, fill=0)
    # 오른쪽: 행 39
    c.rect(x_bounds[3], r39_btm, x_bounds[4] - x_bounds[3], summary_row_h, stroke=1, fill=0)  # D
    c.rect(x_bounds[4], r39_btm, x_bounds[6] - x_bounds[4], summary_row_h, stroke=1, fill=0)  # E:F
    c.rect(x_bounds[6], r39_btm, x_bounds[7] - x_bounds[6], summary_row_h, stroke=1, fill=0)  # G
    c.rect(x_bounds[7], r39_btm, x_bounds[9] - x_bounds[7], summary_row_h, stroke=1, fill=0)  # H:I
    # 오른쪽: 행 40
    c.rect(x_bounds[3], r40_btm, x_bounds[4] - x_bounds[3], summary_row_h, stroke=1, fill=0)
    c.rect(x_bounds[4], r40_btm, x_bounds[6] - x_bounds[4], summary_row_h, stroke=1, fill=0)
    c.rect(x_bounds[6], r40_btm, x_bounds[7] - x_bounds[6], summary_row_h, stroke=1, fill=0)
    c.rect(x_bounds[7], r40_btm, x_bounds[9] - x_bounds[7], summary_row_h, stroke=1, fill=0)

    # === 요약 영역 텍스트 (auto-shrink로 셀 초과 방지) ===
    def draw_cell(text, left_x, right_x, y, base_fs=8, right_align=False):
        if not text:
            return
        cell_w = (right_x - left_x) - 6
        tw = pdfmetrics.stringWidth(text, KF, base_fs)
        fs = base_fs
        if tw > cell_w and tw > 0:
            fs = max(5.5, base_fs * cell_w / tw)
        c.setFont(KF, fs)
        if right_align:
            c.drawRightString(right_x - 4, y, text)
        else:
            c.drawCentredString((left_x + right_x) / 2, y, text)

    # 행 38
    draw_cell("소  계", x_bounds[0], x_bounds[2], r38_btm + 7)
    draw_cell(f"{int(total_qty):,}", x_bounds[2], x_bounds[3], r38_btm + 7)
    draw_cell("공급가액 합계($)", x_bounds[3], x_bounds[4], r38_btm + 7)
    draw_cell(f"${total_usd:,.2f}", x_bounds[4], x_bounds[6], r38_btm + 7)
    draw_cell(f"공급가액 합계({WON})", x_bounds[6], x_bounds[7], r38_btm + 7)
    draw_cell(f"{WON}{int(total_krw):,}", x_bounds[7], x_bounds[9], r38_btm + 7)
    # 행 39
    draw_cell("부가세($)", x_bounds[3], x_bounds[4], r39_btm + 7)
    draw_cell(f"${tax_usd:,.2f}", x_bounds[4], x_bounds[6], r39_btm + 7)
    draw_cell(f"부가세({WON})", x_bounds[6], x_bounds[7], r39_btm + 7)
    draw_cell(f"{WON}{int(tax_krw):,}", x_bounds[7], x_bounds[9], r39_btm + 7)
    # 행 40
    draw_cell("총 금액($)", x_bounds[3], x_bounds[4], r40_btm + 7)
    draw_cell(f"${total_usd_sum:,.2f}", x_bounds[4], x_bounds[6], r40_btm + 7)
    draw_cell(f"총 금액({WON})", x_bounds[6], x_bounds[7], r40_btm + 7)
    draw_cell(f"{WON}{int(total_krw_sum):,}", x_bounds[7], x_bounds[9], r40_btm + 7)
    # 비고 (병합된 왼쪽 셀 중앙)
    bigo_y = (r39_top + r40_btm) / 2 - 3
    bigo_text = "비 고 : 출고 일자, 최초매매기준율 기준"
    bigo_w = (x_bounds[3] - x_bounds[0]) - 12
    tw = pdfmetrics.stringWidth(bigo_text, KF, 9)
    bigo_fs = min(9, 9 * bigo_w / tw) if tw > bigo_w else 9
    c.setFont(KF, bigo_fs)
    c.drawString(x_bounds[0] + 6, bigo_y, bigo_text)

    c.showPage()
    c.save()
    return buf.getvalue()


@app.post("/api/invoice/generate-pdf")
async def generate_invoice_pdf(request: Request):
    try:
        data = await request.json()
        pdf_bytes = _build_invoice_pdf_bytes(data)
    except Exception as e:
        import traceback
        return {"error": f"PDF 생성 실패: {type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]}
    date_str = data.get("date", "")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{date_str}.pdf"},
    )


@app.post("/api/reset-tables")
async def reset_tables():
    ublox_tb.delete_all()
    sales_tb.delete_all()
    return {"status": "ok"}


# ==================== 5실 마이크로칩 백록 PRD 변동 비교 ====================

BACKLOG_SHEET_CANDIDATES = ["인풋", "마이크로칩백록(벤더발주)"]
BACKLOG_KEY_NAMES = ("SO#", "Mchp Sales Order #")  # 둘 중 어느 쪽이든 인정
BACKLOG_PRD = "PRD"
BACKLOG_HEADER_SCAN_LIMIT = 10  # 첫 10행 안에서 헤더 자동 탐지

BACKLOG_OUT_COLS = [
    "Mchp Catalog Part Number", "End Customer Name", "ODM/SubCon Name", "Customer PO#",
    "SO#", "Quote No.", "Qty Due", "Unit Price", "Amount Due",
    "ORD", "CRD", "PRD",
    "일정변동 현황", "변경전 일정", "변경일자",
    "업체명", "더존업체명코드",
]
BACKLOG_DATE_COLS = {"ORD", "CRD", "PRD", "변경전 일정"}


def _backlog_load(contents: bytes, fname: str):
    """파일 포맷 자동 인식 — 헤더 행을 첫 10행 안에서 탐지.

    지원 포맷:
      A) 시트 '인풋'                — 헤더 2행 (SO# / PRD)
      B) 시트 '마이크로칩백록(벤더발주)' — 헤더 3행 (Mchp Sales Order # / PRD)
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    sheet = None
    for c in BACKLOG_SHEET_CANDIDATES:
        if c in wb.sheetnames:
            sheet = c
            break
    if sheet is None:
        sheet = wb.sheetnames[0]
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # 헤더 행 자동 탐지: 키 컬럼명 + PRD 가 한 행에 모두 등장하는 첫 행
    header_idx = -1
    for i, row in enumerate(rows[:BACKLOG_HEADER_SCAN_LIMIT]):
        values = [str(c).strip() if c is not None else "" for c in row]
        has_key = any(k in values for k in BACKLOG_KEY_NAMES)
        has_prd = BACKLOG_PRD in values
        if has_key and has_prd:
            header_idx = i
            break
    if header_idx < 0:
        sample = [
            [str(c).strip() if c is not None else "" for c in r]
            for r in rows[:5]
        ]
        raise ValueError(
            f"{fname}: 헤더 행을 찾지 못했습니다 (시트='{sheet}'). "
            f"한 행 안에 'SO#' 또는 'Mchp Sales Order #' 와 'PRD' 가 함께 있어야 합니다. "
            f"확인한 첫 5행={sample}"
        )

    header = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    col_idx = {n: i for i, n in enumerate(header)}
    key_name = next((k for k in BACKLOG_KEY_NAMES if k in col_idx), None)

    by_key = {}
    for r in rows[header_idx + 1:]:
        k = r[col_idx[key_name]]
        if k is None or (isinstance(k, str) and not k.strip()):
            continue
        k = str(k).strip()
        if k not in by_key:
            by_key[k] = r

    return {
        "sheet": sheet,
        "all_rows": rows,                # 인풋 시트 원본 그대로 복사용
        "header_idx": header_idx,        # 0-indexed
        "header_row": rows[header_idx],
        "data_rows": rows[header_idx + 1:],
        "col_idx": col_idx,
        "by_key": by_key,
        "key_name": key_name,
    }


def _backlog_to_date(v):
    from datetime import datetime as _dt, date as _date
    if v is None:
        return None
    if isinstance(v, _dt):
        return v.date()
    if isinstance(v, _date):
        return v
    return None


def _backlog_get(row, col_idx, name, default=None):
    i = col_idx.get(name)
    if i is None or i >= len(row):
        return default
    return row[i]


def _backlog_build_changed(before: dict, after: dict):
    out = []
    b_idx, a_idx = before["col_idx"], after["col_idx"]
    for so, a_row in after["by_key"].items():
        b_row = before["by_key"].get(so)
        if b_row is None:
            continue
        bp = _backlog_get(b_row, b_idx, BACKLOG_PRD)
        ap = _backlog_get(a_row, a_idx, BACKLOG_PRD)
        bd = _backlog_to_date(bp)
        ad = _backlog_to_date(ap)
        if bd is None or ad is None:
            continue
        delta = (ad - bd).days
        if delta == 0:
            continue
        status = "PUSH-OUT" if delta > 0 else "PULL-IN"
        rec = {
            "Mchp Catalog Part Number": _backlog_get(a_row, a_idx, "PART#"),
            "End Customer Name": _backlog_get(a_row, a_idx, "End Customer Name"),
            "ODM/SubCon Name": _backlog_get(a_row, a_idx, "ODM/SubCon Name"),
            "Customer PO#": _backlog_get(a_row, a_idx, "Customer PO#"),
            "SO#": so,
            "Quote No.": _backlog_get(a_row, a_idx, "Quote No."),
            "Qty Due": _backlog_get(a_row, a_idx, "Qty Due"),
            "Unit Price": _backlog_get(a_row, a_idx, "Unit Price"),
            "Amount Due": _backlog_get(a_row, a_idx, "Amount Due"),
            "ORD": _backlog_get(a_row, a_idx, "ORD"),
            "CRD": _backlog_get(a_row, a_idx, "CRD"),
            "PRD": ap,
            "일정변동 현황": status,
            "변경전 일정": bp,
            "변경일자": abs(delta),
            "업체명": _backlog_get(a_row, a_idx, "업체명"),
            "더존업체명코드": _backlog_get(a_row, a_idx, "더존업체명코드"),
        }
        out.append(rec)
    out.sort(key=lambda r: (0 if r["일정변동 현황"] == "PUSH-OUT" else 1, -r["변경일자"]))
    return out


def _backlog_build_workbook(after: dict, changed: list):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from datetime import datetime as _dt, date as _date

    HDR_FILL = PatternFill("solid", fgColor="1F3A8A")
    HDR_FONT = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
    PUSH_FILL = PatternFill("solid", fgColor="FEE2E2")
    PULL_FILL = PatternFill("solid", fgColor="DCFCE7")
    NOTE_FONT = Font(name="맑은 고딕", italic=True, color="6B7280", size=9)
    DATA_FONT = Font(name="맑은 고딕", size=10)
    THIN = Side(border_style="thin", color="D1D5DB")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CENTER = Alignment(horizontal="center", vertical="center")
    LEFT = Alignment(horizontal="left", vertical="center")
    RIGHT = Alignment(horizontal="right", vertical="center")

    wb = Workbook()
    wb.remove(wb.active)

    # ---- 인풋 시트 (AFTER 원본 그대로 복사) ----
    ws_in = wb.create_sheet("인풋")
    header_excel_row = after["header_idx"] + 1  # 1-indexed
    for i, r in enumerate(after["all_rows"], start=1):
        for j, v in enumerate(r, start=1):
            c = ws_in.cell(i, j, v)
            if i == header_excel_row:
                c.fill = HDR_FILL
                c.font = HDR_FONT
                c.alignment = CENTER
                c.border = BORDER
            else:
                c.font = DATA_FONT
                c.border = BORDER
                if isinstance(v, (_dt, _date)):
                    c.number_format = "yyyy-mm-dd"
                    c.alignment = CENTER
                elif isinstance(v, (int, float)):
                    c.alignment = RIGHT
                else:
                    c.alignment = LEFT
    ws_in.row_dimensions[header_excel_row].height = 26
    ws_in.freeze_panes = ws_in.cell(header_excel_row + 1, 1).coordinate

    # ---- 아웃풋 시트 ----
    ws_out = wb.create_sheet("아웃풋")
    ws_out.cell(1, 13, '전일대비 일정이 줄었으면 "PULL-IN"으로 기재').font = NOTE_FONT
    ws_out.cell(2, 13, '전일대비 일정이 늘어났으면 "PUSH-OUT"으로 기재').font = NOTE_FONT
    ws_out.cell(2, 15, "L4-N4").font = NOTE_FONT

    for j, name in enumerate(BACKLOG_OUT_COLS, start=1):
        c = ws_out.cell(3, j, name)
        c.fill = HDR_FILL
        c.font = HDR_FONT
        c.alignment = CENTER
        c.border = BORDER
    ws_out.row_dimensions[3].height = 26
    ws_out.freeze_panes = "A4"

    for i, rec in enumerate(changed, start=4):
        status = rec.get("일정변동 현황")
        for j, name in enumerate(BACKLOG_OUT_COLS, start=1):
            v = rec.get(name)
            c = ws_out.cell(i, j, v)
            c.font = DATA_FONT
            c.border = BORDER
            if name in BACKLOG_DATE_COLS and isinstance(v, (_dt, _date)):
                c.number_format = "yyyy-mm-dd"
                c.alignment = CENTER
            elif name == "일정변동 현황":
                c.alignment = CENTER
                c.font = Font(name="맑은 고딕", bold=True, size=10)
                if v == "PUSH-OUT":
                    c.fill = PUSH_FILL
                elif v == "PULL-IN":
                    c.fill = PULL_FILL
            elif name == "변경일자":
                c.alignment = RIGHT
                if status == "PUSH-OUT":
                    c.fill = PUSH_FILL
                elif status == "PULL-IN":
                    c.fill = PULL_FILL
            elif isinstance(v, (int, float)):
                c.alignment = RIGHT
            else:
                c.alignment = LEFT

    # 컬럼 너비 자동
    for sheet in (ws_in, ws_out):
        for col in sheet.columns:
            col = list(col)
            if not col:
                continue
            letter = col[0].column_letter
            w = 10
            for c in col[:300]:
                v = c.value
                if v is None:
                    continue
                l = len(str(v))
                if l > w:
                    w = l
            sheet.column_dimensions[letter].width = min(40, w + 2)

    end_row = max(3, len(changed) + 3)
    ws_out.auto_filter.ref = f"A3:{get_column_letter(len(BACKLOG_OUT_COLS))}{end_row}"
    return wb


@app.post("/api/backlog/prd-diff/preview")
async def backlog_prd_diff_preview(before: UploadFile = File(...), after: UploadFile = File(...)):
    """JSON 미리보기 — 웹에서 표로 표시하기 위한 비교 결과."""
    from datetime import datetime as _dt, date as _date
    try:
        b_bytes = await before.read()
        a_bytes = await after.read()
        bd = _backlog_load(b_bytes, before.filename or "before.xlsx")
        ad = _backlog_load(a_bytes, after.filename or "after.xlsx")
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"파일 읽기 실패: {type(e).__name__}: {e}"}

    changed = _backlog_build_changed(bd, ad)

    def _ser(rec):
        out = {}
        for k, v in rec.items():
            if isinstance(v, (_dt, _date)):
                out[k] = v.strftime("%Y-%m-%d")
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                out[k] = None
            else:
                out[k] = v
        return out

    return {
        "columns": BACKLOG_OUT_COLS,
        "before_count": len(bd["by_key"]),
        "after_count": len(ad["by_key"]),
        "changed_count": len(changed),
        "push_out": sum(1 for r in changed if r["일정변동 현황"] == "PUSH-OUT"),
        "pull_in": sum(1 for r in changed if r["일정변동 현황"] == "PULL-IN"),
        "rows": [_ser(r) for r in changed],
    }


@app.post("/api/backlog/prd-diff")
async def backlog_prd_diff(before: UploadFile = File(...), after: UploadFile = File(...)):
    """엑셀 내보내기 — 인풋(AFTER 원본) + 아웃풋(변동분) 2시트 xlsx."""
    try:
        b_bytes = await before.read()
        a_bytes = await after.read()
        bd = _backlog_load(b_bytes, before.filename or "before.xlsx")
        ad = _backlog_load(a_bytes, after.filename or "after.xlsx")
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"파일 읽기 실패: {type(e).__name__}: {e}"}

    changed = _backlog_build_changed(bd, ad)
    wb = _backlog_build_workbook(ad, changed)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"마이크로칩 백록_PRD변동_{stamp}.xlsx"
    n_push = sum(1 for r in changed if r["일정변동 현황"] == "PUSH-OUT")
    n_pull = len(changed) - n_push
    from urllib.parse import quote
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
            "X-Before-Count": str(len(bd["by_key"])),
            "X-After-Count": str(len(ad["by_key"])),
            "X-Changed-Count": str(len(changed)),
            "X-Push-Out": str(n_push),
            "X-Pull-In": str(n_pull),
        },
    )


# ===== 단가(매입가) 마스터 — S3에 1회 저장해두고 재고분석에서 자동 적용 =====
PRICE_MASTER_BUCKET = os.environ.get("PRICE_MASTER_BUCKET", "elasticbeanstalk-ap-northeast-2-376798132745")
PRICE_MASTER_KEY = os.environ.get("PRICE_MASTER_KEY", "microchip-matching/price_master.json")


def _price_s3():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))


def _load_price_master() -> dict:
    """S3에서 단가 마스터 로드. 형식: {"prices": {"SR#||PART#": 단가}, "updated_at", "count"}."""
    try:
        obj = _price_s3().get_object(Bucket=PRICE_MASTER_BUCKET, Key=PRICE_MASTER_KEY)
        return json.loads(obj["Body"].read())
    except Exception:
        return {"prices": {}, "updated_at": None, "count": 0}


def _save_price_master(prices: dict) -> dict:
    payload = {"prices": prices, "updated_at": datetime.now().isoformat(timespec="seconds"),
               "count": len(prices)}
    _price_s3().put_object(
        Bucket=PRICE_MASTER_BUCKET, Key=PRICE_MASTER_KEY,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return payload


def _pm_norm_pn(v):
    s = str(v).strip()
    if s in (".", "-", "", "nan", "NaN", "None"):
        return ""
    return s.upper().replace(" ", "")


@app.post("/api/price-master/upload")
async def price_master_upload(file: UploadFile = File(...)):
    """단가 파일 업로드 → (SR#, Part#)→단가 추출해 S3에 저장. 이후 재고분석이 자동 사용."""
    contents = await file.read()
    try:
        xls = pd.ExcelFile(io.BytesIO(contents))
    except Exception as e:
        return {"error": f"엑셀 읽기 실패: {e}"}

    def fc(df, pats):
        for c in df.columns:
            cl = str(c).strip().lower()
            for p in pats:
                if p in cl:
                    return c
        return None

    # 시트별로 (구분=SR#, Part#, 단가) 컬럼 탐지 — 'Unit Price' 시트 우선(실측상 재고 매입가와 일치)
    best = None
    for name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=name)
        sr_c = fc(df, ["구분", "acc", "sr#", "sr "])
        pn_c = fc(df, ["product", "pn", "part"])
        price_c = None
        for c in df.columns:
            if "unit price" in str(c).strip().lower():
                price_c = c
                break
        score = 2
        if price_c is None:
            for c in df.columns:
                cl = str(c).strip().lower()
                if ("매입가" in cl or "단가" in cl or
                        ("price" in cl and "amt" not in cl and "amount" not in cl)):
                    price_c = c
                    score = 1
                    break
        if sr_c is not None and pn_c is not None and price_c is not None:
            if best is None or score > best[0]:
                best = (score, name, df, sr_c, pn_c, price_c)

    if not best:
        return {"error": "단가 시트에서 구분(SR#)/Part#/단가 컬럼을 찾지 못했습니다. "
                          f"시트: {xls.sheet_names}"}
    _, sheet, df, sr_c, pn_c, price_c = best
    prices = {}
    for _, r in df.iterrows():
        pn = _pm_norm_pn(r.get(pn_c))
        if not pn:
            continue
        sr = str(r.get(sr_c)).strip()
        if sr in (".", "-", "nan", "NaN", "None"):
            sr = ""
        pv = to_float(r.get(price_c))
        if pv is None or pv <= 0:
            continue
        prices[f"{sr}||{pn}"] = pv
    if not prices:
        return {"error": "단가 데이터를 추출하지 못했습니다."}
    saved = _save_price_master(prices)
    return {"saved": True, "count": saved["count"], "sheet": sheet,
            "sr_col": str(sr_c), "price_col": str(price_c), "updated_at": saved["updated_at"]}


@app.get("/api/price-master")
def price_master_status():
    m = _load_price_master()
    return {"count": m.get("count", 0), "updated_at": m.get("updated_at")}


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

        # 단가 마스터(S3) 로드 — 업로드 재고에 매입가가 없으면 (SR#,Part#)로 자동 채움
        pm = _load_price_master().get("prices", {})
        pm_by_pn = {}
        for k, v in pm.items():
            _pn = k.split("||", 1)[-1]
            pm_by_pn.setdefault(_pn, v)
        used_master = False

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
            # 단가: 업로드 파일의 매입가 우선, 없으면 단가 마스터(SR#+Part# → Part#) 사용
            pv = to_float(r.get(inv_price_col)) if inv_price_col is not None else None
            if pv is None or pv <= 0:
                pv = pm.get(f"{sr}||{pn}")
                if pv is None:
                    pv = pm_by_pn.get(pn)
                if pv is not None:
                    used_master = True
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
                "has_price": (inv_price_col is not None) or used_master or bool(pm),
                "price_source": ("파일" if inv_price_col is not None else
                                 ("단가마스터" if used_master else "없음")),
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



def _fs_norm(s):
    """업체명·파트명 정규화 — 공백·(주)·주식회사 제거 후 소문자화."""
    if s is None:
        return ""
    s = str(s).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("(주)", "").replace("(주)", "").replace("㈜", "").replace("주식회사", "")
    return s.lower()


def _fs_float(v):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _fs_clean(v):
    """JSON-safe float: NaN/Inf → None."""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
    return v


def _fs_decrypt(contents: bytes, passwords=("9671", "9176", "VelvetSweatshop", "")):
    """암호가 걸린 xlsx(OLE2 암호화 컨테이너)면 복호화해 평문 bytes 반환.
    일반(비암호) xlsx(ZIP, 'PK\\x03\\x04')는 그대로 반환.
    """
    # OLE2 compound-file 매직 = 암호화된 OOXML. 비암호 xlsx 는 ZIP 시그니처.
    if contents[:8] != b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        return contents
    try:
        import msoffcrypto
    except ImportError:
        raise ValueError("암호가 걸린 엑셀입니다. 서버에 'msoffcrypto-tool' 패키지 설치가 필요합니다.")
    last_err = None
    for pw in passwords:
        try:
            off = msoffcrypto.OfficeFile(io.BytesIO(contents))
            off.load_key(password=pw)
            out = io.BytesIO()
            off.decrypt(out)
            return out.getvalue()
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"암호 해제 실패(비밀번호 확인 필요): {last_err}")


def _fs_load_actuals(contents: bytes):
    """실적 엑셀 파싱.
    헤더 예: Month | 계산서발행일자 | Vendor | 담당자 | Customer | 거래처코드 | MPN | QTY | BP($) | SP($)
    (구 항목명: 출고일자 / 매입가(BC) / 매출가(RS) 도 그대로 인식)
    aggregate by (Customer, MPN), sum(QTY × SP($)).
    """
    import openpyxl as _xl
    contents = _fs_decrypt(contents)
    bio = io.BytesIO(contents)
    wb = _xl.load_workbook(bio, data_only=True)

    # 활성 탭(wb.active)만 보지 않고 모든 시트를 스캔해 데이터 헤더가 있는 시트를 고른다.
    # SUMMARY / Sheet1 같은 요약·피벗 시트가 있어도(또는 그게 활성 탭이어도) 에러 없이 통과.
    ws = None
    header_row = None
    for sheet in wb.worksheets:
        for ri in range(1, min(12, sheet.max_row + 1)):
            vals = [str(c.value).strip() if c.value is not None else "" for c in sheet[ri]]
            if "Customer" in vals and "MPN" in vals and "QTY" in vals:
                ws = sheet
                header_row = ri
                break
        if ws is not None:
            break
    if ws is None:
        raise ValueError("실적 파일에서 헤더(Customer/MPN/QTY)가 있는 시트를 찾지 못했습니다. (SUMMARY 등 요약 시트만 있는지 확인)")

    hdr = [str(c.value).strip() if c.value is not None else "" for c in ws[header_row]]

    def col_of(name, required=True):
        if name in hdr:
            return hdr.index(name)
        if required:
            raise ValueError(f"실적 파일 헤더에 '{name}' 컬럼이 없습니다.")
        return -1

    idx_cust = col_of("Customer")
    idx_mpn = col_of("MPN")
    idx_qty = col_of("QTY")
    idx_owner = col_of("담당자", required=False)
    idx_month = col_of("Month", required=False)
    idx_rs = -1
    for i, h in enumerate(hdr):
        hn = h.replace(" ", "").upper()
        if "매출가" in h or h == "RS" or hn in ("SP($)", "SP", "SP$"):
            idx_rs = i
            break
    if idx_rs == -1:
        raise ValueError("실적 파일에 '매출가(RS)' 또는 'SP($)' 컬럼이 없습니다.")

    agg = {}
    for r in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if not r:
            continue
        cust = r[idx_cust] if idx_cust < len(r) else None
        mpn = r[idx_mpn] if idx_mpn < len(r) else None
        if not cust or not mpn:
            continue
        qty = _fs_float(r[idx_qty] if idx_qty < len(r) else 0)
        price = _fs_float(r[idx_rs] if idx_rs < len(r) else 0)
        owner = r[idx_owner] if 0 <= idx_owner < len(r) else None
        month = r[idx_month] if 0 <= idx_month < len(r) else None
        key = (_fs_norm(cust), _fs_norm(mpn))
        if key not in agg:
            agg[key] = {"cust": cust, "mpn": mpn, "owner": owner,
                        "qty": 0.0, "rs_amt": 0.0, "lines": 0, "month": month}
        agg[key]["qty"] += qty
        agg[key]["rs_amt"] += qty * price
        agg[key]["lines"] += 1

    months = sorted({a["month"] for a in agg.values() if a["month"] is not None})
    return {"by_key": agg, "months": months, "sheet": ws.title, "rows": len(agg)}


_FCST_SHEET = "Sales Revenue"


def _fs_load_fcst(contents: bytes):
    """FCST 엑셀의 'Sales Revenue' 시트 파싱.
    row1: 월 라벨(May/June/July...)이 각 블록 시작 열에.
    row3: 담당자|VENDOR|VENDOR2|Customer|MPN|증감사유|BC|RS|Q'ty|BC AMT|RS AMT|GP × N개월
    """
    import openpyxl as _xl
    contents = _fs_decrypt(contents)
    bio = io.BytesIO(contents)
    wb = _xl.load_workbook(bio, data_only=True)
    # 'Sales Revenue' 시트 — 대소문자·공백 무시, 부분일치 허용
    target = None
    for s in wb.sheetnames:
        sn = str(s).strip().lower()
        if sn == _FCST_SHEET.lower() or "sales revenue" in sn or sn == "revenue":
            target = s
            break
    if target is None:
        raise ValueError(f"'{_FCST_SHEET}' 시트가 없습니다. 시트 목록: {wb.sheetnames}")
    ws = wb[target]

    row1 = [c.value for c in ws[1]]
    row3 = [c.value for c in ws[3]]

    months = []
    for i, v in enumerate(row1):
        if v and isinstance(v, str) and v.strip():
            months.append((v.strip(), i))
    if not months:
        raise ValueError("'Sales Revenue' 시트 row1에서 월 라벨을 찾지 못했습니다.")

    month_blocks = []
    for mi, (label, start) in enumerate(months):
        end = months[mi + 1][1] if mi + 1 < len(months) else len(row3)
        block = {"label": label, "start": start, "end": end, "q_ty": -1, "rs_amt": -1, "gp": -1}
        for ci in range(start, end):
            h = row3[ci] if ci < len(row3) else None
            if not h:
                continue
            hs = str(h).strip()
            if hs == "Q'ty" or hs.lower() == "q'ty" or hs == "Qty":
                block["q_ty"] = ci
            elif hs == "RS AMT":
                block["rs_amt"] = ci
            elif hs == "GP":
                block["gp"] = ci
        month_blocks.append(block)

    static = {}
    for i, h in enumerate(row3):
        if not h:
            continue
        hs = str(h).strip()
        if hs == "담당자":
            static["owner"] = i
        elif hs == "Customer":
            static["cust"] = i
        elif hs == "MPN":
            static["mpn"] = i

    if "cust" not in static or "mpn" not in static:
        raise ValueError("FCST 'Sales Revenue' 시트 row3에 Customer/MPN 컬럼이 없습니다.")

    by_key = {}
    for r in ws.iter_rows(min_row=4, values_only=True):
        if not r:
            continue
        cust = r[static["cust"]] if static["cust"] < len(r) else None
        mpn = r[static["mpn"]] if static["mpn"] < len(r) else None
        if not cust or not mpn:
            continue
        owner = r[static["owner"]] if "owner" in static and static["owner"] < len(r) else None
        key = (_fs_norm(cust), _fs_norm(mpn))
        rec = by_key.get(key)
        if rec is None:
            rec = {"cust": cust, "mpn": mpn, "owner": owner, "months": {}}
            by_key[key] = rec
        for mb in month_blocks:
            qty = _fs_float(r[mb["q_ty"]] if 0 <= mb["q_ty"] < len(r) else 0)
            amt = _fs_float(r[mb["rs_amt"]] if 0 <= mb["rs_amt"] < len(r) else 0)
            gp = _fs_float(r[mb["gp"]] if 0 <= mb["gp"] < len(r) else 0)
            m = rec["months"].setdefault(mb["label"], {"qty": 0.0, "rs_amt": 0.0, "gp": 0.0})
            m["qty"] += qty
            m["rs_amt"] += amt
            m["gp"] += gp

    return {"by_key": by_key, "months": [mb["label"] for mb in month_blocks]}


# 월 번호 → 영문 월 이름 (FCST 라벨 'May'/'June' 등과 매칭용)
_KR_MONTH_MAP = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def _fs_target_month(actuals, fcst_months):
    """실적 Month(예: 202605)에서 FCST 라벨(May) 찾기."""
    if not fcst_months:
        return None
    if not actuals["months"]:
        return fcst_months[0]
    try:
        mi = int(actuals["months"][0]) % 100
    except (TypeError, ValueError):
        return fcst_months[0]
    target = _KR_MONTH_MAP.get(mi, "")
    for fm in fcst_months:
        if fm.lower().startswith(target.lower()[:3]):
            return fm
    return fcst_months[0]


def _fs_classify(ach, has_actual, has_fcst, f_amt):
    if has_actual and not has_fcst:
        return "매칭누락"
    if has_fcst and not has_actual:
        return "미실현"
    if f_amt == 0:
        return "FCST=0"
    if ach is None:
        return "—"
    if ach < 80:
        return "미달"
    if ach < 100:
        return "근접"
    if ach <= 120:
        return "달성"
    return "초과"


def _fs_compare(actuals, fcst):
    target = _fs_target_month(actuals, fcst["months"])
    fcst_months = fcst["months"]

    rows = []
    matched = only_a = only_f = 0
    all_keys = set(actuals["by_key"]) | set(fcst["by_key"])
    for key in all_keys:
        a = actuals["by_key"].get(key)
        f = fcst["by_key"].get(key)
        if a and f:
            matched += 1
        elif a:
            only_a += 1
        else:
            only_f += 1

        f_target = (f["months"].get(target, {"qty": 0, "rs_amt": 0}) if f else {"qty": 0, "rs_amt": 0})
        actual_amt = a["rs_amt"] if a else 0.0
        actual_qty = a["qty"] if a else 0.0
        f_amt = f_target["rs_amt"]
        f_qty = f_target["qty"]

        ach = (actual_amt / f_amt * 100) if f_amt else None
        status = _fs_classify(ach, a is not None, f is not None, f_amt)

        owner = (a["owner"] if a and a.get("owner") else (f["owner"] if f else None))
        cust = a["cust"] if a else f["cust"]
        mpn = a["mpn"] if a else f["mpn"]

        row = {
            "담당자": owner,
            "Customer": cust,
            "MPN": mpn,
            "FCST_Qty": round(f_qty, 2),
            "FCST_RS_AMT": round(f_amt, 2),
            "실제_Qty": round(actual_qty, 2),
            "실제_RS_AMT": round(actual_amt, 2),
            "달성률": round(ach, 1) if ach is not None else None,
            "GAP": round(actual_amt - f_amt, 2),
            "상태": status,
        }
        for fm in fcst_months:
            if fm == target:
                continue
            md = (f["months"].get(fm, {"qty": 0, "rs_amt": 0}) if f else {"qty": 0, "rs_amt": 0})
            row[f"FCST_{fm}_Qty"] = round(md["qty"], 2)
            row[f"FCST_{fm}_RS_AMT"] = round(md["rs_amt"], 2)
        rows.append(row)

    rows.sort(key=lambda r: abs(r["GAP"] or 0), reverse=True)

    total_f = sum(r["FCST_RS_AMT"] for r in rows)
    total_a = sum(r["실제_RS_AMT"] for r in rows)
    total_ach = (total_a / total_f * 100) if total_f else None

    counts = {"달성": 0, "근접": 0, "미달": 0, "초과": 0, "매칭누락": 0, "미실현": 0, "FCST=0": 0, "—": 0}
    for r in rows:
        counts[r["상태"]] = counts.get(r["상태"], 0) + 1

    owners = {}
    for r in rows:
        o = r["담당자"] or "(미지정)"
        d = owners.setdefault(o, {"owner": o, "fcst": 0.0, "actual": 0.0, "items": 0, "miss": 0})
        d["fcst"] += r["FCST_RS_AMT"]
        d["actual"] += r["실제_RS_AMT"]
        d["items"] += 1
        if r["상태"] == "미달":
            d["miss"] += 1
    for d in owners.values():
        d["fcst"] = round(d["fcst"], 2)
        d["actual"] = round(d["actual"], 2)
        d["GAP"] = round(d["actual"] - d["fcst"], 2)
        d["달성률"] = round(d["actual"] / d["fcst"] * 100, 1) if d["fcst"] else None
    owners_list = sorted(owners.values(), key=lambda x: x["fcst"], reverse=True)

    custs = {}
    for r in rows:
        c = r["Customer"] or "(미상)"
        d = custs.setdefault(c, {"customer": c, "fcst": 0.0, "actual": 0.0, "items": 0})
        d["fcst"] += r["FCST_RS_AMT"]
        d["actual"] += r["실제_RS_AMT"]
        d["items"] += 1
    for d in custs.values():
        d["fcst"] = round(d["fcst"], 2)
        d["actual"] = round(d["actual"], 2)
        d["GAP"] = round(d["actual"] - d["fcst"], 2)
        d["달성률"] = round(d["actual"] / d["fcst"] * 100, 1) if d["fcst"] else None
    top_customers = sorted(custs.values(), key=lambda x: abs(x["GAP"]), reverse=True)[:10]

    return {
        "target_month": target,
        "fcst_months": fcst_months,
        "actuals_month_code": actuals["months"][0] if actuals["months"] else None,
        "kpi": {
            "total_fcst": round(total_f, 2),
            "total_actual": round(total_a, 2),
            "total_gap": round(total_a - total_f, 2),
            "달성률": round(total_ach, 1) if total_ach is not None else None,
            "matched": matched,
            "only_actual": only_a,
            "only_fcst": only_f,
            "counts": counts,
        },
        "owners": owners_list,
        "top_customers": top_customers,
        "rows": rows,
    }


@app.post("/api/fcst-sales/preview")
async def fcst_sales_preview(fcst: UploadFile = File(...), actual: UploadFile = File(...)):
    """영업FCST vs 실제 매출 비교 (5실) — JSON 미리보기.
    fcst: FCST 엑셀 (시트 'Sales Revenue', 헤더 3행, 3개월 블록).
    actual: 실적 엑셀 (단일 시트, 헤더 2행, Customer/MPN/QTY/매출가(RS)).
    """
    try:
        a_bytes = await actual.read()
        f_bytes = await fcst.read()
        actuals = _fs_load_actuals(a_bytes)
        fc = _fs_load_fcst(f_bytes)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        import traceback
        return {"error": f"파일 읽기 실패: {type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-1200:]}
    return _fs_compare(actuals, fc)


@app.post("/api/fcst-sales/export")
async def fcst_sales_export(fcst: UploadFile = File(...), actual: UploadFile = File(...)):
    """비교 결과를 엑셀로 (Summary + Detail 2시트)."""
    try:
        a_bytes = await actual.read()
        f_bytes = await fcst.read()
        actuals = _fs_load_actuals(a_bytes)
        fc = _fs_load_fcst(f_bytes)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"파일 읽기 실패: {type(e).__name__}: {e}"}

    result = _fs_compare(actuals, fc)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    BOLD_W = Font(bold=True, color="FFFFFF")
    HDR_FILL = PatternFill("solid", fgColor="1F3A8A")
    CENTER = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    ws_s = wb.active
    ws_s.title = "Summary"
    ws_s["A1"] = "영업FCST vs 실제매출 — Summary"
    ws_s["A1"].font = Font(bold=True, size=14)
    ws_s.merge_cells("A1:G1")

    kpi = result["kpi"]
    pairs = [
        ("기준 월 (FCST)", result["target_month"]),
        ("실적 Month 코드", result["actuals_month_code"]),
        ("FCST 총액", kpi["total_fcst"]),
        ("실제 총액", kpi["total_actual"]),
        ("GAP (실제−FCST)", kpi["total_gap"]),
        ("달성률 %", kpi["달성률"]),
        ("매칭됨", kpi["matched"]),
        ("실적만 (FCST 누락)", kpi["only_actual"]),
        ("FCST만 (실적 없음)", kpi["only_fcst"]),
    ]
    for i, (k, v) in enumerate(pairs, 3):
        ws_s.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws_s.cell(row=i, column=2, value=v)

    row_owner = len(pairs) + 5
    ws_s.cell(row=row_owner, column=1, value="담당자별").font = Font(bold=True, size=12)
    ohdr = ["담당자", "FCST", "실제", "GAP", "달성률 %", "건수", "미달건수"]
    for j, h in enumerate(ohdr, 1):
        c = ws_s.cell(row=row_owner + 1, column=j, value=h)
        c.font = BOLD_W
        c.fill = HDR_FILL
        c.alignment = CENTER
    for i, o in enumerate(result["owners"], row_owner + 2):
        ws_s.cell(row=i, column=1, value=o["owner"])
        ws_s.cell(row=i, column=2, value=o["fcst"])
        ws_s.cell(row=i, column=3, value=o["actual"])
        ws_s.cell(row=i, column=4, value=o["GAP"])
        ws_s.cell(row=i, column=5, value=o["달성률"])
        ws_s.cell(row=i, column=6, value=o["items"])
        ws_s.cell(row=i, column=7, value=o["miss"])

    row_cust = row_owner + 2 + len(result["owners"]) + 2
    ws_s.cell(row=row_cust, column=1, value="Customer Top 10 (절대 GAP 기준)").font = Font(bold=True, size=12)
    chdr = ["Customer", "FCST", "실제", "GAP", "달성률 %", "건수"]
    for j, h in enumerate(chdr, 1):
        c = ws_s.cell(row=row_cust + 1, column=j, value=h)
        c.font = BOLD_W
        c.fill = HDR_FILL
        c.alignment = CENTER
    for i, cu in enumerate(result["top_customers"], row_cust + 2):
        ws_s.cell(row=i, column=1, value=cu["customer"])
        ws_s.cell(row=i, column=2, value=cu["fcst"])
        ws_s.cell(row=i, column=3, value=cu["actual"])
        ws_s.cell(row=i, column=4, value=cu["GAP"])
        ws_s.cell(row=i, column=5, value=cu["달성률"])
        ws_s.cell(row=i, column=6, value=cu["items"])

    # Detail
    ws_d = wb.create_sheet("Detail")
    fixed = ["담당자", "Customer", "MPN", "FCST_Qty", "FCST_RS_AMT",
             "실제_Qty", "실제_RS_AMT", "달성률", "GAP", "상태"]
    forward = [m for m in result["fcst_months"] if m != result["target_month"]]
    cols = fixed + [c for m in forward for c in (f"FCST_{m}_Qty", f"FCST_{m}_RS_AMT")]
    for j, h in enumerate(cols, 1):
        c = ws_d.cell(row=1, column=j, value=h)
        c.font = BOLD_W
        c.fill = HDR_FILL
        c.alignment = CENTER
    for i, r in enumerate(result["rows"], 2):
        for j, k in enumerate(cols, 1):
            ws_d.cell(row=i, column=j, value=r.get(k))

    for sh in (ws_s, ws_d):
        for col in sh.columns:
            try:
                letter = col[0].column_letter
            except AttributeError:
                continue
            w = 10
            for c in col[:300]:
                if c.value is None:
                    continue
                lv = len(str(c.value))
                if lv > w:
                    w = lv
            sh.column_dimensions[letter].width = min(42, w + 2)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"영업FCST_매출비교_{stamp}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
            "X-Matched": str(result["kpi"]["matched"]),
            "X-Only-Actual": str(result["kpi"]["only_actual"]),
            "X-Only-Fcst": str(result["kpi"]["only_fcst"]),
        },
    )


# ==================== FCST 주간 변동 (전주 vs 금주) ====================

def _fs_drift_compare(prev_fcst, curr_fcst):
    """전주 FCST vs 금주 FCST 비교.
    동일 (Customer, MPN) 기준으로 각 월의 RS AMT 변동 계산.
    """
    all_keys = set(prev_fcst["by_key"]) | set(curr_fcst["by_key"])
    # 금주 기준 월 사용 — 새 월이 추가되거나 빠졌을 수도 있으니 union 으로 가도 되지만
    # 현실적으로 동일 분기/연도 유지되므로 금주 기준이 합당.
    months = curr_fcst["months"] or prev_fcst["months"]

    rows = []
    monthly = {m: {"prev": 0.0, "curr": 0.0} for m in months}
    gp_monthly = {m: {"prev": 0.0, "curr": 0.0} for m in months}

    for key in all_keys:
        p = prev_fcst["by_key"].get(key)
        c = curr_fcst["by_key"].get(key)
        owner = (c["owner"] if c and c.get("owner") else (p["owner"] if p else None))
        cust = c["cust"] if c else p["cust"]
        mpn = c["mpn"] if c else p["mpn"]

        prev_total = sum((p["months"].get(m, {"rs_amt": 0})["rs_amt"] for m in months)) if p else 0.0
        curr_total = sum((c["months"].get(m, {"rs_amt": 0})["rs_amt"] for m in months)) if c else 0.0
        delta = curr_total - prev_total

        prev_gp = sum(((p["months"].get(m) or {}).get("gp", 0) for m in months)) if p else 0.0
        curr_gp = sum(((c["months"].get(m) or {}).get("gp", 0) for m in months)) if c else 0.0
        gp_delta = curr_gp - prev_gp

        if not p and c:
            status = "신규"
        elif p and not c:
            status = "제거"
        elif abs(delta) < 0.01:
            status = "무변동"
        elif delta > 0:
            status = "증가"
        else:
            status = "감소"

        delta_pct = None
        if prev_total:
            delta_pct = round(delta / prev_total * 100, 1)
        elif curr_total:
            delta_pct = None  # 신규는 % 표기 불가

        row = {
            "담당자": owner, "Customer": cust, "MPN": mpn,
            "전주합계": round(prev_total, 2),
            "금주합계": round(curr_total, 2),
            "△": round(delta, 2),
            "△%": delta_pct,
            "GP전주": round(prev_gp, 2),
            "GP금주": round(curr_gp, 2),
            "GP△": round(gp_delta, 2),
            "상태": status,
        }
        for m in months:
            p_m = (p["months"].get(m) or {}) if p else {}
            c_m = (c["months"].get(m) or {}) if c else {}
            p_rs, c_rs = p_m.get("rs_amt", 0), c_m.get("rs_amt", 0)
            p_gp, c_gp = p_m.get("gp", 0), c_m.get("gp", 0)
            row[f"{m}_prev"] = round(p_rs, 2)
            row[f"{m}_curr"] = round(c_rs, 2)
            row[f"{m}_△"] = round(c_rs - p_rs, 2)
            row[f"{m}_gp_△"] = round(c_gp - p_gp, 2)
            monthly[m]["prev"] += p_rs
            monthly[m]["curr"] += c_rs
            gp_monthly[m]["prev"] += p_gp
            gp_monthly[m]["curr"] += c_gp
        rows.append(row)

    rows.sort(key=lambda r: abs(r["△"]), reverse=True)

    monthly_kpi = []
    for m in months:
        prev_v = monthly[m]["prev"]
        curr_v = monthly[m]["curr"]
        d = curr_v - prev_v
        dp = round(d / prev_v * 100, 1) if prev_v else None
        gp_prev_v = gp_monthly[m]["prev"]
        gp_curr_v = gp_monthly[m]["curr"]
        gp_d = gp_curr_v - gp_prev_v
        monthly_kpi.append({
            "month": m,
            "prev": round(prev_v, 2),
            "curr": round(curr_v, 2),
            "delta": round(d, 2),
            "delta_pct": dp,
            "gp_prev": round(gp_prev_v, 2),
            "gp_curr": round(gp_curr_v, 2),
            "gp_delta": round(gp_d, 2),
        })

    total_prev = sum(mk["prev"] for mk in monthly_kpi)
    total_curr = sum(mk["curr"] for mk in monthly_kpi)
    total_delta = total_curr - total_prev
    total_pct = round(total_delta / total_prev * 100, 1) if total_prev else None

    gp_total_prev = sum(mk["gp_prev"] for mk in monthly_kpi)
    gp_total_curr = sum(mk["gp_curr"] for mk in monthly_kpi)
    gp_total_delta = gp_total_curr - gp_total_prev
    gp_total_pct = round(gp_total_delta / gp_total_prev * 100, 1) if gp_total_prev else None

    counts = {"증가": 0, "감소": 0, "신규": 0, "제거": 0, "무변동": 0}
    for r in rows:
        counts[r["상태"]] = counts.get(r["상태"], 0) + 1

    owners = {}
    for r in rows:
        o = r["담당자"] or "(미지정)"
        d = owners.setdefault(o, {
            "owner": o, "prev": 0.0, "curr": 0.0, "gp_prev": 0.0, "gp_curr": 0.0, "items": 0,
            "increased": 0, "decreased": 0, "new": 0, "removed": 0, "unchanged": 0,
        })
        d["prev"] += r["전주합계"]
        d["curr"] += r["금주합계"]
        d["gp_prev"] += r["GP전주"]
        d["gp_curr"] += r["GP금주"]
        d["items"] += 1
        s = r["상태"]
        if s == "증가": d["increased"] += 1
        elif s == "감소": d["decreased"] += 1
        elif s == "신규": d["new"] += 1
        elif s == "제거": d["removed"] += 1
        else: d["unchanged"] += 1
    for d in owners.values():
        d["prev"] = round(d["prev"], 2)
        d["curr"] = round(d["curr"], 2)
        d["delta"] = round(d["curr"] - d["prev"], 2)
        d["delta_pct"] = round(d["delta"] / d["prev"] * 100, 1) if d["prev"] else None
        d["gp_prev"] = round(d["gp_prev"], 2)
        d["gp_curr"] = round(d["gp_curr"], 2)
        d["gp_delta"] = round(d["gp_curr"] - d["gp_prev"], 2)
    owners_list = sorted(owners.values(), key=lambda x: abs(x["delta"]), reverse=True)

    return {
        "months": months,
        "kpi": {
            "monthly": monthly_kpi,
            "total_prev": round(total_prev, 2),
            "total_curr": round(total_curr, 2),
            "total_delta": round(total_delta, 2),
            "total_delta_pct": total_pct,
            "gp_total_prev": round(gp_total_prev, 2),
            "gp_total_curr": round(gp_total_curr, 2),
            "gp_total_delta": round(gp_total_delta, 2),
            "gp_total_delta_pct": gp_total_pct,
            "counts": counts,
        },
        "owners": owners_list,
        "top_movers": rows[:10],
        "rows": rows,
    }


@app.post("/api/fcst-drift/preview")
async def fcst_drift_preview(prev: UploadFile = File(...), curr: UploadFile = File(...)):
    """전주 FCST vs 금주 FCST 비교 (JSON).
    prev: 지난주 FCST 엑셀
    curr: 이번주 FCST 엑셀
    둘 다 'Sales Revenue' 시트 보유 (헤더 3행 · 3개월 블록).
    """
    try:
        p_bytes = await prev.read()
        c_bytes = await curr.read()
        prev_fc = _fs_load_fcst(p_bytes)
        curr_fc = _fs_load_fcst(c_bytes)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        import traceback
        return {"error": f"파일 읽기 실패: {type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-1200:]}
    return _fs_drift_compare(prev_fc, curr_fc)


@app.post("/api/fcst-drift/export")
async def fcst_drift_export(prev: UploadFile = File(...), curr: UploadFile = File(...)):
    """FCST 주간 변동 — Summary + Detail 엑셀."""
    try:
        p_bytes = await prev.read()
        c_bytes = await curr.read()
        prev_fc = _fs_load_fcst(p_bytes)
        curr_fc = _fs_load_fcst(c_bytes)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"파일 읽기 실패: {type(e).__name__}: {e}"}

    result = _fs_drift_compare(prev_fc, curr_fc)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    BOLD_W = Font(bold=True, color="FFFFFF")
    HDR_FILL = PatternFill("solid", fgColor="1E293B")
    CENTER = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    ws_s = wb.active
    ws_s.title = "Summary"
    ws_s["A1"] = "FCST 주간 변동 — Summary"
    ws_s["A1"].font = Font(bold=True, size=14)
    ws_s.merge_cells("A1:F1")

    kpi = result["kpi"]
    # 월별
    ws_s["A3"] = "월별 변동"; ws_s["A3"].font = Font(bold=True, size=12)
    mhdr = ["월", "전주 RS AMT", "금주 RS AMT", "△", "△ %", "전주 GP", "금주 GP", "GP △"]
    for j, h in enumerate(mhdr, 1):
        c = ws_s.cell(row=4, column=j, value=h); c.font = BOLD_W; c.fill = HDR_FILL; c.alignment = CENTER
    for i, mk in enumerate(kpi["monthly"], 5):
        ws_s.cell(row=i, column=1, value=mk["month"])
        ws_s.cell(row=i, column=2, value=mk["prev"])
        ws_s.cell(row=i, column=3, value=mk["curr"])
        ws_s.cell(row=i, column=4, value=mk["delta"])
        ws_s.cell(row=i, column=5, value=mk["delta_pct"])
        ws_s.cell(row=i, column=6, value=mk["gp_prev"])
        ws_s.cell(row=i, column=7, value=mk["gp_curr"])
        ws_s.cell(row=i, column=8, value=mk["gp_delta"])
    row_total = 5 + len(kpi["monthly"])
    ws_s.cell(row=row_total, column=1, value="합계").font = Font(bold=True)
    ws_s.cell(row=row_total, column=2, value=kpi["total_prev"]).font = Font(bold=True)
    ws_s.cell(row=row_total, column=3, value=kpi["total_curr"]).font = Font(bold=True)
    ws_s.cell(row=row_total, column=4, value=kpi["total_delta"]).font = Font(bold=True)
    ws_s.cell(row=row_total, column=5, value=kpi["total_delta_pct"]).font = Font(bold=True)
    ws_s.cell(row=row_total, column=6, value=kpi["gp_total_prev"]).font = Font(bold=True)
    ws_s.cell(row=row_total, column=7, value=kpi["gp_total_curr"]).font = Font(bold=True)
    ws_s.cell(row=row_total, column=8, value=kpi["gp_total_delta"]).font = Font(bold=True)

    # 상태 분포
    row_status = row_total + 3
    ws_s.cell(row=row_status, column=1, value="상태 분포").font = Font(bold=True, size=12)
    for i, (k, v) in enumerate(kpi["counts"].items(), row_status + 1):
        ws_s.cell(row=i, column=1, value=k)
        ws_s.cell(row=i, column=2, value=v)

    # 담당자별
    row_owner = row_status + 2 + len(kpi["counts"]) + 2
    ws_s.cell(row=row_owner, column=1, value="담당자별 변동").font = Font(bold=True, size=12)
    ohdr = ["담당자", "전주", "금주", "△", "△ %", "건수", "증가", "감소", "신규", "제거", "전주 GP", "금주 GP", "GP △"]
    for j, h in enumerate(ohdr, 1):
        c = ws_s.cell(row=row_owner + 1, column=j, value=h); c.font = BOLD_W; c.fill = HDR_FILL; c.alignment = CENTER
    for i, o in enumerate(result["owners"], row_owner + 2):
        ws_s.cell(row=i, column=1, value=o["owner"])
        ws_s.cell(row=i, column=2, value=o["prev"])
        ws_s.cell(row=i, column=3, value=o["curr"])
        ws_s.cell(row=i, column=4, value=o["delta"])
        ws_s.cell(row=i, column=5, value=o["delta_pct"])
        ws_s.cell(row=i, column=6, value=o["items"])
        ws_s.cell(row=i, column=7, value=o["increased"])
        ws_s.cell(row=i, column=8, value=o["decreased"])
        ws_s.cell(row=i, column=9, value=o["new"])
        ws_s.cell(row=i, column=10, value=o["removed"])
        ws_s.cell(row=i, column=11, value=o["gp_prev"])
        ws_s.cell(row=i, column=12, value=o["gp_curr"])
        ws_s.cell(row=i, column=13, value=o["gp_delta"])

    # Detail
    ws_d = wb.create_sheet("Detail")
    fixed = ["담당자", "Customer", "MPN", "전주합계", "금주합계", "△", "△%", "GP전주", "GP금주", "GP△", "상태"]
    monthly_cols = []
    for m in result["months"]:
        monthly_cols += [f"{m}_prev", f"{m}_curr", f"{m}_△", f"{m}_gp_△"]
    cols = fixed + monthly_cols
    for j, h in enumerate(cols, 1):
        c = ws_d.cell(row=1, column=j, value=h); c.font = BOLD_W; c.fill = HDR_FILL; c.alignment = CENTER
    for i, r in enumerate(result["rows"], 2):
        for j, k in enumerate(cols, 1):
            ws_d.cell(row=i, column=j, value=r.get(k))

    for sh in (ws_s, ws_d):
        for col in sh.columns:
            try:
                letter = col[0].column_letter
            except AttributeError:
                continue
            w = 10
            for c in col[:300]:
                if c.value is None:
                    continue
                lv = len(str(c.value))
                if lv > w:
                    w = lv
            sh.column_dimensions[letter].width = min(40, w + 2)

    out = io.BytesIO()
    wb.save(out); out.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"FCST_주간변동_{stamp}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
            "X-Total-Delta": str(result["kpi"]["total_delta"]),
            "X-New": str(result["kpi"]["counts"]["신규"]),
            "X-Removed": str(result["kpi"]["counts"]["제거"]),
        },
    )


# 프론트엔드 정적 파일 서빙
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
