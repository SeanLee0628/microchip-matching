from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import pandas as pd
import io
import math
import uuid
from datetime import datetime

from database import engine, get_db, Base
from models import MicrochipMatching, FIELD_MAP, REVERSE_MAP

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
        return float(v)
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

    # DB에 저장 (기존 데이터 삭제 후 새로 저장)
    db.query(MicrochipMatching).delete()
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

    db_rows = [record_to_db_row(r, batch_id) for r in records]
    db.bulk_save_objects(db_rows)
    db.commit()

    return {
        "sheet_name": target_sheet,
        "columns": final_columns,
        "data": records,
        "total_rows": len(records),
        "saved_to_db": True,
        "batch_id": batch_id,
    }


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
    rows = data.get("data", [])
    columns = data.get("columns", COLUMNS)

    df = pd.DataFrame(rows, columns=[c for c in columns if c in (rows[0].keys() if rows else columns)])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="마이크로칩(매칭)", index=False)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=microchip_matching_export.xlsx"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
