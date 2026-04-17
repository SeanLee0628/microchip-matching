from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from database import Base


class SalesPerformance(Base):
    __tablename__ = "sales_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upload_batch = Column(String, index=True)
    uploaded_at = Column(DateTime, server_default=func.now())

    vendor = Column(String)
    mpn = Column(String, index=True)
    qty = Column(Float)
    dcpl_usd = Column(Float)
    buy_amt_usd = Column(Float)
    sp_usd = Column(Float)
    sell_amt_usd = Column(Float)
    exch_rate = Column(Float)
    sp_krw = Column(Float)
    sell_amt_krw = Column(Float)
    gp_usd = Column(Float)
    gp_pct_usd = Column(Float)
    gp_krw = Column(Float)
    gp_pct_krw = Column(Float)
    fse = Column(String)
    customer = Column(String)
    customer_code = Column(String)
    ship_date = Column(String)
    inbound_date = Column(String)
    month = Column(String, index=True)


SALES_FIELD_MAP = {
    "구분": "vendor",
    "MPN": "mpn",
    "QTY": "qty",
    "DCPL($)": "dcpl_usd",
    "매입금액($)": "buy_amt_usd",
    "SP($)": "sp_usd",
    "매출금액($)": "sell_amt_usd",
    "매출환율": "exch_rate",
    "SP(KRW)": "sp_krw",
    "매출금액(KRW)": "sell_amt_krw",
    "GP($)": "gp_usd",
    "GP%($)": "gp_pct_usd",
    "GP(KRW)": "gp_krw",
    "GP%(KRW)": "gp_pct_krw",
    "담당자": "fse",
    "납품처": "customer",
    "거래처코드": "customer_code",
    "출고일자": "ship_date",
    "입고일": "inbound_date",
    "Month": "month",
}

SALES_REVERSE_MAP = {v: k for k, v in SALES_FIELD_MAP.items()}
