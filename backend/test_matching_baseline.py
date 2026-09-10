"""parse_excel 매칭 로직 검증:
- 2023~2025 는 baseline_shipments.json 고정값에서 (업로드로 재계산 안 함)
- 2026 은 업로드 출고내역에서 동적 합산
- 믹스# 가 비어있으면 고객코드+품번으로 생성
"""
import io
import pandas as pd
import main


def _make_workbook(ship_rows, backlog_rows=None):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(ship_rows).to_excel(w, sheet_name="출고내역", index=False)
        if backlog_rows is not None:
            pd.DataFrame(backlog_rows).to_excel(w, sheet_name="백록260324", index=False)
    buf.seek(0)
    return buf.getvalue()


def _by_mix(records):
    return {r["믹스#"]: r for r in records}


def test_baseline_fixed_and_2026_dynamic():
    # 13111223K256T-I/SN: baseline 2024=3300, 2025=16500, 2023=None
    contents = _make_workbook([
        # 2026 출고 (cutoff 이전) → 2026=500
        {"믹스#": "13111223K256T-I/SN", "출고수량": 500,
         "출고일자": pd.Timestamp("2026-01-15"), "고객코드": 131112, "품번": "23K256T-I/SN"},
        # 2025 출고 → baseline(16500)이 이겨야 함. 절대 더해지면 안 됨.
        {"믹스#": "13111223K256T-I/SN", "출고수량": 99999,
         "출고일자": pd.Timestamp("2025-05-01"), "고객코드": 131112, "품번": "23K256T-I/SN"},
    ])
    _, _, records = main.parse_excel(contents)
    rec = _by_mix(records)["13111223K256T-I/SN"]
    assert rec["2023년"] is None, rec
    assert rec["2024년"] == 3300, rec
    assert rec["2025년"] == 16500, rec  # 업로드 99999 무시, baseline 고정
    assert rec["2026년"] == 500, rec


def test_mix_generated_from_code_and_part():
    contents = _make_workbook([
        {"믹스#": None, "출고수량": 700, "출고일자": pd.Timestamp("2026-02-01"),
         "고객코드": 999999, "품번": "TESTPART"},
    ])
    _, _, records = main.parse_excel(contents)
    rec = _by_mix(records)
    assert "999999TESTPART" in rec, list(rec.keys())
    r = rec["999999TESTPART"]
    assert r["2026년"] == 700, r
    # baseline 에 없는 신규 믹스 → 2023~2025 비어있음
    assert r["2023년"] is None and r["2024년"] is None and r["2025년"] is None, r


def test_baseline_loaded():
    assert len(main.BASELINE_SHIPMENTS) > 800, len(main.BASELINE_SHIPMENTS)
    assert main.BASELINE_SHIPMENTS["13111223K256T-I/SN"]["2025"] == 16500
