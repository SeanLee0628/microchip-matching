# -*- coding: utf-8 -*-
"""QTN 잔여수량 누적 차감. Quote Item 단위로 별도 수량 풀을 관리한다."""
from collections import defaultdict
from typing import Dict, List

from .models import QTN


class QTNAllocator:
    def __init__(self, qtns: List[QTN]):
        # 같은 Quote 라도 Quote Item 이 다르면 별도 풀
        self.pool: Dict[str, float] = {}
        self.ledger: List[dict] = []
        self.by_key = defaultdict(list)
        for q in qtns:
            self.pool[q.qtn_id] = float(q.original_remains or 0)
            q.remaining_qty = self.pool[q.qtn_id]
            if q.customer_code:
                self.by_key[(q.customer_code, q.part_number)].append(q)
        for lst in self.by_key.values():
            lst.sort(key=lambda q: q.source_row)

    def candidates(self, customer_code: str, part_number: str) -> List[QTN]:
        out = self.by_key.get((customer_code, part_number), [])
        for q in out:                      # 현재 잔량을 반영
            q.remaining_qty = self.pool[q.qtn_id]
        return out

    def consume(self, q: QTN, qty: float, shipment_id: str, ship_date) -> dict:
        before = self.pool[q.qtn_id]
        if qty > before:
            raise ValueError(f"잔여수량 초과 차감 시도: {q.qtn_id} {qty} > {before}")
        after = before - qty
        self.pool[q.qtn_id] = after
        q.remaining_qty = after
        rec = {
            "QTN 번호": q.quote_number, "Quote Item": q.quote_item_number,
            "고객코드": q.customer_code or "", "품번": q.part_number,
            "초기 잔여수량": float(q.original_remains or 0),
            "적용 출고 고유키": shipment_id,
            "적용일": ship_date, "사용수량": qty, "적용 후 잔여수량": after,
        }
        self.ledger.append(rec)
        return rec

    def negative_pools(self) -> Dict[str, float]:
        return {k: v for k, v in self.pool.items() if v < -1e-9}
