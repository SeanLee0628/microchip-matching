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
