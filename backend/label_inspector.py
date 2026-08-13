#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""부품 라벨 사진 검수 프로그램.

라벨 사진 → Claude Opus 4.8 비전이 모든 필드 추출 → Apr inventory 마스터와 대조
→ 라벨별 매칭 퍼센트 + 항목별 ✅/❌ → localhost 대시보드.

데이터를 있는 그대로 추출·대조만 한다(매수/매도·평가 판단 없음).
"""
from __future__ import annotations

import argparse
import base64
import functools
import io
import json
import os
import re
import socket
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from string import Template

import openpyxl
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
DEFAULT_MASTER = os.path.join(DOWNLOADS, "APR_Inventory_반영_2026-04 (3).xlsx")
# Part# ↔ MOBIS ID 보강 매핑. 서버(EB)에는 모듈 옆에 동봉된 사본을, 로컬에선 Downloads 본을 사용.
_MOBIS_LOCAL = os.path.join(HERE, "mobis id list.xlsx")
MOBIS_LIST = _MOBIS_LOCAL if os.path.exists(_MOBIS_LOCAL) else os.path.join(DOWNLOADS, "mobis id list.xlsx")
DEFAULT_IMAGES = [
    os.path.join(DOWNLOADS, "KakaoTalk_20260611_102036953_03.jpg"),
    os.path.join(DOWNLOADS, "KakaoTalk_20260316_140806975_16.jpg"),
    os.path.join(DOWNLOADS, "KakaoTalk_20260316_140839528_23.jpg"),
]
ENV_FILE = os.path.join(os.path.expanduser("~"), "stock-research", ".env")
MODEL = "claude-opus-4-8"


def fail(msg):
    print(f"[오류] {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- API 키
def load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    fail(f"ANTHROPIC_API_KEY 를 찾을 수 없습니다. 환경변수나 {ENV_FILE} 에 넣어주세요.")


# ---------------------------------------------------------------- 정규화
def norm(x):
    """대시·콜론·공백·대소문자 무시 비교용."""
    return re.sub(r"[^A-Za-z0-9]", "", str(x or "").upper())


def clean(x):
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s in (".", "-", "nan") else s


def within_one(a, b):
    """편집거리(삽입/삭제/치환) 1 이하 — OCR 한 글자 오독 허용용."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:                                    # 치환 1개
        return sum(c1 != c2 for c1, c2 in zip(a, b)) == 1
    if la > lb:                                     # a 를 짧은 쪽으로
        a, b = b, a
    i = j = 0
    skipped = False                                 # 삽입/삭제 1개 허용
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
        elif skipped:
            return False
        else:
            skipped = True; j += 1
    return True


