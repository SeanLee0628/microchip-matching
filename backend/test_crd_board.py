# -*- coding: utf-8 -*-
"""CRD 신호등 보드 순수 로직 테스트 (stdlib unittest, no deps).
데이터 소스: Backlog Shipment Report (행마다 CRD + MAD).
위험 = MAD(자재 가용일) vs CRD(고객 요청일).
실행: python -m unittest test_crd_board -v   (backend/ 에서)
"""
import unittest
from datetime import date

import crd_board as cb

TODAY = date(2026, 5, 27)


def order(crd, mad, **kw):
    base = {"customer": "MOBIS", "did": "D1", "mpn": "M1", "qty": 1000,
            "crd": crd, "mad": mad, "order_type": "OR"}
    base.update(kw)
    return base


class TestClassifyBacklog(unittest.TestCase):
    def test_mad_on_or_before_crd_is_green(self):
        cards = cb.classify_backlog([
            order(date(2026, 8, 1), date(2026, 8, 1)),   # 딱 맞음
            order(date(2026, 8, 1), date(2026, 7, 20)),  # 더 빠름
        ], today=TODAY)
        self.assertEqual(cards[0]["risk"], "green")
        self.assertEqual(cards[1]["risk"], "green")

    def test_mad_within_buffer_is_yellow(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), date(2026, 8, 6))],
                                    today=TODAY, buffer_days=7)
        self.assertEqual(cards[0]["risk"], "yellow")
        self.assertEqual(cards[0]["delay_days"], 5)

    def test_mad_far_after_crd_is_red(self):
        cards = cb.classify_backlog([order(date(2026, 3, 16), date(2026, 10, 8))],
                                    today=TODAY)
        self.assertEqual(cards[0]["risk"], "red")
        self.assertGreater(cards[0]["delay_days"], 7)

    def test_crd_missing_is_unknown(self):
        cards = cb.classify_backlog([order(None, date(2026, 8, 1))], today=TODAY)
        self.assertEqual(cards[0]["risk"], "unknown")

    def test_mad_missing_is_red(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), None)], today=TODAY)
        self.assertEqual(cards[0]["risk"], "red")
        self.assertIn("MAD", cards[0]["reason"])

    def test_overdue_crd_flagged(self):
        # CRD 가 오늘 이전 → overdue True
        cards = cb.classify_backlog([order(date(2026, 3, 16), date(2026, 10, 8))],
                                    today=TODAY)
        self.assertTrue(cards[0]["overdue"])

    def test_order_type_carried(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), date(2026, 8, 1), order_type="FD")],
                                    today=TODAY)
        self.assertEqual(cards[0]["order_type"], "FD")

    def test_sort_red_first_unknown_last_then_crd(self):
        cards = cb.classify_backlog([
            order(None, date(2026, 8, 1)),                       # unknown
            order(date(2026, 9, 1), date(2026, 9, 1)),           # green
            order(date(2026, 7, 1), date(2027, 1, 1)),           # red, CRD 7/1
            order(date(2026, 6, 1), date(2027, 1, 1)),           # red, CRD 6/1 (먼저)
        ], today=TODAY)
        self.assertEqual(cards[0]["risk"], "red")
        self.assertEqual(cards[0]["crd"], date(2026, 6, 1))      # 같은 red 중 CRD 빠른 게 위
        self.assertEqual(cards[1]["crd"], date(2026, 7, 1))
        self.assertIsNone(cards[-1]["crd"])                      # unknown 맨 아래


class TestFseCarried(unittest.TestCase):
    def test_fse_passthrough(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), date(2026, 8, 1), fse="RICK")],
                                    today=TODAY)
        self.assertEqual(cards[0]["fse"], "RICK")


