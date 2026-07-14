#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unitrontech 흰색 라벨 생성기 (프로토타입).

입력 필드 → QR(제조사/유니트론텍 통합 문자열) + 텍스트 라벨 HTML.
QR 규격: MATERIAL/SERIAL/QTY/MAKER//VPN/MSL/LOT///// (사진 실물과 일치 검증됨)
SERIAL 규격: datecode(YYWW) + '00' + LOT  (예: 2612+00+BYWYHGH.41)
상수: MAKER=SJYV, STOCK DAY=오늘.
수량·MSL 은 LOT 파일에 열이 있으면 그 값을 쓰고, 없으면 화면에서 입력(MSL 기본 3).
"""
from __future__ import annotations
import io, os, re, base64, functools, segno

RED = "#c43a3a"
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")


@functools.lru_cache(maxsize=1)
def logo_datauri():
    """실제 Unitrontech 로고(빨간 심볼) PNG → data URI. 파일 없으면 빈 문자열."""
    try:
        with open(LOGO_PATH, "rb") as fp:
            return "data:image/png;base64," + base64.b64encode(fp.read()).decode()
    except OSError:
        return ""

def qr_string(material, serial, qty, maker, vpn, msl, lot):
    return f"{material}/{serial}/{qty}/{maker}//{vpn}/{msl}/{lot}/////"

def material_code(mobis_id):
    """MOBIS ID → MATERIAL CODE : 대시 제거 (M3203-0013671 → M32030013671)."""
    return re.sub(r"[^0-9A-Za-z]", "", str(mobis_id or ""))

def serial_from(datecode, lot):
    """datecode(YYYYWW 또는 YYWW) → YYWW + '00' + LOT."""
    d = re.sub(r"\D", "", str(datecode or ""))
    yyww = d[-4:] if len(d) >= 4 else d
    return f"{yyww}00{lot}"

def qr_png_datauri(data, scale=10, border=4):
    """고해상도 PNG(data URI). 작은 라벨에서도 모듈 경계가 뭉개지지 않게 SVG 대신 PNG."""
    import base64
    q = segno.make(data, error="m")
    buf = io.BytesIO()
    q.save(buf, kind="png", scale=scale, border=border)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def fmt_qty(q):
    try:
        return f"{int(str(q).replace(',','')):,}"
    except Exception:
        return str(q or "")

def render_label(f):
    """f: dict(material, serial, qty, maker, vpn, msl, lot, stock_day). → 라벨 1장 HTML."""
    data = qr_string(f["material"], f["serial"], f["qty"], f["maker"], f["vpn"], f["msl"], f["lot"])
    qr = qr_png_datauri(data)
    top_rows = [                                   # 좌측 정렬
        ("MATERIAL CODE", f["material"]),
        ("SERIAL", f["serial"]),
        ("QUANTITY", fmt_qty(f["qty"])),
        ("MAKER CODE", f["maker"]),
        ("V/PN", f["vpn"]),
    ]
    bot_rows = [                                   # 실물처럼 우측 들여쓰기
        ("STOCK NUMBER", ""),
        ("STOCK DAY", f.get("stock_day", "")),
        ("LOT", f["lot"]),
    ]

    def rows_html(rows):
        return "".join(
            f'<div class="fr"><span class="fk">{k}</span><span class="fc"> : </span>'
            f'<span class="fv">{v}</span></div>' for k, v in rows)

    logo = logo_datauri()
    logo_html = (f'<img class="logo" src="{logo}" alt="Unitrontech">' if logo
                 else '<div class="logotxt">Unitrontech</div>')
    return f'''<div class="label">
  <div class="qcol">
    <div class="qr"><img src="{qr}" alt="QR"></div>
    <div class="qbot">
      <div class="msl">MSL LEVEL : {f["msl"]}</div>
      <div class="loc">Location :</div>
    </div>
  </div>
  <div class="tcol">
    <div class="trows">{rows_html(top_rows)}</div>
    <div class="trows indent">{rows_html(bot_rows)}</div>
  </div>
  <div class="mslbox">
    <div class="mslhd">MSL</div>
    <div class="mslmid">
      <svg class="diag" viewBox="0 0 100 100" preserveAspectRatio="none">
        <line x1="0" y1="0" x2="100" y2="100" stroke="#111" stroke-width="1.4" vector-effect="non-scaling-stroke"/>
      </svg>
      <span class="msln">{f["msl"]}</span>
    </div>
    <div class="mslft">개봉일자</div>
  </div>
  {logo_html}
</div>'''

PAGE_TPL = '''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>Unitrontech 라벨</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
:root{{--red:#c43a3a;}}
*{{box-sizing:border-box;}}
body{{margin:0;background:#e9e9ee;font-family:'Pretendard',system-ui,sans-serif;padding:24px;}}
.sheet{{display:flex;flex-wrap:wrap;gap:14px;}}
/* 기본 라벨 크기 — 실측 후 조정 (mm) */
.label{{width:100mm;height:36mm;background:#fff;border:1px solid #cfcfd6;border-radius:2mm;
  position:relative;display:grid;grid-template-columns:19mm 1fr 15mm;padding:2mm 2.5mm;gap:1.5mm;align-items:start;
  box-shadow:0 1px 3px rgba(0,0,0,.12);}}
.qcol{{display:flex;flex-direction:column;align-items:flex-start;justify-content:space-between;height:31mm;}}
.qr{{width:16mm;height:16mm;}} .qr img{{width:100%;height:100%;display:block;image-rendering:pixelated;}}
.qbot .msl{{font-size:2mm;font-weight:600;}} .qbot .loc{{font-size:2mm;color:#222;margin-top:.4mm;}}
.tcol{{display:flex;flex-direction:column;justify-content:flex-start;overflow:hidden;padding-top:.5mm;}}
.trows.indent{{padding-left:7mm;}}   /* STOCK NUMBER부터 우측 들여쓰기 (실물과 동일) */
.fr{{font-size:2.2mm;line-height:3mm;font-family:ui-monospace,Consolas,monospace;white-space:nowrap;}}
.fk{{font-weight:600;}} .fc{{font-weight:600;}} .fv{{font-weight:600;}}
/* MSL 박스: 상단 MSL(헤더) / 중단 대각선(좌상→우하)+숫자(정사각) / 하단 개봉일자 */
.mslbox{{width:15mm;}}
.mslbox .mslhd{{font-size:2.1mm;font-weight:700;padding:0 .6mm .3mm;border-bottom:0.3mm solid #111;}}
.mslbox .mslmid{{position:relative;height:12mm;
  border-left:0.3mm solid #111;border-right:0.3mm solid #111;border-bottom:0.3mm solid #111;}}
.mslbox .mslmid .diag{{position:absolute;inset:0;width:100%;height:100%;display:block;}}
.mslbox .msln{{position:absolute;top:.4mm;right:1.2mm;font-size:2.8mm;font-weight:800;}}
.mslbox .mslft{{font-size:2mm;text-align:center;padding:.3mm 0;
  border-left:0.3mm solid #111;border-right:0.3mm solid #111;border-bottom:0.3mm solid #111;}}
/* 로고: 맨 아래 (실물과 동일) */
.logo{{position:absolute;width:23mm;height:auto;right:7mm;bottom:2mm;}}
.logotxt{{position:absolute;right:7mm;bottom:2mm;color:var(--red);font-weight:800;font-size:3mm;letter-spacing:-.15mm;white-space:nowrap;}}
@media print{{body{{background:#fff;padding:0;}} .label{{box-shadow:none;}}}}
</style></head><body><div class="sheet">{cards}</div></body></html>'''

def render_page(labels):
    return PAGE_TPL.format(cards="".join(render_label(f) for f in labels))


if __name__ == "__main__":
    import datetime, os
    # 사진의 두 박스 재현 (정답 대조용)
    samples = [
        dict(material="M32030013671", serial="261200BYWYHGH.41", qty="1520",
             maker="SJYV", vpn="MTFC64GAZAOTD-AAT", msl="3", lot="BYWYHGH.41",
             stock_day="20260707"),
        dict(material="M32030013671", serial="261400BYT8MGH.41", qty="1520",
             maker="SJYV", vpn="MTFC64GAZAOTD-AAT", msl="3", lot="BYT8MGH.41",
             stock_day="20260707"),
    ]
    html = render_page(samples)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_labels.html")
    with open(out, "w", encoding="utf-8") as fp:
        fp.write(html)
    print("wrote", out, len(html), "bytes")
    # QR 문자열 정답 대조
    for s in samples:
        print(qr_string(s["material"], s["serial"], s["qty"], s["maker"], s["vpn"], s["msl"], s["lot"]))