# ---------------------------------------------------------------- 마스터 적재
def load_master(path):
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        import msoffcrypto
        with open(path, "rb") as f:
            off = msoffcrypto.OfficeFile(f); off.load_key(password=os.environ.get("MASTER_PW", ""))
            buf = io.BytesIO(); off.decrypt(buf)
        wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    if "Apr inventory" not in wb.sheetnames:
        fail("'Apr inventory' 시트를 찾을 수 없습니다.")
    ws = wb["Apr inventory"]
    hdr = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    H = {str(c).strip(): i for i, c in enumerate(hdr) if c}
    need = ["Part#", "MOBIS ID", "VENDER", "FAB", "FAMILY", "Package", "Q'ty", "MOQ"]
    idx = {k: H.get(k) for k in need}
    by_part = {}
    by_mobis = {}
    # 부품번호 하나에 MOBIS ID 가 여러 개 걸리는 경우가 있다 (혼용 품목).
    # 예: MTFC64GAZAQHD-AAT → M3203-001387 / M3203-0013871 / M3203-0013872
    # 이런 부품은 MOBIS ID 를 'OCR 오독'으로 보고 고쳐 쓰면 안 된다.
    alts = {}                       # norm(부품번호) -> [MOBIS ID, ...]
    for r in ws.iter_rows(min_row=3, values_only=True):
        def g(k):
            i = idx.get(k)
            return r[i] if i is not None and i < len(r) else None
        part = clean(g("Part#"))
        if not part:
            continue
        rec = dict(
            part=part, mobis=clean(g("MOBIS ID")), vender=clean(g("VENDER")),
            fab=clean(g("FAB")), family=clean(g("FAMILY")), package=clean(g("Package")),
            moq=clean(g("MOQ")),
        )
        by_part.setdefault(norm(part), rec)
        if rec["mobis"]:
            by_mobis.setdefault(norm(rec["mobis"]), rec)
            alts.setdefault(norm(part), [])
            if rec["mobis"] not in alts[norm(part)]:
                alts[norm(part)].append(rec["mobis"])
    wb.close()

    # 보강: mobis id list.xlsx (Part# ↔ MOBIS ID). Apr inventory 에 없는 부품을 추가 등록.
    mlist = os.environ.get("MOBIS_LIST", MOBIS_LIST)
    added = 0
    if os.path.exists(mlist):
        try:
            lwb = openpyxl.load_workbook(mlist, read_only=True, data_only=True)
            lws = lwb["Sheet1"] if "Sheet1" in lwb.sheetnames else lwb.worksheets[0]
            lhdr = next(lws.iter_rows(min_row=1, max_row=1, values_only=True))
            LH = {str(c).strip(): i for i, c in enumerate(lhdr) if c}
            pi, mi = LH.get("Part#", 0), LH.get("MOBIS ID", 1)
            for r in lws.iter_rows(min_row=2, values_only=True):
                part = clean(r[pi]) if pi < len(r) else ""
                mob = clean(r[mi]) if mi < len(r) else ""
                if not part:
                    continue
                rec = dict(part=part, mobis=mob, vender="", fab="", family="", package="", moq="")
                if norm(part) not in by_part:
                    by_part[norm(part)] = rec; added += 1
                if mob:
                    if norm(mob) not in by_mobis:
                        by_mobis[norm(mob)] = rec
                    alts.setdefault(norm(part), [])
                    if mob not in alts[norm(part)]:
                        alts[norm(part)].append(mob)
            lwb.close()
            print(f"mobis id list 보강: +{added}종 추가")
        except Exception as e:  # noqa: BLE001
            print(f"mobis id list 로드 실패(무시): {type(e).__name__}: {e}")

    # 각 부품 레코드에 '이 부품이 쓰는 MOBIS ID 전부' 를 붙여 둔다.
    for k, rec in by_part.items():
        rec["mobis_alts"] = alts.get(k, [])
    mixed = sum(1 for v in alts.values() if len(v) > 1)
    if mixed:
        print(f"MOBIS ID 혼용 부품: {mixed}종 (이 부품들은 ID 자동보정 안 함)")
    return by_part, by_mobis


# ---------------------------------------------------------------- 비전 추출
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "vpn": {"type": "string", "description": "V/PN = 제조사 부품번호(MPN). 예: MTFC256GAVATTC-AAT, NEO-M9L-01A"},
                    "material_code": {"type": "string", "description": "MATERIAL CODE. 예: M3203001351"},
                    "vender": {"type": "string", "description": "제조 브랜드. 예: Micron, u-blox, Fujitsu (로고/문구로 판단)"},
                    "quantity": {"type": "string", "description": "QUANTITY 수량. 예: 500, 1500"},
                    "maker_code": {"type": "string", "description": "MAKER CODE. 예: SJYV"},
                    "fab": {"type": "string", "description": "FAB 번호. 예: 10, 11. 없으면 빈칸"},
                    "stock_day": {"type": "string", "description": "STOCK DAY. 예: 20260611"},
                    "lot": {"type": "string", "description": "LOT 번호"},
                    "serial": {"type": "string", "description": "Unitrontech 흰색 라벨의 'SERIAL :' 값. 보이는 그대로."},
                    "serial_maker": {"type": "string", "description": "제조사 라벨에 인쇄된 릴/박스 일련번호. 보통 맨 아래 바코드 옆에 '2622-V25 1500' 처럼 숫자4자리+대시 형태로 찍혀 있다. Unitrontech SERIAL 의 앞 4자리와 짝이 된다. 없으면 빈칸"},
                    "ordering_code": {"type": "string", "description": "Ordering Code (있으면)"},
                    "msl_level": {"type": "string", "description": "MSL LEVEL. 예: 3"},
                    "lot_maker": {"type": "string", "description": "제조사 라벨에서 Unitrontech LOT와 '동일한' LOT 코드(예: MN262220355 처럼). 보통 바코드 옆/아래 작은 글씨로 똑같이 다시 인쇄돼 있음 — 작더라도 끝까지 찾을 것. Batch(B026210010 같은 B-시작 코드)·Ordering Code(부품번호)·Quantity·Date·Reel번호는 LOT가 아니니 절대 넣지 말 것. 정말 없을 때만 빈칸"},
                    "part_maker": {"type": "string", "description": "제조사(Micron/Samsung 등) 라벨에 인쇄된 '그 제조사 자신의' 부품번호(MPN). 보통 제조사 라벨의 큰 글씨 또는 바코드 아래에 있음 (예: MTFC64GBCAVAL-AAT). 이건 Unitrontech 흰색 라벨의 V/PN 과 짝이 되며 정상이라면 서로 같아야 한다. Unitrontech V/PN(vpn)이 아니라 '제조사 라벨 쪽에 실제로 찍힌 부품번호'를 그대로 읽어라. 제조사 라벨이 안 보이거나 부품번호가 없으면 빈칸"},
                    "label_count": {"type": "integer", "description": "이 라벨(같은 품목·같은 내용)이 사진에 실제로 몇 장 보이는가. 세는 단위는 Unitrontech 흰색 라벨 1장 = 1 (릴/박스 1개). 흰색+제조사 라벨이 한 릴에 같이 붙어 있어도 1. 사진에 라벨이 1장뿐이면 1"},
                },
                "required": ["vpn", "material_code", "vender", "quantity", "maker_code",
                             "fab", "stock_day", "lot", "serial", "serial_maker", "ordering_code", "msl_level",
                             "lot_maker", "part_maker", "label_count"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}

