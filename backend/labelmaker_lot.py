#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MOBIS shipping lot management 시트 파서.

시트는 자재별 4열 블록이 가로로 반복: [입고일, 입고처, DATE CODE, LOT(=MPN헤더)].
녹색 LOT 열(D,H,L,P,...) 에서 각 LOT을 뽑아 라벨 데이터로 평탄화한다.
"""
from __future__ import annotations
import io, re, openpyxl

SHEET = "MOBIS shipping lot management"   # 원본 Mobis 출고내역 파일의 시트명

def decrypt(path, password):
    import msoffcrypto
    with open(path, "rb") as f:
        off = msoffcrypto.OfficeFile(f)
        off.load_key(password=password)
        buf = io.BytesIO()
        off.decrypt(buf)
    buf.seek(0)
    return buf

def material_code(mobis_id):
    return re.sub(r"[^0-9A-Za-z]", "", str(mobis_id or ""))

_LOTDC = re.compile(r"^\s*(\d{4,6})\s*\(([^)]+)\)\s*$")

def parse_lot_cell(cell, datecode_col):
    """LOT 셀 → (datecode, lot). '201442(BYM1JQQ.21)' → ('201442','BYM1JQQ.21').
    순수 LOT이면 datecode는 옆 DATE CODE 열 값 사용."""
    s = "" if cell is None else str(cell).strip()
    if not s:
        return None
    m = _LOTDC.match(s)
    if m:
        return m.group(1), m.group(2)
    dc = "" if datecode_col is None else str(datecode_col).strip()
    dc = re.sub(r"\D", "", dc)
    return dc, s

def looks_mobis(x):
    s = str(x or "").strip()
    return bool(re.match(r"^M\d", s))


_DATE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")


def parse_date(v):
    """입고일 셀 → 'YYYY-MM-DD'. 날짜가 아니면 빈 문자열.

    시트의 4열 블록 가정이 일부 자재에서 어긋나 이 칸에 LOT 문자열이 들어오기도 한다.
    그대로 두면 최근순 정렬이 통째로 깨진다 (문자열 정렬이라 'DRP7HKL.41' 이 맨 위로 온다).
    """
    import datetime as _dt
    if v is None:
        return ""
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%Y-%m-%d")
    m = _DATE.search(str(v))
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    try:
        return _dt.date(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return ""

_RECV_HDRS = ("납품일", "입고일")


def _hdr(c):
    return str(c or "").strip().upper().replace(" ", "").replace("'", "")


def col_kind(cell):
    """헤더 셀 → 열 종류. LOT 열은 헤더가 MPN(부품번호)이라 '나머지 전부'로 잡는다."""
    h = _hdr(cell)
    if not h:
        return None
    if h in ("납품일", "입고일"):
        return "recv"
    if h in ("납품처", "입고처"):
        return "src"
    if h == "DATECODE":
        return "dc"
    if h.startswith("수량") or h.startswith("QTY") or h == "QUANTITY":
        return "qty"
    if h.startswith("MSL"):
        return "msl"
    return "lot"


def parse_num(v):
    """수량/MSL 셀 → 문자열. 2500.0 → '2500', '1,000' → '1000', 빈칸 → ''."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    if not s:
        return ""
    try:
        f = float(s.replace(",", ""))
    except ValueError:
        return s
    return str(int(f)) if f.is_integer() else str(f)


_LOT_HDRS = ("LOT", "LOT#", "LOTNO", "LOT번호")
_SCAN_ROWS = 30          # 헤더는 시트 위쪽에 있다. 그 아래는 데이터.


def blocks(rows):
    """자재 블록을 찾는다 → [(헤더행, 시작열, 끝열), ...].

    행 위치를 고정하지 않는다. '납품일'/'입고일' 이 있는 칸이 블록의 헤더다.
    파일마다 위에 제목 행이 붙거나(예: '기존'/'변경') 부품번호가 별도 행에 있어서,
    '1행=MOBIS ID, 2행=헤더' 로 못 박으면 깨진다.
    같은 시트 안에서 블록마다 헤더 행 높이가 달라도 된다.
    """
    width = max((len(r) for r in rows[:_SCAN_ROWS]), default=0)
    out = []
    for ri, row in enumerate(rows[:_SCAN_ROWS]):
        starts = [ci for ci, c in enumerate(row) if str(c or "").strip() in _RECV_HDRS]
        for k, s in enumerate(starts):
            out.append((ri, s, starts[k + 1] if k + 1 < len(starts) else width))
    return out


def cell_at(rows, ri, ci):
    row = rows[ri] if ri < len(rows) else ()
    return row[ci] if ci < len(row) else None


