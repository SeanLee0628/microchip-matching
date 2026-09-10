# -*- coding: utf-8 -*-
"""POS 27열 생성. 근거 없는 값은 만들어내지 않고 MISSING_POS_FIELD 로 보낸다.

예외: `pending_vendor_input: true` 인 필드(마이크로칩이 지정하는 값)는 우리가 만들 수
없으므로 draft 에서는 공란으로 두고 행을 살린다. final 모드는 그대로 막는다.
"""
from typing import Dict, List, Optional, Tuple

from .models import ExceptionRec, ProcessResult


class PosGenerator:
    def __init__(self, field_specs: List[dict], customer_by_code: Dict, namer,
                 address_master: Optional[Dict[str, dict]] = None,
                 disti_num_by_code: Optional[Dict[str, str]] = None,
                 crm=None, strict_vendor_fields: bool = False):
        self.specs = field_specs
        self.customer_by_code = customer_by_code
        self.namer = namer
        self.address_master = address_master or {}
        self.disti_num_by_code = disti_num_by_code or {}
        self.crm = crm
        self.strict_vendor_fields = strict_vendor_fields
        self.pending_fields = [s["name"] for s in field_specs if s.get("pending_vendor_input")]

    @property
    def columns(self) -> List[str]:
        return [s["name"] for s in self.specs]

    def _crm(self, res: ProcessResult) -> dict:
        if self.crm is None:
            return {}
        cust = self.customer_by_code.get(res.shipment.customer_code)
        name = cust.customer_name_original if cust else res.shipment.customer_name_original
        return self.crm.lookup(name)

    def _value(self, spec: dict, res: ProcessResult):
        src = spec.get("source")
        fld = spec.get("field")
        sh = res.shipment
        cust = self.customer_by_code.get(sh.customer_code)
        addr = self.address_master.get(sh.customer_code, {})

        if src == "const":
            return spec.get("value")
        if src == "shipment":
            v = getattr(sh, fld, None)
            if fld == "ship_date" and v is not None and spec.get("format"):
                return v.strftime(spec["format"])
            if fld == "part_number":
                return sh.part_number_original or sh.part_number
            return v
        if src == "applied":
            if fld == "disti_cost":
                return res.disti_cost
            if fld == "applied_price":
                return res.applied_price
            if fld == "dpa_price":
                return res.applied_price if res.applied_price_type == "QTN" else ""
            if fld == "applied_quote_number":
                return res.applied_quote_number if res.applied_price_type == "QTN" else ""
            return getattr(res, fld, None)
        if src == "customer_map":
            if cust is None:
                return None
            for attr in (fld, f"{fld}_original"):
                if hasattr(cust, attr):
                    return getattr(cust, attr)
            return None
        if src == "qtn":
            if fld == "disti_customer_number":
                return (self.disti_num_by_code.get(sh.customer_code)
                        or addr.get("disti_customer_number") or None)
            return None
        if src == "derived":
            if fld == "customer_city":
                # 주소 마스터 > 매칭표 영문 지역명
                return addr.get("end_city") or (cust.region if cust and cust.region else None)
            return None
        if src == "crm_address":
            # 수기 주소 마스터가 최우선, 그다음 CRM, 그다음 기본값
            manual = addr.get(f"purchasing_{fld}") or addr.get(f"end_{fld}") or addr.get(fld)
            if manual:
                return manual
            v = self._crm(res).get(fld)
            return v if v else spec.get("default")
        if src == "address_master":
            v = addr.get(fld)
            return v if v not in (None, "") else spec.get("default")
        return None

    def build(self, results: List[ProcessResult]) -> Tuple[List[dict], List[ExceptionRec], List[dict]]:
        """(POS 행, 예외, 필드매핑표)"""
        rows, excs = [], []
        for res in results:
            if res.processing_status != "OK":
                continue
            row, missing, pending = {}, [], []
            for spec in self.specs:
                v = self._value(spec, res)
                empty = v is None or (isinstance(v, str) and v.strip() == "")
                if spec.get("required", True) and not spec.get("blank_ok") and empty:
                    if spec.get("pending_vendor_input") and not self.strict_vendor_fields:
                        pending.append(spec["name"])     # 벤더 지정값 — 행은 살린다
                    else:
                        missing.append(spec["name"])
                row[spec["name"]] = "" if v is None else v
            if missing:
                res.processing_status = "EXCEPTION"
                res.add_exception("MISSING_POS_FIELD")
                res.review_required = True
                excs.append(ExceptionRec(
                    "MISSING_POS_FIELD", "ERROR", res.shipment.shipment_id,
                    f"{res.shipment.source_sheet}!{res.shipment.source_row}행",
                    "필수 POS 필드 값 없음: " + ", ".join(missing), False,
                    "주소 마스터 입력 또는 config/rules.yaml 의 고정값 지정"))
                continue
            if pending:
                # 행은 살리되 제출 가능 상태는 막는다 (draft 검토용).
                res.add_exception("MISSING_POS_FIELD")
                res.review_required = True
                excs.append(ExceptionRec(
                    "MISSING_POS_FIELD", "ERROR", res.shipment.shipment_id,
                    f"{res.shipment.source_sheet}!{res.shipment.source_row}행",
                    "마이크로칩 지정값 미입력(공란으로 생성됨): " + ", ".join(pending), False,
                    "config/rules.yaml 의 해당 pos_fields 에 value 입력 후 --mode final 로 재실행"))
            rows.append(row)
        return rows, excs, self.mapping_table()

    def mapping_table(self) -> List[dict]:
        src_label = {
            "shipment": ("RAWDATA", "출고이력"), "qtn": ("RAWDATA", "단가2(QTN)"),
            "asd": ("RAWDATA", "단가3(ASD)"),
            "customer_map": ("RAWDATA", "업체코드,마이크로칩업체명 매칭"),
            "applied": ("계산결과", "단가 결정 엔진"),
            "derived": ("파생", "업체코드 매칭표 지역명"),
            "const": ("설정파일", "config/rules.yaml"),
            "crm_address": ("CRM 고객사", "고객사 내보내기"),
            "address_master": ("주소 마스터", "customer_address_master.xlsx"),
        }
        out = []
        for s in self.specs:
            f, sheet = src_label.get(s.get("source"), ("?", "?"))
            if s.get("pending_vendor_input"):
                handling = "벤더 지정값 미입력 — draft 공란, final 차단"
            elif s.get("blank_ok"):
                handling = "공란 허용"
            else:
                handling = "MISSING_POS_FIELD 예외"
            out.append({
                "POS 컬럼명": s["name"], "원천 파일": f, "원천 시트": sheet,
                "원천 컬럼": s.get("field") or (str(s.get("value")) if s.get("source") == "const" else ""),
                "변환 규칙": s.get("evidence", ""),
                "필수 여부": "필수" if s.get("required", True) else "선택",
                "미입력 시 처리": handling,
            })
        return out

    def address_master_needed(self, results: List[ProcessResult]) -> List[dict]:
        """주소 마스터에 채워야 할 고객 목록(중복 제거). CRM 으로 채워진 항목은 제외."""
        need = {}
        for res in results:
            sh = res.shipment
            if sh.customer_code in need:
                continue
            cust = self.customer_by_code.get(sh.customer_code)
            addr = self.address_master.get(sh.customer_code, {})
            crm = self._crm(res)
            missing = []
            for s in self.specs:
                if s.get("source") not in ("crm_address", "address_master", "derived"):
                    continue
                if self._value(s, res) in (None, ""):
                    missing.append(s["name"])
            if missing:
                need[sh.customer_code] = {
                    "고객코드": sh.customer_code,
                    "거래처명": cust.customer_name_original if cust else sh.customer_name_original,
                    "영문명(지역포함)": cust.name_with_region if cust else "",
                    "purchasing_city": addr.get("purchasing_city", cust.region if cust else ""),
                    "purchasing_state": addr.get("purchasing_state", crm.get("state", "")),
                    "purchasing_postal": addr.get("purchasing_postal", crm.get("postal", "")),
                    "purchasing_country": addr.get("purchasing_country", "KR"),
                    "end_city": addr.get("end_city", cust.region if cust else ""),
                    "end_state": addr.get("end_state", crm.get("state", "")),
                    "end_postal": addr.get("end_postal", crm.get("postal", "")),
                    "end_country": addr.get("end_country", "KR"),
                    "disti_customer_number": addr.get("disti_customer_number", ""),
                    "CRM 주소(참고)": crm.get("address", ""),
                    "미입력 필드": ", ".join(missing),
                }
        return sorted(need.values(), key=lambda r: r["고객코드"])
