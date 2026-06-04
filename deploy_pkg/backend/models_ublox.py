from sqlalchemy import Column, Integer, String, Float, DateTime, Date
from sqlalchemy.sql import func
from database import Base


class UbloxBacklog(Base):
    __tablename__ = "ublox_backlog"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upload_version = Column(Integer, index=True)  # 업로드 회차 (비교용)
    upload_date = Column(Date, index=True)
    uploaded_at = Column(DateTime, server_default=func.now())

    order_name = Column(String)
    order_no = Column(String, index=True)
    po_no_line_item = Column(String)
    invoice_number = Column(String)
    reference = Column(String)
    account_name = Column(String)
    account_number = Column(String)
    reporting_office = Column(String)
    order_status = Column(String)
    type_number = Column(String, index=True)     # 품명
    frame_order = Column(String)
    order_date = Column(String)
    request_date = Column(String)
    delivery_date = Column(String)
    prev_delivery_date = Column(String)          # 전일 Delivery Date (엑셀 14번 컬럼)
    delinq = Column(Float)
    qty_ordered = Column(Float)
    qty_invoiced = Column(Float)
    price_per_unit = Column(Float)
    total_value = Column(Float)
    end_customer = Column(String)
    end_customer_number = Column(String)
    project_owner = Column(String)
    project_owner_number = Column(String)


# 엑셀 컬럼 인덱스 → DB 필드 매핑
UBLOX_COLUMN_MAP = {
    0: ("order_name", "Order Name", "str"),
    1: ("order_no", "Order No", "str"),
    2: ("po_no_line_item", "PO No Line Item", "str"),
    3: ("invoice_number", "Invoice Number", "str"),
    4: ("reference", "Reference", "str"),
    5: ("account_name", "Account Name", "str"),
    6: ("account_number", "Account Number", "str"),
    7: ("reporting_office", "Reporting uB Office", "str"),
    8: ("order_status", "Order Status", "str"),
    9: ("type_number", "Type Number", "str"),
    10: ("frame_order", "Frame Order", "str"),
    11: ("order_date", "Order Date", "date"),
    12: ("request_date", "Request Date", "date"),
    13: ("delivery_date", "Delivery Date", "date"),
    14: ("prev_delivery_date", "전일 Delivery Date", "date"),
    15: ("delinq", "DELINQ", "float"),
    16: ("qty_ordered", "Qty Ordered", "float"),
    17: ("qty_invoiced", "Qty Invoiced", "float"),
    18: ("price_per_unit", "Price per unit", "float"),
    19: ("total_value", "Total Value", "float"),
    20: ("end_customer", "End Customer", "str"),
    21: ("end_customer_number", "End Customer No", "str"),
    22: ("project_owner", "Project Owner", "str"),
    23: ("project_owner_number", "Project Owner No", "str"),
}

UBLOX_DISPLAY_COLUMNS = [v[1] for v in UBLOX_COLUMN_MAP.values()]