class TestSummarizeByPart(unittest.TestCase):
    def test_groups_and_sorts_by_red_count(self):
        cards = cb.classify_backlog([
            order(date(2026, 3, 1), date(2027, 1, 1), did="A", mpn="MA", qty=1000),  # red
            order(date(2026, 3, 1), date(2027, 1, 1), did="A", mpn="MA", qty=2000),  # red (같은 부품)
            order(date(2026, 9, 1), date(2026, 9, 1), did="A", mpn="MA"),            # green (같은 부품)
            order(date(2026, 3, 1), date(2027, 1, 1), did="B", mpn="MB", qty=500),   # red (다른 부품)
        ], today=TODAY)
        parts = cb.summarize_by_part(cards)
        # A/MA 가 red 2건으로 1위, B/MB 가 red 1건으로 2위
        self.assertEqual((parts[0]["did"], parts[0]["mpn"]), ("A", "MA"))
        self.assertEqual(parts[0]["red"], 2)
        self.assertEqual(parts[0]["green"], 1)
        self.assertEqual(parts[0]["red_qty"], 3000)   # 1000+2000
        self.assertEqual((parts[1]["did"], parts[1]["mpn"]), ("B", "MB"))
        self.assertEqual(parts[1]["red"], 1)

    def test_top_n_limit(self):
        cards = cb.classify_backlog(
            [order(date(2026, 3, 1), date(2027, 1, 1), did=f"D{i}", mpn=f"M{i}") for i in range(15)],
            today=TODAY)
        self.assertEqual(len(cb.summarize_by_part(cards, top=5)), 5)


def bo(so, mad, **kw):
    """backlog order helper for compare tests."""
    base = {"so": so, "did": "D", "mpn": "M", "customer": "C", "qty": 1000,
            "crd": date(2026, 8, 1), "mad": mad, "fse": "F", "order_type": "OR"}
    base.update(kw)
    return base


class TestCompareBacklog(unittest.TestCase):
    def test_slipped_detected_and_sorted_by_days(self):
        prev = [bo("S1", date(2026, 8, 1)), bo("S2", date(2026, 8, 1))]
        cur = [bo("S1", date(2026, 9, 1)), bo("S2", date(2026, 8, 15))]  # S1 +31, S2 +14
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["summary"]["slipped"], 2)
        self.assertEqual(r["slipped"][0]["so"], "S1")              # 더 많이 밀린 게 위
        self.assertEqual(r["slipped"][0]["slip_days"], 31)
        self.assertEqual(r["slipped"][0]["prev_mad"], date(2026, 8, 1))
        self.assertEqual(r["slipped"][0]["mad"], date(2026, 9, 1))

    def test_improved_and_same_counted(self):
        prev = [bo("S1", date(2026, 9, 1)), bo("S2", date(2026, 8, 1))]
        cur = [bo("S1", date(2026, 8, 1)), bo("S2", date(2026, 8, 1))]  # S1 개선, S2 동일
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["summary"]["improved"], 1)
        self.assertEqual(r["summary"]["same"], 1)
        self.assertEqual(r["summary"]["slipped"], 0)

    def test_new_and_gone(self):
        prev = [bo("S1", date(2026, 8, 1)), bo("S2", date(2026, 8, 1))]
        cur = [bo("S1", date(2026, 8, 1)), bo("S3", date(2026, 8, 1))]  # S2 사라짐, S3 신규
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["summary"]["new"], 1)
        self.assertEqual(r["summary"]["gone"], 1)
        self.assertEqual(r["new"][0]["so"], "S3")

    def test_indefinite_flag_for_far_future_mad(self):
        prev = [bo("S1", date(2026, 9, 1))]
        cur = [bo("S1", date(2030, 12, 31))]   # 2년 넘게 미래 → 무기한
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertTrue(r["slipped"][0]["indefinite"])
        self.assertEqual(r["summary"]["indefinite"], 1)

    def test_missing_mad_not_counted_as_slip(self):
        prev = [bo("S1", None)]
        cur = [bo("S1", date(2026, 9, 1))]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["summary"]["slipped"], 0)


if __name__ == "__main__":
    unittest.main()
