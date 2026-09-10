# -*- coding: utf-8 -*-
"""엑셀 파일 열기 — 읽기 전용. 원본은 절대 수정하지 않는다."""
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


class Workbook:
    """시트 탐색 + 헤더 자동 탐지를 담당. openpyxl read_only 로만 연다."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"파일이 없습니다: {self.path}")
        self.xls = pd.ExcelFile(self.path, engine="openpyxl")
        self.sha256 = file_sha256(self.path)
        self.mtime_before = self.path.stat().st_mtime

    @property
    def sheet_names(self) -> List[str]:
        return list(self.xls.sheet_names)

    def find_sheet(self, candidates: List[str]) -> Optional[str]:
        """후보 이름과 부분일치하는 첫 시트. 정확일치를 우선한다."""
        keys = [_key(c) for c in candidates]
        for s in self.sheet_names:
            if _key(s) in keys:
                return s
        for s in self.sheet_names:
            ks = _key(s)
            for k in keys:
                if k and k in ks:
                    return s
        return None

    def read(self, sheet: str, col_candidates: Dict[str, List[str]],
             scan_rows: int = 6) -> Tuple[pd.DataFrame, Dict[str, str], int]:
        """헤더 행을 자동 탐지해 DataFrame + {표준명: 실제컬럼명} 을 돌려준다.
        필수 컬럼을 가장 많이 만족하는 행을 헤더로 고른다."""
        best = (None, {}, -1)
        for hdr in range(scan_rows):
            try:
                df = pd.read_excel(self.xls, sheet_name=sheet, header=hdr, nrows=200)
            except Exception:
                continue
            cols = {_key(c): str(c) for c in df.columns}
            mapping, hit = {}, 0
            for std, cands in col_candidates.items():
                for c in cands:
                    if _key(c) in cols:
                        mapping[std] = cols[_key(c)]
                        hit += 1
                        break
            if hit > best[2]:
                best = (hdr, mapping, hit)
        hdr_row, mapping, _hit = best
        if hdr_row is None:
            raise ValueError(f"'{sheet}' 시트를 읽지 못했습니다.")
        df = pd.read_excel(self.xls, sheet_name=sheet, header=hdr_row)
        return df, mapping, hdr_row

    def unchanged(self) -> bool:
        """원본이 실행 중 변경되지 않았는지 확인."""
        return self.path.stat().st_mtime == self.mtime_before
