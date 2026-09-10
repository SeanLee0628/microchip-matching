# -*- coding: utf-8 -*-
"""정규화 헬퍼. 원본 값은 절대 바꾸지 않고, 비교용 값을 따로 만든다."""
import math
import re
from datetime import date, datetime
from typing import Optional


def text(v) -> str:
    """문자열화 + 앞뒤 공백 제거. NaN/None/'nan' 은 빈 문자열."""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        if v.is_integer():
            return str(int(v))
    s = str(v).strip()
    if s.lower() in ("nan", "none", "nat"):
        return ""
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def code(v) -> str:
    """더존코드: 131112.0 → '131112'."""
    return text(v)


def part(v) -> str:
    """품번 정규화: 모든 공백·줄바꿈 제거 + 대문자.
    끝자리가 다르면 다른 품목이므로 완전일치 비교만 한다(퍼지 매칭 금지)."""
    s = text(v)
    if not s:
        return ""
    return re.sub(r"\s+", "", s).upper()


def number(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        s = re.sub(r"[,\s]", "", text(v))
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def to_date(v) -> Optional[date]:
    """엑셀 날짜 / datetime / YYYYMMDD 8자리 / YYYY-MM-DD 문자열 → date."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, float) and math.isnan(v):
        return None
    s = text(v)
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    return None


class CustomerNameNormalizer:
    """고객명 정규화 — 비교 전용. 원본은 그대로 보존한다."""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.punct = self.cfg.get("strip_punctuation", [])
        self.suffixes = [s.upper() for s in self.cfg.get("legal_suffixes", [])]
        self.sep = self.cfg.get("region_separator", " - ")

    def split_region(self, name: str):
        """'42dot - Seoul' → ('42dot', 'Seoul'). 구분자가 없으면 (원본, '')."""
        s = text(name)
        if self.sep in s:
            head, _, tail = s.rpartition(self.sep)
            return head.strip(), tail.strip()
        return s, ""

    def normalize(self, name: str) -> str:
        s = text(name)
        if not s:
            return ""
        if self.cfg.get("uppercase", True):
            s = s.upper()
        for p in self.punct:
            s = s.replace(p, " ")
        if self.cfg.get("collapse_whitespace", True):
            s = re.sub(r"\s+", " ", s).strip()
        # 법인 표기는 뒤쪽에서만 제거 (회사명 중간의 단어를 지우지 않기 위해)
        changed = True
        while changed:
            changed = False
            for suf in sorted(self.suffixes, key=len, reverse=True):
                if s.endswith(" " + suf):
                    s = s[: -(len(suf) + 1)].strip()
                    changed = True
        return re.sub(r"\s+", " ", s).strip()

    def normalize_without_region(self, name: str) -> str:
        head, _region = self.split_region(name)
        return self.normalize(head)
