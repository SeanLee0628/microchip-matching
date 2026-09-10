# -*- coding: utf-8 -*-
"""원본 시트 → 표준 데이터 모델 변환."""
from typing import Dict, List, Tuple

import pandas as pd

from . import normalizer as N
from .models import ASD, QTN, CustomerMapEntry, ExceptionRec, Shipment


def _get(row, mapping: Dict[str, str], std: str):
    col = mapping.get(std)
    if not col:
        return None
    return row.get(col)


def map_shipments(df: pd.DataFrame, mapping: Dict[str, str], hdr_row: int,
                  src_file: str, src_sheet: str) -> Tuple[List[Shipment], List[ExceptionRec]]:
    out, exc = [], []
    for i, row in df.iterrows():
        src_row = hdr_row + 2 + int(i)
        d = N.to_date(_get(row, mapping, "ship_date"))
        part = N.part(_get(row, mapping, "part_number"))
        code = N.code(_get(row, mapping, "customer_code"))
        qty = N.number(_get(row, mapping, "ship_qty"))
        inv = N.text(_get(row, mapping, "invoice_number"))
        item = N.text(_get(row, mapping, "invoice_item_number"))
        if not any([d, part, code, qty]):
            continue  # 빈 줄
        sid = f"{d.strftime('%Y%m%d') if d else 'NODATE'}|{inv}|{item}|{code}|{part}|{qty}"
        s = Shipment(
            shipment_id=sid, ship_date=d, customer_code=code,
            customer_name_original=N.text(_get(row, mapping, "customer_name")),
            part_number=part, part_number_original=N.text(_get(row, mapping, "part_number")),
            ship_qty=qty, resale_price=N.number(_get(row, mapping, "resale_price")),
            invoice_number=inv, invoice_item_number=item,
            source_file=src_file, source_sheet=src_sheet, source_row=src_row,
        )
        loc = f"{src_sheet}!{src_row}행"
        if d is None:
            exc.append(ExceptionRec("INVALID_DATE", "ERROR", sid, loc,
                                    "출고일자를 날짜로 해석할 수 없습니다", False,
                                    "원본 셀 서식을 날짜로 수정"))
        if qty is None:
            exc.append(ExceptionRec("INVALID_QUANTITY", "ERROR", sid, loc,
                                    "출고수량이 숫자가 아닙니다", False, "원본 수량 확인"))
        out.append(s)
    return out, exc


def map_customer_map(df: pd.DataFrame, mapping: Dict[str, str], hdr_row: int,
                     namer) -> List[CustomerMapEntry]:
    out = []
    for i, row in df.iterrows():
        code = N.code(_get(row, mapping, "customer_code"))
        if not code:
            continue
        with_region = N.text(_get(row, mapping, "name_with_region"))
        without_region = N.text(_get(row, mapping, "name_without_region"))
        _head, region = namer.split_region(with_region)
        out.append(CustomerMapEntry(
            customer_code=code,
            customer_name_original=N.text(_get(row, mapping, "customer_name")),
            customer_name_normalized=namer.normalize(_get(row, mapping, "customer_name")),
            name_with_region=with_region, name_without_region=without_region,
            region=region, source_row=hdr_row + 2 + int(i),
        ))
    return out


def map_qtns(df: pd.DataFrame, mapping: Dict[str, str], hdr_row: int, namer) -> List[QTN]:
    out = []
    for i, row in df.iterrows():
        part = N.part(_get(row, mapping, "part_number"))
        if not part:
            continue
        src_row = hdr_row + 2 + int(i)
        quote = N.text(_get(row, mapping, "quote_number"))
        item = N.text(_get(row, mapping, "quote_item_number"))
        end_cust = N.text(_get(row, mapping, "end_customer"))
        remains = N.number(_get(row, mapping, "remains"))
        out.append(QTN(
            qtn_id=f"{item or quote}#{src_row}", quote_number=quote, quote_item_number=item,
            end_customer_original=end_cust, end_customer_normalized=namer.normalize(end_cust),
            customer_code=None, part_number=part,
            qtn_price=N.number(_get(row, mapping, "qtn_price")),
            valid_start=N.to_date(_get(row, mapping, "valid_start")),
            valid_end=N.to_date(_get(row, mapping, "valid_end")),
            original_remains=remains, remaining_qty=float(remains or 0),
            disti_customer_number=N.text(_get(row, mapping, "disti_customer_number")),
            match_status="NOT_MATCHED", source_row=src_row,
        ))
    return out


def map_asds(df: pd.DataFrame, mapping: Dict[str, str], hdr_row: int) -> List[ASD]:
    out = []
    for i, row in df.iterrows():
        part = N.part(_get(row, mapping, "part_number"))
        if not part:
            continue
        src_row = hdr_row + 2 + int(i)
        out.append(ASD(
            asd_id=f"ASD#{src_row}", part_number=part,
            asd_price=N.number(_get(row, mapping, "asd_price")),
            valid_start=N.to_date(_get(row, mapping, "valid_start")),
            valid_end=N.to_date(_get(row, mapping, "valid_end")),
            source_row=src_row,
        ))
    return out
