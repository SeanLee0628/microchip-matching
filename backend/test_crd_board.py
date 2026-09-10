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


# ───────── 2026-09-10 영업1실 요청서 반영분 ─────────

class TestElapsed(unittest.TestCase):
    """경과일수 = MAD - CRD. "경과된 것" 은 경과일수 > 0 (0 은 경과 아님)."""

    def test_delay_zero_is_not_elapsed(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), date(2026, 8, 1))], today=TODAY)
        self.assertEqual(cards[0]["delay_days"], 0)
        self.assertFalse(cards[0]["elapsed"])

    def test_delay_positive_is_elapsed(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), date(2026, 8, 2))], today=TODAY)
        self.assertEqual(cards[0]["delay_days"], 1)
        self.assertTrue(cards[0]["elapsed"])

    def test_mad_earlier_than_crd_is_not_elapsed(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), date(2026, 7, 1))], today=TODAY)
        self.assertEqual(cards[0]["delay_days"], -31)
        self.assertFalse(cards[0]["elapsed"])

    def test_missing_date_is_not_elapsed(self):
        cards = cb.classify_backlog([order(date(2026, 8, 1), None), order(None, date(2026, 8, 1))],
                                    today=TODAY)
        self.assertFalse(any(c["elapsed"] for c in cards))

    def test_elapsed_only_filters(self):
        cards = cb.classify_backlog([
            order(date(2026, 8, 1), date(2026, 8, 1), did="ZERO"),   # 경과 0
            order(date(2026, 8, 1), date(2026, 9, 1), did="LATE"),   # 경과 31
        ], today=TODAY)
        kept = cb.elapsed_only(cards)
        self.assertEqual([c["did"] for c in kept], ["LATE"])


class TestExtraColumnsCarried(unittest.TestCase):
    """PO#·PLANT·BOX_TYPE·CUST 는 판정에 안 쓰지만 표·엑셀에 나가야 한다."""

    def test_original_fields_survive_classification(self):
        o = order(date(2026, 8, 1), date(2026, 9, 1),
                  po="45HWA260203-01", plant="SG15", box_type="DRY PACK",
                  cust="HANWHA VISION", customer="UNITRON KR", delivery_number=None)
        c = cb.classify_backlog([o], today=TODAY)[0]
        self.assertEqual(c["po"], "45HWA260203-01")
        self.assertEqual(c["plant"], "SG15")
        self.assertEqual(c["box_type"], "DRY PACK")
        self.assertEqual(c["cust"], "HANWHA VISION")
        self.assertEqual(c["customer"], "UNITRON KR")


class TestSortByMad(unittest.TestCase):
    def test_mad_ascending_none_last(self):
        cards = cb.classify_backlog([
            order(date(2026, 8, 1), None, did="NOMAD"),
            order(date(2026, 8, 1), date(2026, 9, 14), did="LATER"),
            order(date(2026, 8, 1), date(2026, 9, 7), did="SOONER"),
        ], today=TODAY)
        self.assertEqual([c["did"] for c in cb.sort_by_mad(cards)],
                         ["SOONER", "LATER", "NOMAD"])


class TestCompareChanged(unittest.TestCase):
    """"변화된 것" = GAP != 0 양방향 + 신규 SO. GAP = 현재MAD - 이전MAD."""

    def test_pulled_in_is_changed_with_negative_gap(self):
        # 요청서 예시 R11: 9/9 MAD 09-11, 8/31 MAD 09-14, GAP -3
        prev = [bo("S1", date(2026, 9, 14))]
        cur = [bo("S1", date(2026, 9, 11))]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["summary"]["changed"], 1)
        self.assertEqual(r["changed"][0]["gap_days"], -3)
        self.assertIsNone(r["changed"][0]["slip_days"])
        self.assertEqual(r["summary"]["improved"], 1)

    def test_pushed_out_is_changed_with_positive_gap(self):
        # 요청서 예시 R20: 9/9 MAD 09-14, 8/31 MAD 09-07, GAP +7
        prev = [bo("S1", date(2026, 9, 7))]
        cur = [bo("S1", date(2026, 9, 14))]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["changed"][0]["gap_days"], 7)
        self.assertEqual(r["changed"][0]["slip_days"], 7)

    def test_unchanged_is_not_in_changed(self):
        prev = [bo("S1", date(2026, 9, 7))]
        cur = [bo("S1", date(2026, 9, 7))]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["changed"], [])
        self.assertEqual(r["summary"]["same"], 1)

    def test_new_so_has_na_gap_and_is_flagged(self):
        prev = []
        cur = [bo("S9", date(2026, 9, 14))]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["summary"]["changed"], 1)
        self.assertTrue(r["changed"][0]["is_new"])
        self.assertIsNone(r["changed"][0]["prev_mad"])
        self.assertIsNone(r["changed"][0]["gap_days"])

    def test_changed_sorted_by_mad(self):
        prev = [bo("A", date(2026, 9, 1)), bo("B", date(2026, 9, 1))]
        cur = [bo("A", date(2026, 10, 1)), bo("B", date(2026, 9, 20))]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual([c["so"] for c in r["changed"]], ["B", "A"])  # MAD 빠른 순

    def test_fse_and_cust_pulled_from_prev_by_so(self):
        prev = [bo("S1", date(2026, 9, 1), fse="KATE", cust="SKYHIGH")]
        cur = [bo("S1", date(2026, 9, 8), fse=None, cust=None)]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["changed"][0]["fse"], "KATE")
        self.assertEqual(r["changed"][0]["cust"], "SKYHIGH")

    def test_current_fse_wins_over_prev(self):
        prev = [bo("S1", date(2026, 9, 1), fse="OLD", cust="OLDC")]
        cur = [bo("S1", date(2026, 9, 8), fse="NEW", cust="NEWC")]
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual(r["changed"][0]["fse"], "NEW")
        self.assertEqual(r["changed"][0]["cust"], "NEWC")

    def test_slipped_still_available_for_signal_board(self):
        prev = [bo("S1", date(2026, 8, 1)), bo("S2", date(2026, 9, 1))]
        cur = [bo("S1", date(2026, 9, 1)), bo("S2", date(2026, 8, 1))]  # S1 밀림, S2 당겨짐
        r = cb.compare_backlog(prev, cur, today=TODAY)
        self.assertEqual([c["so"] for c in r["slipped"]], ["S1"])
        self.assertEqual(r["summary"]["changed"], 2)


if __name__ == "__main__":
    unittest.main()
