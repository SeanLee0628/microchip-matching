# -*- coding: utf-8 -*-
"""CRM '고객사' 내보내기에서 우편번호·시도를 가져온다.

이름 **정확일치**(표기차이만 정규화)로만 연결한다. 유사도 매칭은 쓰지 않는다.
붙지 않은 고객은 주소 마스터 수기 입력 대상으로 남는다.
"""
import re
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from . import normalizer as N


def _norm_company(s) -> str:
    """(주)/㈜/주식회사/괄호주석/공백 차이를 흡수한 비교용 이름."""
    s = N.text(s)
    if not s:
        return ""
    s = s.replace("(주)", "").replace("㈜", "").replace("주식회사", "")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s+", "", s)
    return s.upper()


def _pick(cols, candidates):
    keys = {re.sub(r"\s+", "", str(c)).lower(): c for c in cols}
    for cand in candidates:
        k = re.sub(r"\s+", "", cand).lower()
        if k in keys:
            return keys[k]
    return None


class CrmAddressSource:
    def __init__(self, path: Optional[str], cfg: dict):
        self.cfg = cfg or {}
        self.romanize: Dict[str, str] = self.cfg.get("province_romanization", {})
        self.by_name: Dict[str, dict] = {}
        self.path = Path(path) if path else None
        self.rows = 0
        if not self.path or not self.path.exists():
            return
        df = pd.read_excel(self.path)
        colcfg = self.cfg.get("columns", {})
        c_name = _pick(df.columns, colcfg.get("name", ["고객사명"]))
        c_post = _pick(df.columns, colcfg.get("postal", ["우편번호"]))
        c_addr = _pick(df.columns, colcfg.get("address", ["주소"]))
        if not c_name:
            return
        for _, r in df.iterrows():
            key = _norm_company(r.get(c_name))
            if not key or key in self.by_name:
                continue
            postal = N.text(r.get(c_post)) if c_post else ""
            addr = N.text(r.get(c_addr)) if c_addr else ""
            self.by_name[key] = {
                "postal": postal,
                "address": addr,
                "state": self.province_of(addr),
            }
            self.rows += 1

    def province_of(self, address: str) -> str:
        """'경기도 성남시 …' → 'Gyeonggi-do'. 표에 없으면 빈 문자열(추측하지 않음)."""
        a = N.text(address)
        if not a:
            return ""
        first = a.split()[0]
        return self.romanize.get(first, "")

    def lookup(self, customer_name: str) -> dict:
        return self.by_name.get(_norm_company(customer_name), {})
