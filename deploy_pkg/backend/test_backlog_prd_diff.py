"""5실 백록 PRD 변동 비교 — 컬럼명 별칭 매핑 회귀 테스트.

한 논리 항목(PART#/ORD/CRD/PRD)을 레거시 이름이든 마이크로칩 원본 이름이든
동일 데이터로 인식하는지 검증한다.
  PART# = Mchp Catalog Part Number
  ORD   = Order date
  CRD   = Customer Requested Delivery Date
  PRD   = Mchp Scheduled Delivery Date
"""
import io
from datetime import date

import openpyxl
import main_aws as m

_BASE = ["End Customer Name", "ODM/SubCon Name", "Customer PO#", "SO#",
         "Quote No.", "Qty Due", "Unit Price", "Amount Due", "업체명", "더존업체명코드"]
LEGACY_HDR = ["PART#", "ORD", "CRD", "PRD"] + _BASE
# 대소문자/공백을 일부러 다르게 — 정규화 매칭 확인
ALIAS_HDR = ["Mchp Catalog Part Number", "order DATE",
             "Customer Requested  Delivery Date", "Mchp Scheduled Delivery Date"] + _BASE


def _row(part, ord_, crd, prd, so):
    return [part, ord_, crd, prd, "고객A", "ODM1", "PO1", so, "Q1", 100, 1.5, 150, "벤더A", 12345]


def _xlsx(headers, rows, sheet="인풋", pre_rows=0):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for _ in range(pre_rows):           # 헤더 앞 더미행 → 헤더 자동탐지 검증
        ws.append([])
    ws.append(headers)
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


_BEFORE = [_row("PARTX", date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 10), "S1"),
           _row("PARTY", date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 20), "S2")]
_AFTER = [_row("PARTX", date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 15), "S1"),
          _row("PARTY", date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 18), "S2")]


def _compare(hdr_before, hdr_after):
    bd = m._backlog_load(_xlsx(hdr_before, _BEFORE), "before.xlsx")
    ad = m._backlog_load(_xlsx(hdr_after, _AFTER, pre_rows=2), "after.xlsx")
    changed = m._backlog_build_changed(bd, ad)
    return {r["SO#"]: r for r in changed}


def _assert_expected(by_so):
    assert set(by_so) == {"S1", "S2"}
    assert by_so["S1"]["일정변동 현황"] == "PUSH-OUT" and by_so["S1"]["변경일자"] == 5
    assert by_so["S2"]["일정변동 현황"] == "PULL-IN" and by_so["S2"]["변경일자"] == 2
    # 별칭으로 들어와도 PART#/ORD/CRD/PRD 가 모두 채워져야 한다
    assert by_so["S1"]["Mchp Catalog Part Number"] == "PARTX"
    for so in ("S1", "S2"):
        for col in ("ORD", "CRD", "PRD"):
            assert by_so[so][col] is not None


def test_legacy_headers():
    _assert_expected(_compare(LEGACY_HDR, LEGACY_HDR))


def test_alias_headers():
    _assert_expected(_compare(ALIAS_HDR, ALIAS_HDR))


def test_mixed_headers():
    # 전일=레거시, 금일=별칭 이어도 동일 항목으로 매칭
    _assert_expected(_compare(LEGACY_HDR, ALIAS_HDR))


def test_field_idx_resolution():
    ad = m._backlog_load(_xlsx(ALIAS_HDR, _AFTER), "after.xlsx")
    assert ad["field_idx"] == {"PART#": 0, "ORD": 1, "CRD": 2, "PRD": 3}
