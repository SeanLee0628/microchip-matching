# -*- coding: utf-8 -*-
"""검증 — 대사(對査)와 제출 가능 여부 판정."""
from collections import Counter
from typing import Dict, List

from .models import ExceptionRec, ProcessResult

ERROR_MARKERS = ("#REF!", "#VALUE!", "#N/A", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!")


def summarize(results: List[ProcessResult], pos_rows: List[dict], excs: List[ExceptionRec],
              allocator, severity_map: Dict[str, str], period: str) -> List[dict]:
    src_rows = len(results)
    ok = [r for r in results if r.processing_status == "OK"]
    bad = [r for r in results if r.processing_status != "OK"]
    q = lambda rs: sum(float(r.shipment.ship_qty or 0) for r in rs)

    # 단가 적용 집계는 POS 필드 누락 여부와 무관하게 '단가가 정해진 행' 전체를 센다.
    # (ok 만 세면 주소 미입력 때문에 QTN/ASD 건수가 0으로 보여 오해를 부른다)
    qtn_rows = [r for r in results if r.applied_price_type == "QTN"]
    asd_rows = [r for r in results if r.applied_price_type == "ASD"]
    amt = lambda rs: sum(float(r.applied_price or 0) * float(r.shipment.ship_qty or 0) for r in rs)

    codes = Counter(c for r in results for c in r.exception_codes)
    n_err = sum(1 for e in excs if e.severity == "ERROR")
    n_rev = sum(1 for e in excs if e.severity == "REVIEW")

    formula_errors = 0
    for row in pos_rows:
        for v in row.values():
            if isinstance(v, str) and any(m in v for m in ERROR_MARKERS):
                formula_errors += 1

    qty_match = abs(q(ok) + q(bad) - q(results)) < 1e-6
    row_match = (len(ok) + len(bad) == src_rows)
    neg_pools = allocator.negative_pools() if allocator else {}
    submit = "YES" if (n_err == 0 and n_rev == 0 and qty_match and row_match
                       and not neg_pools and formula_errors == 0 and pos_rows) else "NO"

    def row(k, v):
        return {"항목": k, "값": v}

    return [
        row("대상 기간", period),
        row("원본 출고 행 수", src_rows),
        row("정상 처리 행 수", len(ok)),
        row("예외 행 수", len(bad)),
        row("POS 생성 행 수", len(pos_rows)),
        row("원본 출고수량 합계", q(results)),
        row("정상 POS 수량 합계", q(ok)),
        row("예외 출고수량 합계", q(bad)),
        row("수량 대사(정상+예외=원본)", "일치" if qty_match else "불일치"),
        row("행수 대사(정상+예외=원본)", "일치" if row_match else "불일치"),
        row("QTN 적용 건수", len(qtn_rows)),
        row("QTN 적용 수량", q(qtn_rows)),
        row("QTN 적용 금액", round(amt(qtn_rows), 4)),
        row("ASD 적용 건수", len(asd_rows)),
        row("ASD 적용 수량", q(asd_rows)),
        row("ASD 적용 금액", round(amt(asd_rows), 4)),
        row("단가 없음 건수", codes.get("NO_VALID_PRICE", 0)),
        row("고객 미매칭 건수", codes.get("CUSTOMER_NOT_MATCHED", 0)),
        row("고객 다중후보 건수", codes.get("CUSTOMER_MULTIPLE_MATCHES", 0)),
        row("중복 출고 건수", codes.get("DUPLICATE_SHIPMENT", 0)),
        row("반품(마이너스) 건수", codes.get("RETURN_REVIEW", 0)),
        row("필수 POS 필드 누락 건수", codes.get("MISSING_POS_FIELD", 0)),
        row("QTN 잔여수량 음수 건수", len(neg_pools)),
        # 라벨에 오류 마커 문자열을 넣지 않는다 — 결과 파일 검사에 그대로 걸린다.
        row("출력 수식오류 셀 수", formula_errors),
        row("ERROR 예외 수", n_err),
        row("REVIEW 예외 수", n_rev),
        row("최종 제출 가능 여부", submit),
    ] + [row(f"[예외코드] {c}", n) for c, n in sorted(codes.items(), key=lambda x: -x[1])]


def submit_ok(summary_rows: List[dict]) -> bool:
    for r in summary_rows:
        if r["항목"] == "최종 제출 가능 여부":
            return r["값"] == "YES"
    return False
