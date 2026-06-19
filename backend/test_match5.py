# -*- coding: utf-8 -*-
"""영업5실 매칭 순수 로직 테스트 (stdlib unittest, openpyxl).
실행: python -m unittest test_match5 -v   (backend/ 에서)
"""
import io
import unittest
from datetime import date, datetime

import openpyxl

import match5 as m5


class TestMix(unittest.TestCase):
    def test_make_mix_concatenates_code_and_part(self):
        self.assertEqual(m5.make_mix("133742", "24FC01T-E/ST36KVAO"),
                         "13374224FC01T-E/ST36KVAO")

    def test_make_mix_strips_internal_whitespace_and_newlines(self):
        # FCST 일부 MPN에 선행 \n 이 섞여 있음 → 동일 키로 매칭되어야 함
        self.assertEqual(m5.make_mix("133742", "\nMCP1322T-27LE/OTVAO"),
                         m5.make_mix("133742", "MCP1322T-27LE/OTVAO"))

    def test_make_mix_uppercases_part(self):
        self.assertEqual(m5.make_mix("100", "abc-1"), "100ABC-1")

    def test_make_mix_float_code_becomes_int_string(self):
        self.assertEqual(m5.make_mix(131112.0, "23K256T-I/SN"),
                         "13111223K256T-I/SN")

    def test_make_mix_none_returns_none(self):
        self.assertIsNone(m5.make_mix(None, "X"))
        self.assertIsNone(m5.make_mix("100", None))
        self.assertIsNone(m5.make_mix("100", "   "))

    def test_columns_shapes(self):
        self.assertEqual(len(m5.COLUMNS), 27)
        self.assertEqual(m5.COLUMNS[1], "믹스#")
        # 대시보드는 월별/추이/wBL 을 숨긴다
        self.assertNotIn("6월", m5.DASHBOARD_COLUMNS)
        self.assertNotIn("23~25추이", m5.DASHBOARD_COLUMNS)
        self.assertIn("BLOG TTL", m5.DASHBOARD_COLUMNS)


class TestToDate(unittest.TestCase):
    def test_datetime_unchanged(self):
        self.assertEqual(m5._to_date(datetime(2026, 1, 2)), date(2026, 1, 2))

    def test_date_unchanged(self):
        self.assertEqual(m5._to_date(date(2026, 1, 2)), date(2026, 1, 2))

    def test_yyyymmdd_int(self):
        self.assertEqual(m5._to_date(20250825), date(2025, 8, 25))

    def test_yyyymmdd_str(self):
        self.assertEqual(m5._to_date("20250825"), date(2025, 8, 25))

    def test_invalid_month_returns_none(self):
        self.assertIsNone(m5._to_date("20251301"))

    def test_not_eight_digits_returns_none(self):
        self.assertIsNone(m5._to_date(12345))

    def test_none_returns_none(self):
        self.assertIsNone(m5._to_date(None))

    def test_non_numeric_str_returns_none(self):
        self.assertIsNone(m5._to_date("hello"))