PROMPT = """이 사진에 보이는 부품 라벨(릴/박스 라벨)을 모두 찾아 각 라벨의 필드를 정확히 읽어줘.

읽기 주의(매우 중요):
- 라벨이 옆으로 90도 누워있거나 거꾸로일 수 있다. 머릿속으로 똑바로 돌려서 읽어라.
- 글씨가 작으니 MATERIAL CODE 와 V/PN(부품번호)은 **한 글자씩 또박또박** 확인하고, 읽은 뒤 한 번 더 검산해라.
- 숫자/문자 혼동 주의: 0↔O, 1↔I↔L, 2↔Z, 3↔8, 4↔A, 5↔S, 6↔G, 9↔g. 특히 MATERIAL CODE 의 숫자를 헷갈리지 마라.
- **연속된 같은 숫자의 개수를 정확히 세라 (가장 흔한 오독!).** 00·11·000 처럼 같은 숫자가 붙어있을 때 그 개수를 하나 더 많게/적게 읽는 실수가 매우 잦다. 예: 실제 `…010…` 인데 `…0110…` 으로 1을 하나 더 읽는 식. 헷갈리는 구간은 머릿속으로 확대해서 자릿수를 하나하나 짚어가며 세라.
- **MATERIAL CODE 는 읽은 뒤 전체 자릿수를 세어 검산하라.** 'M' 다음에 숫자가 몇 개인지 정확히 센다(예: M + 10자리). 한 번 읽은 값과 다시 센 자릿수가 맞는지 확인하고, 다르면 그 구간을 다시 봐라.
- MATERIAL CODE 는 보통 'M' + 숫자(대시 포함 가능, 예: M3234-500039 또는 M3234500039). 흰색 Unitrontech 라벨의 'MATERIAL CODE :' 칸에 있다.
- 같은 사진에 서로 다른 라벨이 여러 개면 각각 별도 객체로.
- **라벨 장수를 반드시 세라 (`label_count`) — 사진 장수가 아니라 라벨 실물 장수다.** 같은 품목 라벨이 여러 장 반복되면 객체는 대표 1개만 만들되, `label_count` 에 그 사진에서 보이는 **그 품목 라벨의 실제 장수**를 넣어라. 서로 다른 라벨은 각각 객체를 만들고 각자의 장수를 센다.
  - 세는 단위: **Unitrontech 흰색 라벨 1장 = 1** (= 릴/박스 1개). 한 릴에 흰색 라벨과 제조사 라벨이 같이 붙어 있어도 그건 1로 센다.
  - 릴/박스가 여러 개 쌓여 있으면 **라벨이 일부만 보여도 식별되면 1로 센다.** 줄·단으로 규칙적으로 놓였으면 (가로 개수 × 세로 개수)로 한 번 검산해라.
  - 라벨이 아예 안 보이는(완전히 가려진) 릴은 세지 마라 — 추측 금지.
  - 사진에 라벨이 1장뿐이면 `label_count` 는 1.
- **두 스티커 LOT 비교 (정밀하게)**: 한 박스/릴엔 보통 라벨이 두 장 — Unitrontech 흰색 라벨과 제조사 라벨. Unitrontech 라벨의 'LOT :' 값을 `lot` 에 넣어라.
  그리고 **그 LOT와 똑같은 코드가 제조사 라벨에도 거의 항상 다시 인쇄돼 있다** (특히 바코드 바로 옆이나 아래의 작은 글씨로). `lot_maker` 에는 **Unitrontech LOT와 짝이 되는 바로 그 동일 코드**(예: `MN262220355`)를 작더라도 끝까지 찾아 넣어라.
  ⚠️ 혼동 절대 금지: Batch(예 `B026210010` 같은 B-시작 코드)·Ordering Code(부품번호)·Quantity(수량)·Date·Reel번호 는 LOT가 아니다 — 이런 걸 `lot_maker`에 넣지 마라.
  먼저 Unitrontech LOT를 읽은 뒤, 같은 문자열이 제조사 라벨 어딘가(작은 글씨·바코드 주변 포함)에 있는지 끝까지 살펴서 그 값을 넣어라. 정말로 어디에도 없을 때만 빈칸.
- **두 스티커 부품번호 비교 (매우 중요)**: Unitrontech 흰색 라벨의 'V/PN :' 값을 `vpn` 에 넣고, **제조사(Micron/Samsung 등) 라벨에 그 제조사가 직접 인쇄한 부품번호**(보통 제조사 라벨의 큰 글씨/바코드 아래, 예: `MTFC64GBCAVAL-AAT`)를 `part_maker` 에 따로 넣어라. 정상이라면 이 둘은 같아야 하지만, **다를 수도 있으니 각 스티커에 실제로 찍힌 값을 그대로 읽어라 — 절대 같다고 가정해서 한쪽 값을 양쪽에 복사하지 마라.** 제조사 라벨이 안 보이면 `part_maker` 는 빈칸.
- **두 스티커 SERIAL 비교 (혼입 검사 — 매우 중요)**: Unitrontech 흰색 라벨의 'SERIAL :' 값을 `serial` 에 넣어라.
  그리고 **제조사 라벨(맨 아래 바코드 쪽)에 찍힌 릴 일련번호**를 `serial_maker` 에 넣어라 — 보통 `2622-V25 1500` 처럼
  **숫자 4자리 + 대시 + 코드** 형태다. 그 숫자 4자리가 Unitrontech SERIAL 의 앞 4자리와 짝이 된다.
  ⚠️ 이 둘이 다르면 **서로 다른 릴의 라벨이 한 박스에 섞인 것**이다. 실제로 2622 와 2623 이 섞여 들어온 적이 있다.
  **각 스티커에 실제로 찍힌 값을 그대로 읽어라 — 같다고 가정해서 한쪽을 양쪽에 복사하지 마라.**
  앞 4자리는 특히 또박또박 봐라(2↔3 혼동 주의). 제조사 라벨이 안 보이면 `serial_maker` 는 빈칸.
- QUANTITY 는 숫자만(예: 2,000 → 2000).
- 안 보이거나 없는 항목은 빈 문자열. 절대 추측해서 지어내지 말고, 보이는 그대로만."""


