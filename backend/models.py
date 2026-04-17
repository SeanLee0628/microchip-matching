from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from database import Base


class MicrochipMatching(Base):
    __tablename__ = "microchip_matching"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upload_batch = Column(String, index=True)  # 업로드 그룹 식별자
    uploaded_at = Column(DateTime, server_default=func.now())

    고객코드 = Column(String)
    믹스 = Column("mix_key", String)            # 믹스#
    sales = Column(String)
    고객 = Column(String)
    end_customer = Column(String)               # END
    purchasing = Column(String)                  # PURCHASING
    part_no = Column(String)                     # PART#
    fab2 = Column(String)
    lt = Column(Float)                           # LT
    y2023 = Column(Float)                        # 2023년
    y2024 = Column(Float)                        # 2024년
    y2025 = Column(Float)                        # 2025년
    y2026 = Column(Float)                        # 2026년
    trend_23_25 = Column(Float)                  # 23~25추이
    bl_25_26 = Column(Float)                     # 25-26(w/BL)
    blog_ttl = Column(Float)                     # BLOG TTL
    m03 = Column(Float)                          # 3월
    m04 = Column(Float)                          # 4월
    m05 = Column(Float)                          # 5월
    m06 = Column(Float)                          # 6월
    m07 = Column(Float)                          # 7월
    m08 = Column(Float)                          # 8월
    m09 = Column(Float)                          # 9월
    m10 = Column(Float)                          # 10월
    m11 = Column(Float)                          # 11월
    m12 = Column(Float)                          # 12월
    mix_customer_part = Column(String, unique=True, index=True)  # 믹스#(customer&part) - 중복 체크 키


# DB 컬럼명 ↔ 엑셀 컬럼명 매핑
FIELD_MAP = {
    "고객코드": "고객코드",
    "믹스#": "믹스",
    "Sales": "sales",
    "고객": "고객",
    "END": "end_customer",
    "PURCHASING": "purchasing",
    "PART#": "part_no",
    "FAB2": "fab2",
    "LT": "lt",
    "2023년": "y2023",
    "2024년": "y2024",
    "2025년": "y2025",
    "2026년": "y2026",
    "23~25추이": "trend_23_25",
    "25-26(w/BL)": "bl_25_26",
    "BLOG TTL": "blog_ttl",
    "3월": "m03",
    "4월": "m04",
    "5월": "m05",
    "6월": "m06",
    "7월": "m07",
    "8월": "m08",
    "9월": "m09",
    "10월": "m10",
    "11월": "m11",
    "12월": "m12",
    "믹스#(customer&part)": "mix_customer_part",
}

# 역방향 매핑 (DB → 엑셀)
REVERSE_MAP = {v: k for k, v in FIELD_MAP.items()}