class TestFindHeaderRow(unittest.TestCase):
    def _ws(self, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        return ws

    def test_header_on_first_row(self):
        ws = self._ws([["PART#", "Qty Due"], ["A", 1]])
        idx, colmap = m5._find_header_row(ws, {"PART#"}, max_scan=5)
        self.assertEqual(idx, 1)
        self.assertEqual(colmap["PART#"], 1)

    def test_header_on_third_row(self):
        ws = self._ws([["title"], [None], ["담당자", "MPN", "Demand Total"], ["a", "b", 3]])
        idx, colmap = m5._find_header_row(ws, {"Demand Total", "MPN"}, max_scan=6)
        self.assertEqual(idx, 3)
        self.assertEqual(colmap["MPN"], 2)
        self.assertEqual(colmap["Demand Total"], 3)

    def test_returns_none_when_not_found(self):
        ws = self._ws([["x", "y"], ["a", "b"]])
        idx, colmap = m5._find_header_row(ws, {"PART#"}, max_scan=5)
        self.assertIsNone(idx)


def _wb_bytes(header_rows, data_rows, sheet_title="Sheet1"):
    """헤더 여러 줄 + 데이터 줄을 가진 xlsx 를 bytes 로."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    for row in header_rows:
        ws.append(row)
    for row in data_rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestParseBlog(unittest.TestCase):
    HEADER = ["PART#", "더존코드", "Qty Due", "PDD", "Change Window",
              "Lead Time Weeks", "Full Manufacturing Cycle Time Weeks", "더존업체명"]

    def _rows(self, rows):
        return _wb_bytes([self.HEADER], rows, sheet_title="260609")

    def test_blog_ttl_and_monthly_bucket_by_pdd(self):
        data = [
            ["23K256T-I/SN", 131112, 3300, datetime(2026, 6, 7), 45, 9, 17, "(주)에이텍"],
            ["23K256T-I/SN", 131112, 6600, datetime(2026, 8, 7), 45, 9, 17, "(주)에이텍"],
        ]
        out = m5.parse_blog(self._rows(data))
        mix = m5.make_mix(131112, "23K256T-I/SN")
        self.assertEqual(out[mix]["blog_ttl"], 9900)
        self.assertEqual(out[mix]["monthly"][6], 3300)
        self.assertEqual(out[mix]["monthly"][8], 6600)

    def test_lead_time_is_max_of_two_columns(self):
        data = [["P1", 100, 10, datetime(2026, 6, 1), 45, 9, 17, "C"]]
        out = m5.parse_blog(self._rows(data))
        self.assertEqual(out[m5.make_mix(100, "P1")]["lead_time"], 17)

    def test_cancel_window_is_earliest_pdd_minus_change_window(self):
        # PDD - Change Window(일). 두 행 중 더 이른 날짜.
        data = [
            ["P1", 100, 10, datetime(2026, 8, 7), 45, 9, 17, "C"],   # 8/7 - 45 = 6/23
            ["P1", 100, 10, datetime(2026, 6, 26), 45, 9, 17, "C"],  # 6/26 - 45 = 5/12 (더 이름)
        ]
        out = m5.parse_blog(self._rows(data))
        self.assertEqual(out[m5.make_mix(100, "P1")]["cancel_window"], date(2026, 5, 12))

    def test_company_and_part_carried(self):
        data = [["P1", 100, 10, datetime(2026, 6, 1), 45, 9, 17, "(주)테스트"]]
        out = m5.parse_blog(self._rows(data))
        rec = out[m5.make_mix(100, "P1")]
        self.assertEqual(rec["더존업체명"], "(주)테스트")
        self.assertEqual(rec["part"], "P1")
        self.assertEqual(rec["더존코드"], "100")


class TestParseShipment(unittest.TestCase):
    HEADER = ["출고일자", "출고번호", "No", "고객코드", "고객", "담당자",
              "품번", "출고수량", "단가"]

    def _rows(self, rows):
        return _wb_bytes([self.HEADER], rows)

    def test_sums_2026_qty_by_mix(self):
        data = [
            [datetime(2026, 1, 2), "IS1", 1, 131149, "(주)에스엔아이", "신성일", "TC4452VAT", 1600, 1],
            [datetime(2026, 3, 5), "IS2", 1, 131149, "(주)에스엔아이", "신성일", "TC4452VAT", 400, 1],
        ]
        out = m5.parse_shipment(self._rows(data))
        mix = m5.make_mix(131149, "TC4452VAT")
        self.assertEqual(out[mix]["y2026"], 2000)
        self.assertEqual(out[mix]["담당자"], "신성일")
        self.assertEqual(out[mix]["고객"], "(주)에스엔아이")
        self.assertEqual(out[mix]["고객코드"], "131149")
        self.assertEqual(out[mix]["part"], "TC4452VAT")

    def test_non_2026_excluded(self):
        data = [[datetime(2025, 12, 31), "X", 1, 100, "C", "S", "P1", 999, 1]]
        out = m5.parse_shipment(self._rows(data))
        self.assertEqual(out[m5.make_mix(100, "P1")]["y2026"], 0)

    def test_cutoff_excludes_on_or_after(self):
        # cutoff_date 지정 시 그 날짜 이상은 제외 (전월 말까지 누적 옵션)
        data = [
            [datetime(2026, 5, 10), "A", 1, 100, "C", "S", "P1", 10, 1],
            [datetime(2026, 6, 20), "B", 1, 100, "C", "S", "P1", 20, 1],
        ]
        out = m5.parse_shipment(self._rows(data), cutoff_date=date(2026, 6, 1))
        self.assertEqual(out[m5.make_mix(100, "P1")]["y2026"], 10)


class TestParseFcst(unittest.TestCase):
    def _bytes(self, data_rows):
        # 헤더 3행: 1·2행 더미, 3행 실제 헤더. 시트명 'Sales Revenue'.
        # 컬럼 순서: 담당자, Customer, MPN, Demand Total, Customer Code.
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sales Revenue"
        ws.append(["month band"])
        ws.append(["sub totals"])
        ws.append(["담당자", "Customer", "MPN", "Demand Total", "Customer Code"])
        for row in data_rows:
            ws.append(row)
        buf = io.BytesIO(); wb.save(buf)
        return buf.getvalue()

    def test_demand_total_keyed_by_mix(self):
        data = [
            ["김담당", "(주)다나와", "ATMEGA169P-16AU", 6120, 131150],
            ["김담당", "(주)다나와", "PIC16F1947-I/PT", 3200, 131150],
        ]
        out = m5.parse_fcst(self._bytes(data))
        self.assertEqual(out[m5.make_mix(131150, "ATMEGA169P-16AU")]["demand"], 6120)
        self.assertEqual(out[m5.make_mix(131150, "PIC16F1947-I/PT")]["demand"], 3200)

    def test_identifiers_populated(self):
        data = [["김담당", "(주)다나와", "ATMEGA169P-16AU", 6120, 131150]]
        out = m5.parse_fcst(self._bytes(data))
        rec = out[m5.make_mix(131150, "ATMEGA169P-16AU")]
        self.assertEqual(rec["code"], "131150")
        self.assertEqual(rec["part"], m5._norm_part("ATMEGA169P-16AU"))
        self.assertEqual(rec["담당자"], "김담당")
        self.assertEqual(rec["고객"], "(주)다나와")

    def test_mpn_with_newline_normalized(self):
        data = [["김담당", "(주)x", "\nMCP1322T-27LE/OTVAO", 50, 133742]]
        out = m5.parse_fcst(self._bytes(data))
        self.assertEqual(out[m5.make_mix(133742, "MCP1322T-27LE/OTVAO")]["demand"], 50)

    def test_sums_duplicates(self):
        data = [
            ["김담당", "c", "P1", 10, 100],
            ["김담당", "c", "P1", 5, 100],
        ]
        out = m5.parse_fcst(self._bytes(data))
        self.assertEqual(out[m5.make_mix(100, "P1")]["demand"], 15)


class TestSumAvailableQty(unittest.TestCase):
    def _ws(self, data_rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Jun inventory"
        ws.append(["title row"])  # 1행 더미
        ws.append(["Central", "Sales team", "VENDER", "SR#", "FAMILY", "DID#",
                   "품명", "Part#", "MOBIS ID", "unit", "site", "MOQ", "Package",
                   "FAB", "Q'ty", "SALES", "CUSTOMER", "CRD", "booking",
                   "available Q'ty"])  # 2행 헤더 (Part#=8열, available Q'ty=20열)
        for row in data_rows:
            ws.append(row)
        return ws

    def _row(self, part, oqty, avail):
        return ["A", "5실", "MICROCHIP", ".", ".", ".", "UT", part, ".", "EA",
                ".", ".", ".", ".", oqty, ".", ".", ".", ".", avail]

    def test_sums_available_qty_per_part(self):
        ws = self._ws([
            self._row("PIC16F1947-I/PT", 6400, 6400),
            self._row("PIC16F1947-I/PT", 0, 0),
            self._row("23K256T-I/SN", 3300, 1000),
        ])
        out = m5._sum_available_qty(ws)
        self.assertEqual(out[m5._norm_part("PIC16F1947-I/PT")], 6400)
        self.assertEqual(out[m5._norm_part("23K256T-I/SN")], 1000)

    def test_missing_header_returns_empty(self):
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["nope", "data"])
        self.assertEqual(m5._sum_available_qty(ws), {})


import os
_INV_PATH = r"C:\Users\user\Downloads\cowork\Uniquant_마이크로칩 매칭\Jun_2026_daily warehouse inventory list(영업5실).xlsx"


class TestParseInventoryRealFile(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(_INV_PATH), "real inventory file not present")
    def test_decrypts_and_aggregates(self):
        with open(_INV_PATH, "rb") as f:
            out = m5.parse_inventory(f.read(), password="9178")
        self.assertGreater(len(out), 0)
        # PIC16F1947-I/PT 가 존재하고 합계가 0 이상
        self.assertIn(m5._norm_part("PIC16F1947-I/PT"), out)

    @unittest.skipUnless(os.path.exists(_INV_PATH), "real inventory file not present")
    def test_wrong_password_raises(self):
        with open(_INV_PATH, "rb") as f:
            data = f.read()
        with self.assertRaises(ValueError):
            m5.parse_inventory(data, password="0000")


class TestBuildRecords(unittest.TestCase):
    def test_joins_all_sources_and_computes_balance(self):
        mix = m5.make_mix(131112, "23K256T-I/SN")
        part = m5._norm_part("23K256T-I/SN")
        inv = {part: 5000}
        fcst = {mix: {"demand": 2000, "code": "131112", "part": part,
                      "담당자": None, "고객": None}}
        blog = {mix: {"lead_time": 17, "cancel_window": date(2026, 5, 12),
                      "blog_ttl": 6600, "monthly": {6: 0, 8: 6600},
                      "더존코드": "131112", "더존업체명": "(주)에이텍", "part": part}}
        ship = {mix: {"담당자": "신성일", "고객": "(주)에이텍", "고객코드": "131112",
                      "part": part, "y2026": 1200}}
        cols, dash, recs = m5.build_records(inv, fcst, blog, ship)
        self.assertEqual(cols, m5.COLUMNS)
        self.assertEqual(dash, m5.DASHBOARD_COLUMNS)
        self.assertEqual(len(recs), 1)
        row = recs[0]
        self.assertEqual(row["고객코드"], "131112")
        self.assertEqual(row["믹스#"], mix)
        self.assertEqual(row["담당자"], "신성일")
        self.assertEqual(row["품번"], part)
        self.assertEqual(row["Q'ty"], 5000)
        self.assertEqual(row["Lead Time"], 17)
        self.assertEqual(row["Cancel Window"], "2026-05-12")
        self.assertEqual(row["Demand Total"], 2000)
        # Balance = 5000 + 6600 - 2000
        self.assertEqual(row["Balance"], 9600)
        self.assertEqual(row["2026년"], 1200)
        self.assertIsNone(row["2023년"])
        self.assertEqual(row["BLOG TTL"], 6600)
        self.assertEqual(row["8월"], 6600)
        self.assertEqual(row["6월"], 0)

    def test_row_universe_is_union(self):
        m_blog = m5.make_mix(1, "A")
        m_ship = m5.make_mix(2, "B")
        m_fcst = m5.make_mix(3, "C")
        cols, dash, recs = m5.build_records(
            {}, {m_fcst: {"demand": 9, "code": "3", "part": "C",
                          "담당자": None, "고객": None}},
            {m_blog: {"lead_time": None, "cancel_window": None, "blog_ttl": 1,
                      "monthly": {}, "더존코드": "1", "더존업체명": None, "part": "A"}},
            {m_ship: {"담당자": None, "고객": None, "고객코드": "2", "part": "B", "y2026": 0}},
        )
        mixes = {r["믹스#"] for r in recs}
        self.assertEqual(mixes, {m_blog, m_ship, m_fcst})

    def test_fcst_only_zero_demand_dropped(self):
        # FCST 단독 + demand 0 → 행 생성 안 함. demand>0 → 행 생성 + 식별자 보강.
        m_zero = m5.make_mix(1, "ZERO")
        m_real = m5.make_mix(2, "REAL")
        part_real = m5._norm_part("REAL")
        fcst = {
            m_zero: {"demand": 0, "code": "1", "part": m5._norm_part("ZERO"),
                     "담당자": "담당0", "고객": "고객0"},
            m_real: {"demand": 500, "code": "2", "part": part_real,
                     "담당자": "담당R", "고객": "고객R"},
        }
        _, _, recs = m5.build_records({}, fcst, {}, {})
        mixes = {r["믹스#"] for r in recs}
        self.assertNotIn(m_zero, mixes)
        self.assertIn(m_real, mixes)
        row = next(r for r in recs if r["믹스#"] == m_real)
        self.assertEqual(row["고객코드"], "2")
        self.assertEqual(row["품번"], part_real)
        self.assertEqual(row["고객"], "고객R")
        self.assertEqual(row["담당자"], "담당R")

    def test_qty_shared_across_mix_with_same_part(self):
        part = m5._norm_part("P1")
        inv = {part: 800}
        blog = {
            m5.make_mix(10, "P1"): {"lead_time": None, "cancel_window": None, "blog_ttl": 0,
                                    "monthly": {}, "더존코드": "10", "더존업체명": None, "part": part},
            m5.make_mix(20, "P1"): {"lead_time": None, "cancel_window": None, "blog_ttl": 0,
                                    "monthly": {}, "더존코드": "20", "더존업체명": None, "part": part},
        }
        _, _, recs = m5.build_records(inv, {}, blog, {})
        for r in recs:
            self.assertEqual(r["Q'ty"], 800)

    def test_balance_none_when_shipment_only(self):
        # MIX가 출고내역에만 존재(재고/백록/FCST 없음) → Balance is None
        mix = m5.make_mix(100, "P1")
        ship = {mix: {"담당자": "S", "고객": "C", "고객코드": "100",
                      "part": m5._norm_part("P1"), "y2026": 0}}
        _, _, recs = m5.build_records({}, {}, {}, ship)
        self.assertEqual(len(recs), 1)
        self.assertIsNone(recs[0]["Balance"])

    def test_sorted_by_part_then_customer(self):
        blog = {
            m5.make_mix(1, "ZEBRA"): {"lead_time": None, "cancel_window": None, "blog_ttl": 0,
                                      "monthly": {}, "더존코드": "1", "더존업체명": "z", "part": "ZEBRA"},
            m5.make_mix(2, "ALPHA"): {"lead_time": None, "cancel_window": None, "blog_ttl": 0,
                                      "monthly": {}, "더존코드": "2", "더존업체명": "a", "part": "ALPHA"},
        }
        _, _, recs = m5.build_records({}, {}, blog, {})
        self.assertEqual(recs[0]["품번"], "ALPHA")
        self.assertEqual(recs[1]["품번"], "ZEBRA")


class TestExportWorkbook(unittest.TestCase):
    def test_produces_openable_xlsx_with_all_columns(self):
        rows = [{c: None for c in m5.COLUMNS}]
        rows[0]["믹스#"] = "100P1"
        rows[0]["6월"] = 1234
        rows[0]["2026년"] = 50
        buf = m5.export_workbook(m5.COLUMNS, rows)
        self.assertTrue(buf.getvalue()[:2] == b"PK")  # xlsx(zip) 시그니처
        wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
        ws = wb.active
        # 1행 그룹헤더, 2행 컬럼헤더, 3행부터 데이터
        header_row = [ws.cell(row=2, column=i + 1).value for i in range(len(m5.COLUMNS))]
        self.assertEqual(header_row, m5.COLUMNS)
        # 그룹헤더: 출하이력(2023년 위치), BLOG 2026(PDD기준)(6월 위치)
        y2023_col = m5.COLUMNS.index("2023년") + 1
        jun_col = m5.COLUMNS.index("6월") + 1
        self.assertEqual(ws.cell(row=1, column=y2023_col).value, "출하이력")
        self.assertEqual(ws.cell(row=1, column=jun_col).value, "BLOG 2026(PDD기준)")
        # 데이터 값
        self.assertEqual(ws.cell(row=3, column=m5.COLUMNS.index("6월") + 1).value, 1234)


if __name__ == "__main__":
    unittest.main()