def b64_image(path, max_side=1568):
    img = Image.open(path)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        img = img.resize((int(w * s), int(h * s)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode()


def thumb_data_uri(path, max_side=520):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        img = img.resize((int(w * s), int(h * s)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.standard_b64encode(buf.getvalue()).decode()


def extract_labels(client, path):
    data = b64_image(path)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)["labels"]


# ---------------------------------------------------------------- 대조
# 라벨필드 → (표시명, 마스터필드)
# (lkey, 표시명, 비교대상 ref, 마스터/타필드 key)
#   ref="master" → 엑셀 마스터와 비교, ref="cross" → 같은 사진의 다른 스티커와 비교
COMPARE_FIELDS = [
    ("vpn", "부품번호(V/PN ↔ Part#)", "master", "part"),
    ("vpn", "부품번호 (V/PN ↔ 제조사라벨)", "cross", "part_maker"),
    ("material_code", "MATERIAL CODE ↔ MOBIS ID", "master", "mobis"),
    ("lot", "LOT (제조사 ↔ Unitrontech)", "cross", "lot_maker"),
    ("serial", "SERIAL (제조사 ↔ Unitrontech)", "cross", "serial_maker"),
]
INFO_FIELDS = [
    ("fab", "FAB"), ("quantity", "QUANTITY(수량)"), ("maker_code", "MAKER CODE"),
    ("stock_day", "STOCK DAY"), ("serial", "SERIAL"),
    ("ordering_code", "Ordering Code"), ("msl_level", "MSL"),
]


def serial_head(v):
    """SERIAL 에서 릴을 가르는 앞 4자리 숫자만 뽑는다.

    두 스티커의 표기 형식이 다르다.
      Unitrontech : `262300 1167040006`  (앞 4자리 + 뒤에 로트가 이어짐)
      제조사       : `2622-V25 1500`      (앞 4자리 + 대시 + 코드)
    문자열을 통째로 비교하면 늘 불일치가 나고, 그러면 경고가 무의미해진다.
    실제로 섞였는지를 가르는 것은 **앞 4자리**다 — 2622 냐 2623 이냐.

    4자리를 못 뽑으면 빈 문자열을 돌려준다. 억지로 비교하지 않는다.
    """
    if not v:
        return ""
    m = re.match(r"\s*(\d{4})", str(v))
    return m.group(1) if m else ""


def mobis_is_real(lv, rec, by_mobis):
    """라벨에서 읽은 MOBIS ID 가 '마스터에 실재하는 ID' 인가?

    실재한다면 오독이 아니다. 마스터에 있는 값을 다른 값으로 '교정'하는 건 정의상 틀렸다.
    같은 부품에 ID 가 둘 이상 걸린 혼용 품목도 여기서 걸린다.
    (MTFC64GAZAQHD-AAT → M3203-001387 / -0013871 / -0013872. 끝 한 글자 차이라
     1글자 오독 보정 규칙에 그대로 걸려 조용히 ✅ 통과되고 있었다.)
    """
    if not lv:
        return False
    n = norm(lv)
    if n in by_mobis:
        return True
    return any(norm(m) == n for m in ((rec or {}).get("mobis_alts") or []))


def label_count_of(lab):
    """이 라벨 객체가 대표하는 '실물 라벨 장수'.

    사진 1장에 같은 라벨이 10장 찍혀 있으면 비전은 객체 1개 + label_count=10 으로 돌려준다.
    수량 집계는 객체 수(=사진 수에 가까움)가 아니라 이 값을 더해야 맞다.
    값이 없거나(구버전 응답) 이상하면 1로 본다 — 못 세었다고 0으로 지우면 수량이 사라진다.
    """
    try:
        n = int(str(lab.get("label_count", 1)).strip() or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, 999))


def match_label(lab, by_part, by_mobis, uncertain=None):
    """uncertain: 2차 교차판독에서 두 판독이 달랐던 라벨필드 key 집합 → 그 필드는 'verify'(확인필요)."""
    uncertain = uncertain or set()
    rec = by_part.get(norm(lab.get("vpn", "")))
    found_by = "Part#"
    if rec is None and lab.get("material_code"):
        rec = by_mobis.get(norm(lab.get("material_code", "")))
        found_by = "MATERIAL CODE" if rec else found_by
    registered = rec is not None

    rows = []
    matched = comparable = 0
    # 두 핵심키(V/PN↔Part#, MATERIAL CODE↔MOBIS ID)의 마스터 정확일치 여부를 미리 계산.
    # 한쪽이 정확히 맞으면 부품이 확실히 식별된 것 → 다른 키의 1글자 오독은 자동보정(오탐 방지).
    def _exact(lk, mk):
        lv = clean(lab.get(lk)); mv = clean(rec.get(mk)) if rec else ""
        return bool(lv) and bool(mv) and norm(lv) == norm(mv)
    vpn_exact = _exact("vpn", "part")
    mat_exact = _exact("material_code", "mobis")
    for lkey, disp, ref, mkey in COMPARE_FIELDS:
        lv = clean(lab.get(lkey))
        if ref == "master":
            mv = clean(rec.get(mkey)) if rec else ""
        else:  # cross: 같은 사진의 다른 스티커(제조사 라벨)
            mv = clean(lab.get(mkey))
        # 두 판독이 달랐던(불확실) 필드는 확인필요 — 단정하지 않음
        field_uncertain = (lkey in uncertain) or (ref == "cross" and mkey in uncertain)
        other_exact = mat_exact if lkey == "vpn" else (vpn_exact if lkey == "material_code" else False)
        shown = lv
        corrected = False
        note = ""
        if field_uncertain and lv:
            status = "verify"
        elif not lv and not mv:
            status = "na"
        elif not mv:
            if ref == "cross" and mkey in ("part_maker", "serial_maker") and lv:
                # 제조사 라벨(봉투 안)을 반사·포장 때문에 못 읽음. 조용히 통과(➖)시키면
                # '제조사 라벨까지 검증된 일치'로 착각하게 된다 → 사람이 직접 보라고 확인필요로 넘긴다.
                status = "verify"
                note = "제조사 라벨 부품번호를 읽지 못했습니다 (반사·포장) — 봉투 안 라벨을 직접 확인하세요"
            else:
                status = "label_only"      # ➖ 마스터에 없음
        elif not lv:
            status = "master_only"     # 라벨에서 못 읽음
        elif lkey == "serial" and ref == "cross":
            # 두 스티커의 표기 형식이 다르다 — Unitrontech `262300 1167040006`,
            # 제조사 `2622-V25 1500`. 통째로 비교하면 늘 불일치가 나므로 앞
            # 4자리로만 맞춘다. 섞였는지를 가르는 것은 그 4자리다.
            a, b = serial_head(lv), serial_head(mv)
            if not a or not b:
                status = "verify"; comparable += 1
                note = "SERIAL 앞 4자리를 읽지 못했습니다 — 두 스티커를 직접 대조해 주십시오"
            elif a == b:
                status = "match"; matched += 1; comparable += 1
            else:
                status = "mismatch"; comparable += 1
                note = (f"릴 번호가 다릅니다 — Unitrontech 라벨 {a}, 제조사 라벨 {b}. "
                        "서로 다른 릴의 라벨이 한 박스에 섞였는지 확인해 주십시오")
        elif norm(lv) == norm(mv):
            status = "match"; matched += 1; comparable += 1
        elif lkey == "material_code" and mobis_is_real(lv, rec, by_mobis):
            # 라벨의 MOBIS ID 가 마스터에 실재하는 ID다 (혼용 품목). 오독이 아니라
            # 진짜 다른 ID일 수 있으니 기계가 단정하면 안 된다 → 사람이 확인.
            status = "verify"; comparable += 1
            ids = (rec or {}).get("mobis_alts") or [mv]
            note = (("이 부품은 MOBIS ID 가 혼용됩니다 (" + " / ".join(ids)
                     + ") — 사진에서 어느 쪽인지 확인하세요") if len(ids) > 1 else
                    f"라벨의 '{lv}' 도 마스터에 등록된 MOBIS ID 입니다 — 자동보정하지 않습니다")
        elif lkey in ("vpn", "material_code") and other_exact and within_one(norm(lv), norm(mv)):
            # 다른 핵심키가 마스터와 정확일치 → 부품 식별됨. 이 키는 딱 1글자 오독 → 마스터값으로 고정(✅).
            # 예: MATERIAL CODE 정확일치인데 V/PN만 'VAMV0607E'(7 하나 빠짐) → 마스터 'VAMV06077E'로 보정.
            status = "match"; matched += 1; comparable += 1; shown = mv; corrected = True
        elif ref == "cross" and lv and mv and (norm(lv).startswith(norm(mv)) or norm(mv).startswith(norm(lv))):
            # LOT 교차대조: 제조사코드가 유니트론 LOT의 접두(유니트론이 -NNNN 리일번호를 뒤에 덧붙임) → 같은 LOT(✅).
            status = "match"; matched += 1; comparable += 1
        else:
            status = "mismatch"; comparable += 1
        rows.append(dict(field=disp, label=shown, master=mv, status=status,
                         corrected=corrected, note=note))

    pct = round(matched / comparable * 100) if comparable else 0
    has_verify = any(r["status"] == "verify" for r in rows)
    has_mismatch = any(r["status"] == "mismatch" for r in rows)
    # 혼용 ID 때문에 뜬 확인필요는 재판독해도 답이 안 나온다 (라벨은 제대로 읽혔고
    # 마스터가 두 갈래인 것). 재판독 대상에서 빼려고 따로 표시해 둔다.
    has_mixed = any(r["status"] == "verify" and r["note"] for r in rows)
    has_verify_other = any(r["status"] == "verify" and not r["note"] for r in rows)
    info = [dict(field=disp, label=clean(lab.get(k))) for k, disp in INFO_FIELDS]
    return dict(registered=registered, found_by=found_by if registered else "",
                pct=pct, matched=matched, comparable=comparable,
                has_verify=has_verify, has_mismatch=has_mismatch,
                has_mixed=has_mixed, has_verify_other=has_verify_other,
                rows=rows, info=info, vpn=clean(lab.get("vpn")),
                material_code=clean(lab.get("material_code")),
                label_count=label_count_of(lab))


# ---------------------------------------------------------------- 렌더링
def render(results, gen):
    cards = []
    for res in results:
        img = res["thumb"]; fname = res["fname"]
        for li, m in enumerate(res["labels"]):
            badge = ("🆕 마스터에 미등록" if not m["registered"]
                     else ("✅ 전체 일치" if m["pct"] == 100 else f"⚠️ {m['pct']}% 일치"))
            badge_cls = ("nf" if not m["registered"] else ("ok" if m["pct"] == 100 else "warn"))
            comp = "".join(
                f'<tr class="r-{r["status"]}"><td>{r["field"]}</td>'
                f'<td class="mono">{r["label"] or "—"}</td>'
                f'<td class="mono">{r["master"] or "—"}</td>'
                f'<td class="st">{ {"match":"✅","mismatch":"❌","verify":"⚠️","label_only":"➖","master_only":"❓","na":"·"}[r["status"]] }</td></tr>'
                for r in m["rows"])
            info = "".join(f'<span class="ic"><b>{i["field"]}</b> {i["label"] or "—"}</span>' for i in m["info"])
            cards.append(f"""
            <div class="card">
              <div class="cimg">{'<img src="'+img+'">' if li==0 else '<div class="noimg">동일 사진의 라벨 #'+str(li+1)+'</div>'}<div class="fn">{fname}</div></div>
              <div class="cbody">
                <div class="chead">
                  <div class="vpn mono">{m['vpn'] or '(부품번호 못 읽음)'}</div>
                  <div class="badge {badge_cls}">{badge}</div>
                </div>
                <div class="gauge"><div class="bar" style="width:{m['pct'] if m['registered'] else 0}%"></div><span>{m['pct']}% · {m['matched']}/{m['comparable']} 항목</span></div>
                <table class="cmp"><thead><tr><th>항목</th><th>라벨에서 읽음</th><th>엑셀 마스터</th><th></th></tr></thead><tbody>{comp}</tbody></table>
                <div class="info">{info}</div>
              </div>
            </div>""")

    total = sum(len(r["labels"]) for r in results)
    okc = sum(1 for r in results for m in r["labels"] if m["registered"] and m["pct"] == 100)
    warnc = sum(1 for r in results for m in r["labels"] if m["registered"] and m["pct"] < 100)
    nfc = sum(1 for r in results for m in r["labels"] if not m["registered"])
    return Template(_TPL).safe_substitute(
        GEN=gen, CARDS="".join(cards), TOTAL=total, OKC=okc, WARNC=warnc, NFC=nfc)


_TPL = r"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>부품 라벨 검수</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
:root{--red:#c43a3a;--green:#3f9d6b;--amber:#e0a93a;--ink:#1d1d20;--mut:#8a8a92;--line:#ececef;--bg:#f5f5f7;}
*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',system-ui,sans-serif;-webkit-font-smoothing:antialiased;}
.mono{font-family:ui-monospace,'SF Mono',Consolas,monospace;}
header{background:linear-gradient(135deg,#1d1d20,#2c2c33 55%,#3a2326);color:#fff;padding:28px 34px;}
header h1{margin:0;font-size:22px;font-weight:800;}
header .sub{margin-top:7px;font-size:12.5px;color:#b9b9c2;}
.wrap{max-width:1200px;margin:0 auto;padding:24px 34px 70px;}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-bottom:26px;}
.kpi{background:#fff;border-radius:13px;padding:15px 17px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 4px 18px rgba(0,0,0,.04);border-left:4px solid var(--c,#999);}
.kpi.t{--c:var(--ink);} .kpi.o{--c:var(--green);} .kpi.w{--c:var(--amber);} .kpi.n{--c:var(--red);}
.kpi .l{font-size:11px;color:var(--mut);font-weight:600;} .kpi .v{font-size:25px;font-weight:800;margin-top:5px;}
.card{display:grid;grid-template-columns:280px 1fr;gap:0;background:#fff;border-radius:15px;overflow:hidden;margin-bottom:20px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 4px 20px rgba(0,0,0,.05);}
@media(max-width:780px){.card{grid-template-columns:1fr;}}
.cimg{background:#11121a;position:relative;display:flex;align-items:center;justify-content:center;min-height:200px;}
.cimg img{width:100%;height:100%;object-fit:cover;display:block;}
.cimg .noimg{color:#9a9aa6;font-size:12px;padding:30px;text-align:center;}
.cimg .fn{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.55);color:#ddd;font-size:10px;padding:4px 8px;}
.cbody{padding:18px 20px;}
.chead{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;}
.vpn{font-size:16px;font-weight:800;}
.badge{font-size:12px;font-weight:700;padding:4px 11px;border-radius:8px;white-space:nowrap;}
.badge.ok{background:#e6f4ec;color:var(--green);} .badge.warn{background:#fcf3e0;color:#a9781d;} .badge.nf{background:#fbe9e9;color:var(--red);}
.gauge{position:relative;height:24px;background:#f0f0f2;border-radius:7px;margin:14px 0 16px;overflow:hidden;}
.gauge .bar{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,var(--green),#5ab884);border-radius:7px;}
.gauge span{position:absolute;left:10px;top:0;line-height:24px;font-size:11.5px;font-weight:700;color:#1d1d20;mix-blend-mode:luminosity;}
table.cmp{width:100%;border-collapse:collapse;font-size:12.5px;}
table.cmp th{text-align:left;color:var(--mut);font-weight:600;font-size:10.5px;text-transform:uppercase;padding:5px 8px;border-bottom:1px solid var(--line);}
table.cmp td{padding:7px 8px;border-bottom:1px solid var(--line);}
table.cmp td.st{text-align:center;width:34px;font-size:14px;}
tr.r-mismatch{background:#fdf0f0;} tr.r-mismatch td.mono{color:var(--red);font-weight:700;}
tr.r-match td:nth-child(2){color:var(--green);}
.info{margin-top:13px;display:flex;flex-wrap:wrap;gap:7px;}
.info .ic{font-size:11px;background:#f4f4f6;border-radius:6px;padding:3px 9px;color:#555;}
.info .ic b{color:var(--mut);font-weight:600;margin-right:3px;}
.foot{color:#aaa;font-size:11px;text-align:center;margin-top:8px;}
</style></head><body>
<header><h1>부품 라벨 검수 · 영업1·2실</h1>
<div class="sub">라벨 사진을 읽어 Apr inventory 마스터와 대조합니다. 매칭 %는 대조 가능 항목(부품번호·MATERIAL CODE·VENDER·FAB) 기준. 데이터를 있는 그대로 대조만 합니다.</div></header>
<div class="wrap">
  <div class="kpis">
    <div class="kpi t"><div class="l">검수한 라벨</div><div class="v">$TOTAL건</div></div>
    <div class="kpi o"><div class="l">✅ 전체 일치</div><div class="v">$OKC건</div></div>
    <div class="kpi w"><div class="l">⚠️ 불일치 있음</div><div class="v">$WARNC건</div></div>
    <div class="kpi n"><div class="l">🆕 마스터 미등록</div><div class="v">$NFC건</div></div>
  </div>
  $CARDS
  <div class="foot">자료: 사내 재고관리 엑셀 + 라벨 사진 · 작성 $GEN · 추출 모델 Claude Opus 4.8</div>
</div></body></html>"""


# ---------------------------------------------------------------- 서버
def find_free_port(pref):
    for p in (pref, pref + 1, pref + 2, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p)); port = s.getsockname()[1]; s.close(); return port
        except OSError:
            s.close()
    return pref


def serve(out, port, open_browser):
    directory = os.path.dirname(os.path.abspath(out)) or "."
    fname = os.path.basename(out)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    httpd = HTTPServer(("127.0.0.1", port), handler)
    url = f"http://localhost:{port}/{fname}"
    print(f"\n대시보드 → {url}\n종료: Ctrl+C")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료."); httpd.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="부품 라벨 검수")
    ap.add_argument("images", nargs="*", default=DEFAULT_IMAGES)
    ap.add_argument("--master", default=DEFAULT_MASTER)
    ap.add_argument("--port", type=int, default=8773)
    ap.add_argument("--out", default=os.path.join(HERE, "label_check.html"))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-serve", action="store_true", help="HTML만 만들고 서버 안 띄움")
    args = ap.parse_args()

    import anthropic
    client = anthropic.Anthropic(api_key=load_api_key())
    by_part, by_mobis = load_master(args.master)
    print(f"마스터 적재: Part# {len(by_part)}종, MOBIS ID {len(by_mobis)}종")

    results = []
    for path in args.images:
        if not os.path.exists(path):
            print(f"  건너뜀(없음): {path}"); continue
        print(f"  라벨 추출 중: {os.path.basename(path)} …", flush=True)
        labels = extract_labels(client, path)
        matched = [match_label(l, by_part, by_mobis) for l in labels]
        for m in matched:
            tag = "미등록" if not m["registered"] else f"{m['pct']}%"
            print(f"    - {m['vpn']}  →  {tag}")
        results.append(dict(fname=os.path.basename(path), thumb=thumb_data_uri(path), labels=matched))

    html = render(results, datetime.now().strftime("%Y-%m-%d %H:%M"))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 생성: {args.out} ({len(html):,} bytes)")

    if not args.no_serve:
        serve(args.out, find_free_port(args.port), open_browser=not args.no_open)
