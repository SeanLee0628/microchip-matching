# -*- coding: utf-8 -*-
"""표준 데이터 모델. 원본 값과 정규화 값을 함께 보존한다."""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, List, Dict, Any


@dataclass
class Shipment:
    shipment_id: str
    ship_date: Optional[date]
    customer_code: str
    customer_name_original: str
    part_number: str                 # 정규화(대문자·공백제거)
    part_number_original: str
    ship_qty: Optional[float]
    resale_price: Optional[float]
    invoice_number: str
    invoice_item_number: str
    source_file: str
    source_sheet: str
    source_row: int


@dataclass
class QTN:
    qtn_id: str
    quote_number: str
    quote_item_number: str
    end_customer_original: str
    end_customer_normalized: str
    customer_code: Optional[str]
    part_number: str
    qtn_price: Optional[float]
    valid_start: Optional[date]
    valid_end: Optional[date]
    original_remains: Optional[float]
    remaining_qty: float
    disti_customer_number: str
    match_status: str                # AUTO_EXACT / AUTO_NORMALIZED / ... / MULTIPLE / NOT_MATCHED
    source_row: int


@dataclass
class ASD:
    asd_id: str
    part_number: str
    asd_price: Optional[float]
    valid_start: Optional[date]
    valid_end: Optional[date]
    source_row: int


@dataclass
class CustomerMapEntry:
    customer_code: str
    customer_name_original: str
    customer_name_normalized: str
    name_with_region: str
    name_without_region: str
    region: str
    source_row: int


@dataclass
class ExceptionRec:
    code: str
    severity: str
    shipment_id: str
    source_location: str
    message: str
    auto_handled: bool
    recommended_action: str
    owner_input: str = ""
    reprocess: str = ""

    def as_row(self) -> Dict[str, Any]:
        return {
            "예외 코드": self.code, "심각도": self.severity,
            "출고 고유키": self.shipment_id, "원본 위치": self.source_location,
            "문제 내용": self.message, "자동 처리 여부": "예" if self.auto_handled else "아니오",
            "추천 조치": self.recommended_action,
            "담당자 입력": self.owner_input, "재처리 여부": self.reprocess,
        }


@dataclass
class ProcessResult:
    shipment: Shipment
    qtn_candidate_count: int = 0
    selected_qtn: Optional[QTN] = None
    qtn_candidate_price: Optional[float] = None
    qtn_remaining_before: Optional[float] = None
    qtn_used_qty: Optional[float] = None
    qtn_remaining_after: Optional[float] = None
    asd_candidate_count: int = 0
    selected_asd: Optional[ASD] = None
    asd_candidate_price: Optional[float] = None
    applied_price_type: str = "NONE"     # QTN / ASD / NONE
    applied_price: Optional[float] = None
    disti_cost: Optional[float] = None   # 적용단가, 없으면 표준 수입가(단가1 DC)
    disti_cost_source: str = ""          # APPLIED / IMPORT_PRICE / NONE
    applied_quote_number: str = ""
    applied_quote_item_number: str = ""
    selection_reason: str = ""
    rule_id: str = ""
    processing_status: str = "OK"        # OK / EXCEPTION
    exception_codes: List[str] = field(default_factory=list)
    review_required: bool = False
    customer_name: str = ""

    def add_exception(self, code: str):
        if code not in self.exception_codes:
            self.exception_codes.append(code)
