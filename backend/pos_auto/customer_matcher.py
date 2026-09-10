# -*- coding: utf-8 -*-
"""QTN 의 End Customer(영문) → 더존 고객코드 매칭.

자동 확정은 '정확 일치' 계열만. 후보가 여럿이거나 못 찾으면 사람 검토로 보낸다.
유사도(rapidfuzz)는 추천에만 쓰고 확정에는 절대 쓰지 않는다.
"""
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .models import QTN, CustomerMapEntry

try:
    from rapidfuzz import fuzz, process as fuzz_process
    HAS_FUZZ = True
except ImportError:                                    # pragma: no cover
    HAS_FUZZ = False


class CustomerMatcher:
    def __init__(self, entries: List[CustomerMapEntry], namer, cfg: dict,
                 approved: Optional[Dict[str, str]] = None):
        self.entries = entries
        self.namer = namer
        self.cfg = cfg or {}
        self.approved = {k.strip().upper(): v for k, v in (approved or {}).items()}

        self.by_original: Dict[str, set] = defaultdict(set)
        self.by_normalized: Dict[str, set] = defaultdict(set)
        self.by_no_region: Dict[str, set] = defaultdict(set)
        self.by_code: Dict[str, CustomerMapEntry] = {}
        for e in entries:
            self.by_code.setdefault(e.customer_code, e)
            for raw in (e.name_with_region, e.name_without_region):
                if raw:
                    self.by_original[raw.strip().upper()].add(e.customer_code)
                    self.by_normalized[namer.normalize(raw)].add(e.customer_code)
                    self.by_no_region[namer.normalize_without_region(raw)].add(e.customer_code)
        self._choices = sorted({e.name_with_region for e in entries if e.name_with_region})

    # ── 매칭 ────────────────────────────────────────────────────────────
    def match(self, name: str) -> Tuple[Optional[str], str, List[str]]:
        """returns (customer_code, match_status, candidates)"""
        key = (name or "").strip().upper()
        if not key:
            return None, "EMPTY_NAME", []
        if key in self.approved:
            return self.approved[key], "APPROVED_MASTER", [self.approved[key]]

        for table, status in ((self.by_original, "AUTO_EXACT"),
                              (self.by_normalized, "AUTO_NORMALIZED"),
                              (self.by_no_region, "AUTO_NO_REGION")):
            k = key if status == "AUTO_EXACT" else (
                self.namer.normalize(name) if status == "AUTO_NORMALIZED"
                else self.namer.normalize_without_region(name))
            codes = table.get(k)
            if codes:
                if len(codes) == 1:
                    return next(iter(codes)), status, sorted(codes)
                return None, "MULTIPLE", sorted(codes)   # 임의 선택 금지
        return None, "NOT_MATCHED", []

    def suggest(self, name: str, top_n: int = 3) -> List[Tuple[str, str, float]]:
        """추천만 — (코드, 매핑이름, 점수). 확정에는 쓰지 않는다."""
        if not HAS_FUZZ or not name or not self._choices:
            return []
        hits = fuzz_process.extract(name, self._choices, scorer=fuzz.WRatio, limit=top_n)
        out = []
        for matched_name, score, _idx in hits:
            for code in sorted(self.by_original.get(matched_name.strip().upper(), [])):
                out.append((code, matched_name, float(score)))
        return out[:top_n]

    def apply(self, qtns: List[QTN]) -> Tuple[List[QTN], List[dict]]:
        """QTN 리스트에 customer_code 를 채우고, 검토표 행을 만든다."""
        review, seen = [], set()
        min_score = float(self.cfg.get("fuzzy_min_score", 80))
        top_n = int(self.cfg.get("fuzzy_suggest_top_n", 3))
        for q in qtns:
            code, status, cands = self.match(q.end_customer_original)
            q.customer_code, q.match_status = code, status
            if status in ("AUTO_EXACT", "AUTO_NORMALIZED", "AUTO_NO_REGION", "APPROVED_MASTER"):
                continue
            if q.end_customer_original in seen:
                continue
            seen.add(q.end_customer_original)
            sugg = self.suggest(q.end_customer_original, top_n)
            if not sugg:
                sugg = [(c, self.by_code[c].name_with_region if c in self.by_code else "", 100.0)
                        for c in cands]
            for rank, (c, matched_name, score) in enumerate(sugg, 1):
                review.append({
                    "QTN 원본 고객명": q.end_customer_original,
                    "정규화 고객명": q.end_customer_normalized,
                    "추천 고객코드": c,
                    "추천 고객명": self.by_code[c].customer_name_original if c in self.by_code else matched_name,
                    "추천 순위": rank,
                    "유사도": round(score, 1),
                    "추천 근거": ("다중 후보(정확일치)" if status == "MULTIPLE"
                                else ("문자열 유사도" if score < 100 else "이름 일치")),
                    "자동 확정 가능 여부": "아니오",
                    "담당자 선택": "",
                    "담당자 비고": ("여러 고객코드가 후보 — 사람이 골라야 함" if status == "MULTIPLE"
                                 else ("유사도는 참고용, 확정 금지" if score < min_score else "")),
                })
            if not sugg:
                review.append({
                    "QTN 원본 고객명": q.end_customer_original,
                    "정규화 고객명": q.end_customer_normalized,
                    "추천 고객코드": "", "추천 고객명": "", "추천 순위": 0, "유사도": 0,
                    "추천 근거": "후보 없음", "자동 확정 가능 여부": "아니오",
                    "담당자 선택": "", "담당자 비고": "매핑표에 해당 영문명이 없음",
                })
        return qtns, review