def block_head(rows, hr, c0, c1):
    """헤더 행 위쪽에서 MOBIS ID 와 부품번호(V/PN)를 찾는다.

    새 포맷: MOBIS ID 행 → 부품번호 행 → 헤더 행. 부품번호가 헤더가 아니다.
    옛 포맷: MOBIS ID 행 → 헤더 행, 부품번호가 LOT 열의 헤더.
    """
    mobis = mobis_row = None
    for ri in range(hr - 1, -1, -1):
        for ci in range(c0, c1):
            v = cell_at(rows, ri, ci)
            if looks_mobis(v):
                mobis, mobis_row = str(v).strip(), ri
                break
        if mobis:
            break
    if not mobis:
        return None, None

    # 부품번호: 헤더 행 바로 위부터, MOBIS ID 행은 건너뛰고 처음 나오는 문자열.
    for ri in range(hr - 1, -1, -1):
        if ri == mobis_row:
            continue
        for ci in range(c0, c1):
            v = str(cell_at(rows, ri, ci) or "").strip()
            if v and not looks_mobis(v):
                return mobis, v
    return mobis, None


def is_lot_sheet(rows):
    """LOT 블록 시트인가? = 쓸 수 있는 블록이 하나라도 있나 (헤더 + 그 위 MOBIS ID)."""
    return bool(block_specs(rows))


def pick_sheet(wb):
    """LOT 시트를 고른다. 시트 이름은 파일마다 다르다
    (Mobis 출고내역 = 'MOBIS shipping lot management', 덕산 납품 LOT# = 'LOT').
    이름이 맞으면 그걸 쓰고, 아니면 구조로 찾는다."""
    if SHEET in wb.sheetnames:
        return list(wb[SHEET].iter_rows(values_only=True))
    for name in wb.sheetnames:
        rows = list(wb[name].iter_rows(values_only=True))
        if is_lot_sheet(rows):
            return rows
    raise ValueError(
        "LOT 시트를 찾을 수 없습니다. 자재 블록마다 '납품일'(또는 '입고일') 헤더가 있고, "
        f"그 위에 MOBIS ID(M으로 시작)와 부품번호가 있어야 합니다. 있는 시트: {wb.sheetnames}")


def block_specs(rows):
    """블록 → dict(mobis, vpn, cols{recv,src,dc,lot,qty,msl}, first_row). 못 쓰는 블록은 버린다."""
    specs = []
    for hr, c0, c1 in blocks(rows):
        cols = {}
        for ci in range(c0, c1):
            kind = col_kind(cell_at(rows, hr, ci))
            if kind and kind not in cols:
                cols[kind] = ci
        if "lot" not in cols:
            continue
        mobis, vpn = block_head(rows, hr, c0, c1)
        if not mobis:
            continue
        lot_hdr = _hdr(cell_at(rows, hr, cols["lot"]))
        if lot_hdr not in _LOT_HDRS:      # 옛 포맷: LOT 열의 헤더가 곧 부품번호
            vpn = str(cell_at(rows, hr, cols["lot"])).strip()
        if not vpn:
            continue
        cols.setdefault("recv", c0)
        specs.append(dict(mobis=mobis, vpn=vpn, cols=cols, first_row=hr + 1))
    return specs


def parse(workbook_buf):
    wb = openpyxl.load_workbook(workbook_buf, read_only=True, data_only=True)
    rows = pick_sheet(wb)
    out = []
    for spec in block_specs(rows):
        cols, mcode = spec["cols"], material_code(spec["mobis"])

        def cell(ri, key):
            ci = cols.get(key)
            return cell_at(rows, ri, ci) if ci is not None else None

        for ri in range(spec["first_row"], len(rows)):
            parsed = parse_lot_cell(cell(ri, "lot"), cell(ri, "dc"))
            if not parsed:
                continue
            datecode, lot = parsed
            out.append(dict(mobis_id=spec["mobis"], material_code=mcode, vpn=spec["vpn"],
                            datecode=datecode, lot=lot,
                            received=parse_date(cell(ri, "recv")),
                            qty=parse_num(cell(ri, "qty")),
                            msl=parse_num(cell(ri, "msl")),
                            row=ri + 1))
    wb.close()
    return out


if __name__ == "__main__":
    import sys, collections
    path = r"C:\Users\user\AppData\Local\Microsoft\Olk\Attachments\ooa-05c63a9f-9693-4dd2-8ce7-c04745b9bf11\38ce4679f03094c58f346f7c42095027ba3e9c68a59d05b3c5828f5a8934bc5d\Mobis 출고내역(0709).xlsx"
    buf = decrypt(path, "9178")
    lots = parse(buf)
    print("총 LOT 추출:", len(lots))
    by_mobis = collections.Counter(l["mobis_id"] for l in lots)
    print("자재 종류:", len(by_mobis))
    print("--- 샘플 8건 ---")
    for l in lots[:8]:
        print(f"  {l['mobis_id']} | {l['material_code']} | {l['vpn'][:22]:22} | dc={l['datecode']:>6} | LOT={l['lot']}")
    # 현재 부품(MTFC64GAZAOTD-AAT) 있나?
    cur = [l for l in lots if "MTFC64GAZAOTD" in l["vpn"]]
    print(f"--- MTFC64GAZAOTD-AAT LOT: {len(cur)}건 (최근 5) ---")
    for l in cur[-5:]:
        print(f"  {l['material_code']} | dc={l['datecode']} | LOT={l['lot']} | serial={l['datecode'][-4:]+'00'+l['lot'] if l['datecode'] else '?'}")
