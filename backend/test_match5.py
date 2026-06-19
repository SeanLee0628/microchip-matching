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


if __name__ == "__main__":
    unittest.main()
