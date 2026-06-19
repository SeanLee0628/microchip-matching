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


if __name__ == "__main__":
    unittest.main()
