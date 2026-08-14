#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""부품 라벨 검수 웹앱.

브라우저에서 사진을 업로드(개수 무제한)하면 → 한 장씩 Claude Opus 4.8 비전으로
라벨 필드 추출 → Apr inventory 마스터와 대조 → 매칭 % 즉시 표시.

표준 라이브러리만 사용. 사진은 브라우저에서 base64로 전송한다.
"""
from __future__ import annotations

import base64
import io
import json
import os
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dataurl
from PIL import Image

from label_inspector import (
    load_api_key, load_master, match_label,
    EXTRACT_SCHEMA, PROMPT, DEFAULT_MASTER,
)

HERE = os.path.dirname(os.path.abspath(__file__))

# Sonnet 5 단일패스(2576/high)로 한 번만 판독 — Opus 재판독 없음(비용·시간 절감).
# 실측(480장): API ~24분·~$11. 애매한 건(미등록·불일치·1글자)은 화면 표시 + '확인필요 ZIP'으로
# 따로 모아 사람이 육안 확인(그 과정은 Anthropic API 안 씀 = 무료).
MODEL = "claude-sonnet-5"

# ---- 전역 상태 (서버 시작 시 1회) ----
import anthropic
def _api_key():
    try:
        return load_api_key()
    except SystemExit:
        return os.environ.get("ANTHROPIC_API_KEY", "")
CLIENT = anthropic.Anthropic(api_key=_api_key() or "MISSING", max_retries=5)  # 429/5xx/529 자동 백오프

DATA_DIR = os.environ.get("DATA_DIR", HERE)
MASTER_PATH = os.path.join(DATA_DIR, "master.xlsx")   # 업로드한 마스터(재고) 저장 위치

BY_PART, BY_MOBIS = {}, {}
def reload_master():
    """마스터 적재 — 업로드본 > MASTER_XLSX 환경변수 > 로컬 기본 순. 없으면 빈 상태(UI에서 업로드)."""
    global BY_PART, BY_MOBIS
    for p in (MASTER_PATH, os.environ.get("MASTER_XLSX"), DEFAULT_MASTER):
        if p and os.path.exists(p):
            try:
                BY_PART, BY_MOBIS = load_master(p)
                print(f"마스터 적재: Part# {len(BY_PART)}종, MOBIS ID {len(BY_MOBIS)}종  ({p})")
                return len(BY_PART)
            except Exception as e:  # noqa: BLE001
                print(f"마스터 적재 실패({type(e).__name__}: {e}) — 다음 후보 시도")
    BY_PART, BY_MOBIS = {}, {}
    print("마스터 없음 — UI에서 재고 파일을 업로드하세요.")
    return 0
reload_master()


def downscale_b64(raw_b64, max_side=1568, quality=90):  # C안: 2576→1568px (입력 이미지 토큰 ~60%↓)
    img = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        img = img.resize((int(w * s), int(h * s)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode()


def thumb_b64(raw_b64, max_side=520, quality=80):
    img = Image.open(io.BytesIO(base64.b64decode(raw_b64))).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        img = img.resize((int(w * s), int(h * s)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.standard_b64encode(buf.getvalue()).decode()


def extract_labels(b64data, model=MODEL, effort="medium"):
    resp = CLIENT.messages.create(
        model=model, max_tokens=8192,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64data}},
            {"type": "text", "text": PROMPT},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}, "effort": effort},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)["labels"]


from label_inspector import norm as _norm
from label_inspector import label_count_of as _label_count

CONSENSUS_FIELDS = ["vpn", "material_code", "vender", "lot", "lot_maker", "part_maker", "serial"]


def _key(l):
    return _norm(l.get("vpn", "")) or _norm(l.get("material_code", ""))


def _vote_key(l):
    """다수결로 묶을 때 쓰는 키. 부품번호 **+ SERIAL** 이다.

    부품번호만으로 묶으면, 한 사진에 같은 부품의 릴이 두 개 있고 SERIAL 이
    2622·2623 으로 다를 때 **둘이 한 라벨로 합쳐지고 SERIAL 은 다수결로 하나만
    남는다.** 섞이면 안 되는 물건이 섞였다는 사실이 조용히 사라지는 것이다.
    실제로 그렇게 통과된 건이 있었다.

    다수결은 같은 라벨을 여러 번 읽은 값들을 안정화하려는 장치이지, 서로 다른
    개체를 하나로 만들라는 장치가 아니다. SERIAL 은 개체를 가르는 값이므로
    키에 넣는다.
    """
    return (_key(l), _norm(l.get("serial", "")))


def reconcile(read1, read2):
    """두 판독 결과를 대조 → [(label, uncertain_fields_set), ...].
    두 판독이 같은 라벨에서 같은 값을 냈으면 확실, 다르거나 한쪽에만 잡혔으면 그 필드는 불확실."""
    d2 = {}
    for l in read2:
        d2.setdefault(_key(l), l)
    keys1 = {_key(l) for l in read1}
    merged = []
    for l1 in read1:
        l2 = d2.get(_key(l1))
        if l2 is None:
            merged.append((l1, set(CONSENSUS_FIELDS)))     # 한쪽에만 잡힘
            continue
        unc = {f for f in CONSENSUS_FIELDS if _norm(l1.get(f, "")) != _norm(l2.get(f, ""))}
        merged.append((l1, unc))
    for l2 in read2:                                        # 2차에만 잡힌 라벨
        if _key(l2) not in keys1:
            merged.append((l2, set(CONSENSUS_FIELDS)))
    return merged


def _vote_field(values):
    """여러 판독값 중 최빈값(정규화 기준) — 비어있지 않은 것만."""
    from collections import Counter
    vals = [v for v in values if _norm(v)]
    if not vals:
        return ""
    counts = Counter(_norm(v) for v in vals).most_common()
    best, top = counts[0]
    # 동점이면 아무것도 고르지 않는다. 2:2 로 갈린 값을 조용히 하나 골라 두면,
    # 판독이 갈렸다는 사실이 화면에서 사라지고 확실한 값처럼 보인다.
    if len(counts) > 1 and counts[1][1] == top:
        return ""
    for v in vals:
        if _norm(v) == best:
            return v
    return vals[0]


def _vote_labels(reads):
    """여러 번 읽은 라벨 리스트들을 묶어 필드별 다수결 → [(라벨, 불확실필드), ...].

    묶는 키에 SERIAL 이 들어간다(`_vote_key`). 서로 다른 개체는 합치지 않는다.

    **판독이 갈린 필드는 함께 돌려준다.** 예전에는 다수결로 하나를 고르고 끝이라,
    2:1 로 갈린 값이 화면에서는 확실한 값과 똑같이 보였다. 갈렸다는 사실이야말로
    사람이 봐야 한다는 신호다.
    """
    groups, order = {}, []
    for r in reads:
        for l in r:
            k = _vote_key(l)
            if k not in groups:
                groups[k] = []; order.append(k)
            groups[k].append(l)
    out = []
    for k in order:
        ls = groups[k]
        fields = set().union(*[set(l.keys()) for l in ls])
        voted, unsure = {}, set()
        for f in fields:
            vals = [l.get(f, "") for l in ls]
            voted[f] = _vote_field(vals)
            # 비어 있지 않은 값이 두 종 이상 나왔으면 판독이 갈린 것이다.
            seen = {_norm(v) for v in vals if _norm(v)}
            if len(seen) > 1:
                unsure.add(f)
        out.append((voted, unsure))
    return out


def _needs_recheck(matched):
    """미등록·불일치·1글자차이(verify)면 고해상+Opus로 정밀 재판독해 교정.

    단 'MOBIS ID 혼용'으로 뜬 확인필요는 제외한다. 라벨은 제대로 읽힌 것이고 마스터에
    유효한 ID가 둘 이상 있는 상황이라, 다시 읽어도 같은 값이 나온다 — 비용만 든다.
    """
    return any((not m["registered"]) or m["has_mismatch"]
               or m.get("has_verify_other", m["has_verify"]) for m in matched)


def inspect_one(raw_b64):
    """Sonnet 5 단일패스(2576/high)로 한 번만 판독. Opus 재판독 없음(비용·시간 절감).
    애매한 건(미등록·불일치·1글자)은 결과에 표시만 하고, 사람이 '확인필요 ZIP'으로 따로 모아 육안 확인."""
    big = downscale_b64(raw_b64, max_side=2576, quality=92)
    labels = extract_labels(big, effort="high")   # MODEL(Sonnet 5) / 2576 / high 단일패스
    matched = [match_label(lab, BY_PART, BY_MOBIS) for lab in labels]
    return dict(thumb=thumb_b64(raw_b64), labels=matched)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/master":                      # 마스터 적재 상태
            self._send(200, json.dumps({"parts": len(BY_PART), "mobis": len(BY_MOBIS)}))
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            if self.path == "/inspect":
                raw_b64 = dataurl.to_b64(payload["image"])   # data URL 접두 제거
                result = inspect_one(raw_b64)
            elif self.path == "/master":          # 마스터(재고) 파일 업로드 → 저장 후 재적재
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(MASTER_PATH, "wb") as f:
                    f.write(dataurl.to_bytes(payload["file"]))
                reload_master()
                if not BY_PART:
                    raise ValueError("마스터를 읽었지만 부품이 0건입니다. 'Apr inventory' 시트가 있는 파일인지 확인하세요.")
                result = {"ok": True, "parts": len(BY_PART), "mobis": len(BY_MOBIS)}
            else:
                self._send(404, json.dumps({"error": "not found"})); return
            self._send(200, json.dumps(result, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))


def find_free_port(pref):
    for p in (pref, pref + 1, pref + 2, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p)); port = s.getsockname()[1]; s.close(); return port
        except OSError:
            s.close()
    return pref


PAGE = r"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>부품 라벨 검수</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
:root{--red:#c43a3a;--green:#3f9d6b;--amber:#e0a93a;--blue:#3a6ea5;--ink:#1d1d20;--mut:#8a8a92;--line:#ececef;--bg:#f5f5f7;}
*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',system-ui,sans-serif;-webkit-font-smoothing:antialiased;}
.mono{font-family:ui-monospace,'SF Mono',Consolas,monospace;}
header{background:linear-gradient(135deg,#1d1d20,#2c2c33 55%,#3a2326);color:#fff;padding:26px 34px;}
header h1{margin:0;font-size:21px;font-weight:800;}
header .sub{margin-top:7px;font-size:12.5px;color:#b9b9c2;max-width:820px;line-height:1.6;}
.wrap{max-width:1180px;margin:0 auto;padding:24px 30px 70px;}
.drop{border:2.5px dashed #cfcfd6;border-radius:16px;background:#fff;padding:34px;text-align:center;cursor:pointer;transition:.15s;}
.drop.hot{border-color:var(--red);background:#fdf4f4;}
.drop .big{font-size:16px;font-weight:700;}
.drop .hint{font-size:12.5px;color:var(--mut);margin-top:6px;}
.drop .btn{display:inline-block;margin-top:14px;background:var(--ink);color:#fff;font-weight:700;font-size:13px;padding:10px 20px;border-radius:10px;}
.bar2{display:flex;gap:10px;align-items:center;margin:18px 0 6px;flex-wrap:wrap;}
.bar2 .overall{font-size:15px;font-weight:800;color:#fff;background:var(--ink);padding:7px 16px;border-radius:10px;letter-spacing:-.3px;}
.bar2 .overall.full{background:var(--green);} .bar2 .overall.part{background:var(--amber);} .bar2 .overall.low{background:var(--red);}
.bar2 .stat{font-size:12.5px;color:var(--mut);}
.bar2 .chip{font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:7px;}
.chip.ok{background:#e6f4ec;color:var(--green);} .chip.mis{background:#fbe9e9;color:var(--red);} .chip.warn{background:#fcf3e0;color:#a9781d;} .chip.nf{background:#f0e6f5;color:#9a5ba6;}
.fsel{display:inline-flex;background:#eaeaef;border-radius:9px;padding:3px;gap:3px;margin-left:6px;}
.fsel .fb{border:none;background:transparent;font-family:inherit;font-size:12px;font-weight:700;color:#777;padding:6px 13px;border-radius:7px;cursor:pointer;}
.fsel .fb.on{background:#fff;color:var(--ink);box-shadow:0 1px 4px rgba(0,0,0,.13);}
.clearb{margin-left:auto;font-size:12px;color:var(--mut);background:none;border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;}
.card{display:grid;grid-template-columns:280px 1fr;background:#fff;border-radius:15px;overflow:hidden;margin-bottom:18px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 4px 20px rgba(0,0,0,.05);}
@media(max-width:780px){.card{grid-template-columns:1fr;}}
.cimg{background:#11121a;position:relative;display:flex;align-items:center;justify-content:center;min-height:190px;}
.cimg img{width:100%;height:100%;object-fit:cover;display:block;cursor:zoom-in;}
.lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:300;padding:24px;overflow:hidden;}
.lb.show{display:flex;}
/* 확대는 transform:scale 이 아니라 요소 크기(width/height)로 한다.
   scale 은 화면 크기로 한 번 래스터화한 비트맵을 늘려서 원본 픽셀이 있어도 뭉갠다.
   크기를 키우면 브라우저가 원본에서 다시 그린다 → 원본 해상도까지 선명하다.
   transform 은 이동(translate)에만 쓴다. */
.lb img{max-width:none;max-height:none;border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.5);cursor:zoom-in;user-select:none;-webkit-user-drag:none;flex:none;}
.lb .hint{position:fixed;top:16px;left:0;right:0;text-align:center;color:#bbb;font-size:12px;}
.cimg .fn{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.55);color:#ddd;font-size:10px;padding:4px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.cbody{padding:16px 20px;}
.sub-lab{font-size:11px;color:var(--mut);margin:14px 0 8px;border-top:1px dashed var(--line);padding-top:10px;}
.chead{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;}
.vpn{font-size:16px;font-weight:800;}
.badge{font-size:12px;font-weight:700;padding:4px 11px;border-radius:8px;white-space:nowrap;}
.badge.ok{background:#e6f4ec;color:var(--green);} .badge.warn,.badge.ver{background:#fcf3e0;color:#a9781d;} .badge.mis{background:#fbe9e9;color:var(--red);} .badge.nf{background:#f0e6f5;color:#9a5ba6;}
.corr{font-size:9px;font-weight:800;color:#a9781d;background:#fcf3e0;border-radius:5px;padding:1px 5px;margin-left:4px;letter-spacing:.3px;}
/* 라벨 장수(사진 장수 아님). 비전이 센 값을 사람이 고칠 수 있게 입력칸으로 둔다. */
.lcnt{display:flex;align-items:center;gap:5px;font-size:11.5px;font-weight:700;color:var(--mut);background:#f4f4f6;border-radius:8px;padding:3px 9px;}
.lcnt input{width:52px;font:inherit;font-size:14px;font-weight:800;color:var(--ink);text-align:right;border:1px solid var(--line);border-radius:6px;padding:2px 5px;background:#fff;}
.lcnt input:focus{outline:2px solid var(--ink);outline-offset:-1px;}
.lcnt.multi{background:#eef3fb;color:#2c5aa0;}
.gauge{position:relative;height:24px;background:#f0f0f2;border-radius:7px;margin:12px 0 14px;overflow:hidden;}
.gauge .bar{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,var(--green),#5ab884);border-radius:7px;transition:width .5s;}
.gauge.lo .bar{background:linear-gradient(90deg,var(--amber),#eebb55);}
.gauge span{position:absolute;left:10px;top:0;line-height:24px;font-size:11.5px;font-weight:700;}
table.cmp{width:100%;border-collapse:collapse;font-size:12.5px;}
table.cmp th{text-align:left;color:var(--mut);font-weight:600;font-size:10.5px;text-transform:uppercase;padding:5px 8px;border-bottom:1px solid var(--line);}
table.cmp td{padding:7px 8px;border-bottom:1px solid var(--line);}
table.cmp td.st{text-align:center;width:34px;font-size:14px;}
tr.r-mismatch{background:#fdf0f0;} tr.r-mismatch td.mono{color:var(--red);font-weight:700;}
tr.r-verify{background:#fdf8ec;} tr.r-verify td.mono{color:#a9781d;font-weight:700;}
tr.r-match td:nth-child(2){color:var(--green);}
tr.r-note{background:#fdf8ec;} tr.r-note td.note{padding-top:0;font-size:12px;color:#8a6d1f;line-height:1.5;border-bottom:1px solid var(--line);}
.lotbox{margin-top:14px;border:1px solid var(--line);border-radius:11px;padding:13px 15px;background:#fafafb;}
.lotbox .lh{font-size:11px;color:var(--mut);font-weight:700;text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px;display:flex;align-items:center;gap:9px;}
.lotbox .lh .v{font-size:12px;font-weight:700;padding:3px 10px;border-radius:7px;}
.lotbox .lh .v.ok{background:#e6f4ec;color:var(--green);} .lotbox .lh .v.bad{background:#fbe9e9;color:var(--red);} .lotbox .lh .v.ver{background:#fcf3e0;color:#a9781d;} .lotbox .lh .v.na{background:#eee;color:#888;}
.lotrow{display:flex;align-items:center;gap:12px;margin:7px 0;}
.lotrow .who{width:120px;flex:none;font-size:11.5px;color:#666;font-weight:600;}
.lotrow .val{font-family:ui-monospace,Consolas,monospace;font-size:19px;font-weight:700;letter-spacing:1px;word-break:break-all;}
.lotrow .val .c{display:inline-block;padding:0 1px;}
.lotrow .val .c.bad{color:#fff;background:var(--red);border-radius:3px;}
.lotrow .val .none{color:#bbb;font-size:13px;font-weight:500;font-family:'Pretendard';}
.info{margin-top:12px;display:flex;flex-wrap:wrap;gap:7px;}
.info .ic{font-size:11px;background:#f4f4f6;border-radius:6px;padding:3px 9px;color:#555;}
.info .ic b{color:var(--mut);font-weight:600;margin-right:3px;}
.spin{display:inline-block;width:15px;height:15px;border:2.5px solid #ddd;border-top-color:var(--red);border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;}
@keyframes sp{to{transform:rotate(360deg);}}
.pending{padding:16px 20px;color:var(--mut);font-size:13px;}
.err{padding:16px 20px 4px;color:var(--red);font-size:13px;}
.retry{margin:6px 20px 16px;background:var(--ink);color:#fff;border:none;font-family:inherit;font-size:12.5px;font-weight:700;padding:9px 17px;border-radius:9px;cursor:pointer;}
.retry:hover{background:#000;}
#masterbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px;padding:11px 16px;border-radius:12px;font-size:13px;font-weight:600;}
#masterbar.ok{background:#e9f4ee;color:#2c6b48;} #masterbar.warn{background:#fcf3e0;color:#9a6b15;}
#masterbar #mstat{flex:1;min-width:200px;}
#masterbar .mbtn{background:#fff;border:1.5px solid rgba(0,0,0,.12);font-family:inherit;font-size:12.5px;font-weight:700;padding:7px 13px;border-radius:9px;cursor:pointer;}
#masterbar .mbtn:hover{border-color:rgba(0,0,0,.25);}
/* 확인 모드(게임화) */
.reviewbtn{background:var(--amber);color:#3a2c08;border:none;font-family:inherit;font-size:13px;font-weight:800;padding:9px 16px;border-radius:10px;cursor:pointer;}
.reviewbtn:hover{filter:brightness(.96);} .reviewbtn[disabled]{opacity:.4;cursor:default;}
#rev{position:fixed;inset:0;z-index:200;background:rgba(20,20,26,.72);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;padding:24px;}
#rev .panel{background:#fff;border-radius:20px;max-width:780px;width:100%;max-height:92vh;overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.4);}
#rev .rtop{padding:16px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;}
#rev .rtop .prog{flex:1;height:12px;background:#eee;border-radius:7px;overflow:hidden;}
#rev .rtop .prog .b{height:100%;background:linear-gradient(90deg,var(--green),#5ab884);transition:width .4s;}
#rev .rtop .cnt{font-size:13px;font-weight:800;color:var(--ink);white-space:nowrap;}
#rev .rtop .x{background:none;border:none;font-size:20px;color:#aaa;cursor:pointer;line-height:1;}
#rev .rimg{width:100%;max-height:46vh;object-fit:contain;background:#111;display:block;cursor:zoom-in;}
#rev .rbody{padding:18px 22px;}
#rev .rvpn{font-size:17px;font-weight:800;margin-bottom:4px;}
#rev .rwhy{font-size:12.5px;color:#a9781d;font-weight:700;margin-bottom:12px;}
#rev table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:6px;}
#rev th{text-align:left;color:var(--mut);font-size:10.5px;text-transform:uppercase;padding:5px 8px;border-bottom:1px solid var(--line);}
#rev td{padding:8px;border-bottom:1px solid var(--line);font-family:ui-monospace,Consolas,monospace;}
#rev tr.bad td{color:var(--red);font-weight:700;background:#fdf0f0;} #rev tr.ver td{color:#a9781d;font-weight:700;background:#fdf8ec;}
#rev .ract{padding:16px 22px 22px;display:flex;gap:10px;align-items:center;}
#rev .ok{flex:1;background:var(--green);color:#fff;border:none;font-family:inherit;font-size:15px;font-weight:800;padding:14px;border-radius:12px;cursor:pointer;}
#rev .ok:hover{background:#338a59;}
#rev .bad{background:#fbe9e9;color:var(--red);border:1.5px solid #f0c9c9;font-family:inherit;font-size:13px;font-weight:700;padding:14px 16px;border-radius:12px;cursor:pointer;}
#rev .prev{background:#eee;border:none;font-family:inherit;font-size:13px;font-weight:700;padding:14px 16px;border-radius:12px;cursor:pointer;color:#555;}
#rev .done{text-align:center;padding:50px 30px;}
#rev .done .em{font-size:60px;} #rev .done h2{margin:14px 0 6px;font-size:24px;} #rev .done p{color:var(--mut);font-size:14px;}
.card.confirmed{outline:2px solid var(--green);outline-offset:-2px;}
.card .cfmtag{display:inline-block;font-size:10px;font-weight:800;color:#fff;background:var(--green);border-radius:6px;padding:2px 8px;margin-left:8px;}
#rev .rimg{max-height:54vh;}
#rev tr.okrow td{color:var(--green);}
#rev .rbadge{font-size:11px;font-weight:700;color:var(--mut);margin-left:6px;}
.revall{font-size:12px;font-weight:700;color:var(--mut);display:inline-flex;align-items:center;gap:5px;cursor:pointer;user-select:none;}
.revall input{cursor:pointer;margin:0;}
.cdiff{background:var(--red);color:#fff;border-radius:3px;padding:0 2px;font-weight:800;}
/* MOBIS ID별 수량 검토 모달 */
#summary{position:fixed;inset:0;z-index:210;background:rgba(20,20,26,.72);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;padding:24px;}
#summary .spanel{background:#fff;border-radius:18px;max-width:640px;width:100%;max-height:90vh;overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.4);}
#summary .stop{position:sticky;top:0;background:#fff;padding:15px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;font-size:14.5px;}
#summary .sx{background:none;border:none;font-size:20px;color:#aaa;cursor:pointer;line-height:1;}
table.sum{width:100%;border-collapse:collapse;font-size:13px;}
table.sum th{text-align:left;color:var(--mut);font-size:10.5px;text-transform:uppercase;padding:7px 10px;border-bottom:2px solid var(--line);}
table.sum td{padding:8px 10px;border-bottom:1px solid var(--line);}
table.sum tr.tot td{border-top:2px solid var(--ink);font-weight:800;background:#fafafb;}
.mixtag{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:4px;
  background:#fff3d6;color:#8a5a00;font-size:11px;font-weight:700;vertical-align:middle}
.covbox{margin:0 0 12px;border:1px solid #cfd6e0;background:#f7f9fc;border-radius:8px;
  padding:9px 12px;font-size:12.5px;line-height:1.6;color:#3a4453}
.mixbox{margin:0 0 14px;border:1px solid #f0c36d;background:#fff9ec;border-radius:8px;padding:10px 12px}
.mixtitle{font-weight:700;font-size:13px;color:#8a5a00;margin-bottom:6px}
.mixlist{margin:0;padding-left:18px}
.mixlist li{font-size:13px;color:#4a3a10;margin:4px 0;line-height:1.5}
</style></head><body>
<header><h1>부품 라벨 검수 · 모비스향</h1></header>
<div class="wrap">
  <div id="masterbar" class="warn"><span id="mstat">마스터 확인 중…</span>
    <button class="mbtn" id="mbtn">📋 재고 마스터 업로드</button>
    <input id="mfile" type="file" accept=".xlsx" style="display:none">
  </div>
  <div class="drop" id="drop">
    <div class="big">📷 라벨 사진 · 폴더를 여기로 끌어다 놓거나 클릭해서 선택</div>
    <div class="hint">폴더 통째로 드래그 가능 · 여러 장 한꺼번에 · 애매한 사진은 아래 '📁 확인필요 ZIP'으로 따로 받기</div>
    <div class="btn">사진 선택</div>
    <button class="btn" id="folderbtn" type="button" style="border:none;font-family:inherit;cursor:pointer;margin-left:8px">📁 폴더 선택</button>
    <input id="file" type="file" accept="image/*" multiple style="display:none">
    <input id="folder" type="file" webkitdirectory style="display:none">
  </div>
  <div class="bar2" id="bar" style="display:none">
    <span class="overall" id="overall">전체 일치율 —</span>
    <span class="stat" id="stat"></span>
    <span class="chip ok" id="c-ok">✅ 0</span>
    <span class="chip mis" id="c-mis">❌ 0</span>
    <span class="chip warn" id="c-ver">⚠️ 0</span>
    <span class="chip nf" id="c-nf">🆕 0</span>
    <span class="fsel"><button class="fb on" id="f-all">전체</button><button class="fb" id="f-bad">확인필요</button></span>
    <button class="reviewbtn" id="reviewbtn" disabled>🔍 확인 모드</button>
    <label class="revall" title="확인 모드에서 ✅ 전체일치 건도 육안 검산 대상에 포함"><input type="checkbox" id="revall-cb"> ✅ 정상도 확인</label>
    <button class="clearb" id="sumbtn">📊 수량 검토</button>
    <button class="clearb" id="zipbtn">📁 확인필요 ZIP</button>
    <label class="revall" title="검수가 모두 끝나면 확인필요 사진을 자동으로 ZIP 저장"><input type="checkbox" id="autozip-cb" checked> 완료 시 자동저장</label>
    <button class="clearb" id="clear">전체 지우기</button>
  </div>
  <div id="results"></div>
</div>
<div class="lb" id="lb"><div class="hint">클릭=확대(2배씩) · 드래그=이동 · 휠=줌 · 바깥/Esc=닫기 &nbsp;<b id="lbz">1.0×</b></div><img id="lbimg"></div>
<div id="rev">
  <div class="panel">
    <div class="rtop">
      <button class="x" id="rev-x" title="닫기 (Esc)">✕</button>
      <div class="prog"><div class="b" id="rev-bar"></div></div>
      <div class="cnt" id="rev-cnt"></div>
    </div>
    <div id="rev-content"></div>
  </div>
</div>
<div id="summary">
  <div class="spanel">
    <div class="stop"><b>📊 MOBIS ID별 수량 검토 (총 수량 확인)</b><button class="sx" id="sum-x" title="닫기 (Esc)">✕</button></div>
    <div id="sum-body"></div>
  </div>
</div>
<script>
const ICON={match:"✅",mismatch:"❌",verify:"⚠️",label_only:"➖",master_only:"❓",na:"·"};
const drop=document.getElementById('drop'), file=document.getElementById('file'),
      results=document.getElementById('results'), bar=document.getElementById('bar');
let nOk=0,nMis=0,nVer=0,nNf=0,nLab=0,nImg=0,sumM=0,sumC=0;
let FILTER='all';
function applyOne(c){ c.style.display=(FILTER==='all'||c.dataset.problem!=='0')?'':'none'; }
function applyFilter(){ document.querySelectorAll('#results .card').forEach(applyOne); }
function updateBad(){ const n=problemCards().length;
  document.getElementById('f-bad').textContent='확인필요'+(n?' ('+n+')':''); updateReviewBtn(); }
function setFilter(f){ FILTER=f;
  document.getElementById('f-all').classList.toggle('on',f==='all');
  document.getElementById('f-bad').classList.toggle('on',f==='bad'); applyFilter(); }
document.getElementById('f-all').onclick=()=>setFilter('all');
document.getElementById('f-bad').onclick=()=>setFilter('bad');
drop.onclick=()=>file.click();
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hot');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hot');}));
drop.addEventListener('drop',ev=>{
  const items=ev.dataTransfer.items;
  if(items && items.length && items[0].webkitGetAsEntry){
    const ents=[]; for(const it of items){ const e=it.webkitGetAsEntry&&it.webkitGetAsEntry(); if(e) ents.push(e); }
    if(ents.length){ collectFiles(ents).then(fs=>handle(fs)); return; }
  }
  handle(ev.dataTransfer.files);
});
file.addEventListener('change',ev=>handle(ev.target.files));
const folderInput=document.getElementById('folder');
document.getElementById('folderbtn').onclick=(e)=>{ e.stopPropagation(); folderInput.click(); };
folderInput.addEventListener('change',ev=>handle(ev.target.files));
// 폴더 드래그 시 하위 파일 재귀 수집
function collectFiles(entries){
  return new Promise(resolve=>{
    const files=[]; let pending=0, done=false;
    const check=()=>{ if(done && pending===0) resolve(files); };
    function walk(entry){ pending++;
      if(entry.isFile){ entry.file(f=>{ files.push(f); pending--; check(); }, ()=>{ pending--; check(); }); }
      else if(entry.isDirectory){ const rd=entry.createReader();
        (function more(){ rd.readEntries(es=>{ if(es.length){ es.forEach(walk); more(); } else { pending--; check(); } }, ()=>{ pending--; check(); }); })();
      } else { pending--; check(); } }
    entries.forEach(walk); done=true; check();
  });
}
document.getElementById('clear').onclick=()=>{
  document.querySelectorAll('#results .card').forEach(c=>{ if(c._img && c._img.indexOf('blob:')===0) URL.revokeObjectURL(c._img); });
  results.innerHTML='';nOk=nMis=nVer=nNf=nLab=nImg=sumM=sumC=0;setFilter('all');updateBad();refresh();bar.style.display='none';};

// 재고 마스터 상태 + 업로드 (클라우드: 런타임 업로드)
function setMaster(parts,mobis){
  const mb=document.getElementById('masterbar'), s=document.getElementById('mstat');
  if(parts>0){ mb.className='ok'; s.textContent=`📋 재고 마스터 적재됨 — Part# ${parts}종 / MOBIS ID ${mobis}종`; }
  else{ mb.className='warn'; s.textContent='⚠️ 재고 마스터 없음 — 비교하려면 재고(Apr inventory) 엑셀을 업로드하세요'; }
}
fetch('/master').then(r=>r.json()).then(d=>setMaster(d.parts,d.mobis)).catch(()=>{});
const mbtn=document.getElementById('mbtn'), mfile=document.getElementById('mfile');
mbtn.onclick=()=>mfile.click();
mfile.addEventListener('change',ev=>{ const f=ev.target.files[0]; if(!f) return;
  const s=document.getElementById('mstat'); s.textContent='마스터 적재 중…';
  const rd=new FileReader();
  rd.onload=()=>fetch('/master',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:rd.result})})
    .then(r=>r.json()).then(d=>{ if(d.error){ document.getElementById('masterbar').className='warn'; s.textContent='오류: '+d.error; } else setMaster(d.parts,d.mobis); })
    .catch(e=>{ s.textContent='전송 오류: '+e; });
  rd.readAsDataURL(f);
});

function refresh(){
  bar.style.display='flex';
  const ov=document.getElementById('overall');
  const pct = sumC>0 ? Math.round(sumM/sumC*100) : null;
  ov.textContent = pct===null ? '전체 일치율 —' : `전체 일치율 ${pct}%`;
  ov.className='overall'+(pct===null?'':(pct===100?' full':(pct>=80?' part':' low')));
  document.getElementById('stat').textContent=`사진 ${nImg}장 · 라벨 ${nLab}개`;
  document.getElementById('c-ok').textContent='✅ 일치 '+nOk;
  document.getElementById('c-mis').textContent='❌ 불일치 '+nMis;
  document.getElementById('c-ver').textContent='⚠️ 확인필요 '+nVer;
  document.getElementById('c-nf').textContent='🆕 미등록 '+nNf;
}
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// 읽은 값(read)이 마스터(master)와 다른 '글자'를 빨갛게 하이라이트 (앞뒤 공통부분 제외한 가운데 차이).
const diffMark=(read,master)=>{ read=String(read==null?'':read); master=String(master==null?'':master);
  if(!read) return '—'; if(!master||read===master) return esc(read);
  const rl=read.length, ml=master.length, mn=Math.min(rl,ml);
  let p=0; while(p<mn && read[p]===master[p]) p++;
  let s=0; while(s<mn-p && read[rl-1-s]===master[ml-1-s]) s++;
  const mid=esc(read.slice(p, rl-s))||'‸';
  return esc(read.slice(0,p))+'<span class="cdiff">'+mid+'</span>'+esc(read.slice(rl-s)); };

// 클릭 시 원본 사진 전체보기 (안 잘리게)
const lb=document.getElementById('lb'), lbimg=document.getElementById('lbimg');
let lbZoom=1, lbPan={x:0,y:0}, lbDrag=null, lbDown=null, lbMoved=false;
let lbBase=null, lbMax=4;         // 화면에 꽉 채운 크기 / 원본 픽셀을 다 쓰는 배율

function lbFit(){
  const nw=lbimg.naturalWidth, nh=lbimg.naturalHeight;
  if(!nw||!nh){ lbBase=null; return; }
  const vw=lb.clientWidth-48, vh=lb.clientHeight-48;
  const s=Math.min(vw/nw, vh/nh, 1);                 // 화면에 맞춤 (원본보다 크게 시작하진 않음)
  lbBase={w:Math.round(nw*s), h:Math.round(nh*s)};
  // 원본 픽셀을 100% 쓰는 배율까지는 선명하다. 그 이상은 늘려도 정보가 없다.
  lbMax=Math.min(12, Math.max(4, nw/lbBase.w * 2));  // 원본의 2배까지는 허용 (라벨 글자 확인용)
}
function lbApply(){
  if(!lbBase) return;
  lbimg.style.width=Math.round(lbBase.w*lbZoom)+'px';
  lbimg.style.height=Math.round(lbBase.h*lbZoom)+'px';
  lbimg.style.transform='translate('+lbPan.x+'px,'+lbPan.y+'px)';
  lbimg.style.cursor=lbZoom>1?'grab':'zoom-in';
  const z=document.getElementById('lbz');
  if(z) z.textContent=lbZoom.toFixed(1)+'×';
}
function openLB(src){
  lbZoom=1; lbPan={x:0,y:0}; lbBase=null;
  lbimg.style.width=''; lbimg.style.height='';
  lbimg.onload=()=>{ lbFit(); lbApply(); };
  lbimg.src=src;
  if(lbimg.complete && lbimg.naturalWidth){ lbFit(); lbApply(); }
  lb.classList.add('show');
}
function closeLB(){ lb.classList.remove('show'); }
window.addEventListener('resize',()=>{ if(lb.classList.contains('show')){ lbFit(); lbApply(); } });
lb.onclick=(e)=>{ if(e.target===lbimg){ if(lbMoved){ lbMoved=false; return; }
    lbZoom = lbZoom*2 > lbMax ? 1 : lbZoom*2;        // 1 → 2 → 4 → 8 … → 1
    if(lbZoom===1) lbPan={x:0,y:0}; lbApply();
  } else { closeLB(); } };
lbimg.addEventListener('mousedown',e=>{ lbDown={x:e.clientX,y:e.clientY}; lbMoved=false;
  if(lbZoom>1){ lbDrag={x:e.clientX-lbPan.x,y:e.clientY-lbPan.y}; lbimg.style.cursor='grabbing'; } });
window.addEventListener('mousemove',e=>{ if(!lbDown) return;
  if(Math.abs(e.clientX-lbDown.x)+Math.abs(e.clientY-lbDown.y)>5) lbMoved=true;
  if(lbDrag){ lbPan.x=e.clientX-lbDrag.x; lbPan.y=e.clientY-lbDrag.y; lbApply(); } });
window.addEventListener('mouseup',()=>{ lbDown=null; if(lbDrag){ lbDrag=null; lbApply(); } });
lb.addEventListener('wheel',e=>{ if(!lb.classList.contains('show')) return; e.preventDefault();
  const f=e.deltaY<0?1.2:1/1.2;                       // 배율은 곱셈으로 — 고배율에서도 조작감이 같다
  lbZoom=Math.min(lbMax, Math.max(1, lbZoom*f));
  if(lbZoom<=1){ lbZoom=1; lbPan={x:0,y:0}; } lbApply(); },{passive:false});
results.addEventListener('click',e=>{
  if(e.target.tagName==='IMG' && e.target.closest('.cimg')){ openLB(e.target.src); }
});
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeLB(); });

// ── MOBIS ID별 수량 검토(총 수량 확인) ──
// labCount(m) = 이 라벨이 사진에 실제로 몇 장 있는지. 비전이 센 값이며 사람이 카드/확인모드에서 고칠 수 있다.
const labCount=m=>Math.max(1,Math.min(999,parseInt(m&&m.label_count,10)||1));
// 라벨 장수 입력칸을 결과 객체에 연결 — 고치면 그 자리에서 합계·상단 카운터까지 반영된다.
function bindCounts(root,labels){
  root.querySelectorAll('.lcnt-in').forEach((inp,i)=>{
    inp.onchange=()=>{
      const m=labels[i]; if(!m) return;
      const old=labCount(m), v=Math.max(1,Math.min(999,parseInt(inp.value,10)||1));
      inp.value=v; m.label_count=v; nLab+=v-old; refresh();
      // 같은 라벨을 카드와 확인 모드 양쪽에서 보고 있을 수 있다 → 다른 쪽 입력칸도 맞춰준다.
      document.querySelectorAll('#results .card').forEach(c=>{
        if(c._res && c._res.labels===labels){ const t=c.querySelectorAll('.lcnt-in')[i]; if(t&&t!==inp) t.value=v; } });
      const rc=document.getElementById('rev-content'), rcard=REV&&REV.list&&REV.list[REV.i];
      if(rc && rcard && rcard._res && rcard._res.labels===labels){
        const t=rc.querySelectorAll('.lcnt-in')[i]; if(t&&t!==inp) t.value=v; }
    };
  });
}
function collectLabels(){ const out=[];
  document.querySelectorAll('#results .card').forEach(c=>{ const r=c._res; if(r&&r.labels) out.push.apply(out,r.labels); });
  return out; }
function mobisOf(m){ const r=(m.rows||[]).find(x=>/MOBIS ID/.test(x.field||'')); return (r&&r.master)?r.master:''; }
function serialOf(m){ const q=(m.info||[]).find(i=>/SERIAL/i.test(i.field||'')); return String((q&&q.label)||'').trim(); }

/**
 * SERIAL 혼입 검사.
 *
 * SERIAL 은 마스터에 없어 옳고 그름을 대조할 수 없다. 대신 **같은 MOBIS ID 에
 * 몇 종이 들어왔는지** 는 셀 수 있고, 그것이 섞이면 안 되는 물건이 섞인 것을
 * 잡는 유일한 길이다. 실제로 2622·2623 이 섞인 건이 전 항목 일치로 통과했다.
 *
 * 기계가 불량으로 단정하지 않는다 — 의도한 혼입일 수 있다. 사람 눈에 걸리게만 한다.
 */
function serialGroups(){
  const g={};
  collectLabels().forEach(m=>{ const id=mobisOf(m); const sn=serialOf(m);
    if(!id||!sn) return; (g[id]=g[id]||new Set()).add(sn); });
  return g;
}
/**
 * 이번 배치에서 **대조하지 못한 것**을 한 줄로 남긴다.
 *
 * 숨기는 것과 조용히 통과시키는 것은 다르다. 제조사 릴 번호가 없는 벤더의
 * 라벨을 건건이 「확인필요」로 올리면 98건 중 93건이 확인필요가 되어 아무도
 * 안 본다(실제로 그렇게 됐다). 그렇다고 말없이 넘어가면 "두 스티커까지
 * 대조된 일치" 로 읽힌다. 건건이 아니라 **배치당 한 줄**로 올린다.
 */
function coverageNote(){
  const labels=collectLabels();
  if(!labels.length) return '';
  let done=0, none=0;
  labels.forEach(m=>{
    const r=(m.rows||[]).find(x=>String(x.field||'').indexOf('SERIAL')===0);
    if(!r) return;
    if(r.status==='match'||r.status==='mismatch') done++;
    else if(r.status==='na') none++;
  });
  if(!none) return '';
  return '<div class="covbox">제조사 릴 번호를 '+labels.length+'건 중 <b>'+done+'건</b>에서만 읽었습니다. '
    +'이 벤더 라벨에는 릴 번호가 인쇄돼 있지 않을 수 있습니다 — '
    +'<b>나머지 '+none+'건은 두 스티커 SERIAL 대조를 하지 않았습니다.</b> '
    +'같은 품목에 SERIAL 이 여러 종 들어온 경우는 아래에서 따로 표시합니다.</div>';
}
function markMixed(){
  const g=serialGroups();
  const mixed=new Set(Object.keys(g).filter(k=>g[k].size>1));
  document.querySelectorAll('#results .card').forEach(c=>{
    const r=c._res; if(!r||!r.labels) return;
    c.querySelectorAll('.mixtag').forEach(e=>e.remove());
    if(!r.labels.some(m=>mixed.has(mobisOf(m)))) return;
    if(c.dataset.problem!=='1'){ c.dataset.problem='1'; if(typeof applyOne==='function') applyOne(c); }
    const fn=c.querySelector('.fn')||c.querySelector('.rvpn');
    if(fn) fn.insertAdjacentHTML('beforeend',
      ' <span class="mixtag">⚠️ 같은 품목에 SERIAL 여러 종 — 혼입 확인</span>');
  });
}
function buildSummary(){
  // 수량은 '라벨 객체 수'가 아니라 라벨 실물 장수(label_count)의 합이다.
  // 사진 1장에 같은 라벨 10장이면 객체는 1개지만 수량은 10 — 객체 수로 세면 사실상 사진 수가 된다.
  const labels=collectLabels(), groups={}, order=[];
  labels.forEach(m=>{ const id=mobisOf(m)||'__none__';
    if(!groups[id]){ groups[id]={count:0,kinds:0,vpns:{}}; order.push(id); }
    groups[id].count+=labCount(m); groups[id].kinds++; if(m.vpn) groups[id].vpns[m.vpn]=1; });
  order.sort((a,b)=> a==='__none__'?1 : b==='__none__'?-1 : groups[b].count-groups[a].count);
  const total=labels.reduce((s,m)=>s+labCount(m),0), distinct=order.filter(id=>id!=='__none__').length;
  const body=order.map(id=>{ const g=groups[id];
    const vpns=Object.keys(g.vpns).map(esc).join(', ')||'—';
    const idCell=id==='__none__'?'미등록 / MOBIS ID 없음':esc(id);
    return '<tr class="'+(id==='__none__'?'none':'')+'"><td class="mono">'+idCell+'</td><td class="mono" style="font-size:11px;color:#777">'+vpns+'</td><td style="text-align:right;font-weight:800;font-size:15px">'+g.count+'<span style="font-size:11px;font-weight:600;color:var(--mut)">개</span></td></tr>';
  }).join('');
  const mixG=serialGroups(), mixed=Object.keys(mixG).filter(k=>mixG[k].size>1);
  const mixHtml=mixed.length?('<div class="mixbox"><div class="mixtitle">⚠️ SERIAL 이 섞인 품목 '+mixed.length+'건</div><ul class="mixlist">'
    +mixed.map(k=>'<li><b class="mono">'+esc(k)+'</b> — SERIAL '+mixG[k].size+'종<br><span class="mono" style="font-size:12px">'
      +[...mixG[k]].map(esc).join(' · ')+'</span></li>').join('')
    +'</ul><div style="font-size:11.5px;color:#6b5320;margin-top:6px">같은 품목에 서로 다른 SERIAL 이 들어왔습니다. 의도한 혼입인지 <b>직접 확인해 주십시오.</b> SERIAL 은 마스터에 없어 프로그램이 판정할 수 없습니다.</div></div>'):'';
  const html=coverageNote()+mixHtml+'<table class="sum"><thead><tr><th>MOBIS ID</th><th>부품번호(V/PN)</th><th style="text-align:right">라벨 수(장수)</th></tr></thead><tbody>'+body+'<tr class="tot"><td>합계</td><td>'+distinct+' MOBIS ID</td><td style="text-align:right">'+total+'개</td></tr></tbody></table>';
  return {html:html, total:total, distinct:distinct};
}
const summary=document.getElementById('summary');
function showSummary(){ const has=collectLabels().length;
  const cap='<div style="font-size:11.5px;color:var(--mut);margin-bottom:10px">사진 장수가 아니라 <b>라벨 실물 장수</b> 합계입니다 (사진 1장에 라벨 10장 = 10개). 장수가 틀리면 각 카드의 「라벨 __장」 칸에서 고치세요.</div>';
  document.getElementById('sum-body').innerHTML='<div style="padding:18px 22px">'+(has?cap+buildSummary().html:'<div style="color:var(--mut);text-align:center;padding:24px">검수된 라벨이 없습니다.</div>')+'</div>';
  summary.style.display='flex'; }
function closeSummary(){ summary.style.display='none'; }
document.getElementById('sumbtn').onclick=showSummary;
document.getElementById('sum-x').onclick=closeSummary;
summary.onclick=(e)=>{ if(e.target===summary) closeSummary(); };
document.addEventListener('keydown',e=>{ if(e.key==='Escape' && summary.style.display==='flex') closeSummary(); });

// ── 확인필요(❌·⚠️·🆕) 사진만 ZIP으로 묶어 다운로드 (Anthropic API 안 씀 = 무료) ──
let CRC_TABLE=null;
function crc32(bytes){
  if(!CRC_TABLE){ CRC_TABLE=new Uint32Array(256); for(let n=0;n<256;n++){ let c=n; for(let k=0;k<8;k++) c=c&1?(0xEDB88320^(c>>>1)):(c>>>1); CRC_TABLE[n]=c>>>0; } }
  let crc=0xffffffff; for(let i=0;i<bytes.length;i++) crc=(crc>>>8)^CRC_TABLE[(crc^bytes[i])&0xff]; return (crc^0xffffffff)>>>0;
}
function makeZip(files){  // files:[{name,data(Uint8Array)}] — 무압축(store) ZIP
  const enc=new TextEncoder(); const local=[]; const cent=[]; let offset=0, n=0;
  const u16=v=>[v&0xff,(v>>8)&0xff], u32=v=>[v&0xff,(v>>8)&0xff,(v>>16)&0xff,(v>>>24)&0xff];
  for(const f of files){
    const name=enc.encode(f.name), crc=crc32(f.data), sz=f.data.length;
    const lfh=new Uint8Array([0x50,0x4b,0x03,0x04,...u16(20),...u16(0),...u16(0),...u16(0),...u16(0),...u32(crc),...u32(sz),...u32(sz),...u16(name.length),...u16(0)]);
    local.push(lfh,name,f.data);
    cent.push(new Uint8Array([0x50,0x4b,0x01,0x02,...u16(20),...u16(20),...u16(0),...u16(0),...u16(0),...u16(0),...u32(crc),...u32(sz),...u32(sz),...u16(name.length),...u16(0),...u16(0),...u16(0),...u16(0),...u32(0),...u32(offset)]),name);
    offset += lfh.length + name.length + sz; n++;
  }
  const cdStart=offset; let cdSize=0; for(const c of cent) cdSize+=c.length;
  const eocd=new Uint8Array([0x50,0x4b,0x05,0x06,...u16(0),...u16(0),...u16(n),...u16(n),...u32(cdSize),...u32(cdStart),...u16(0)]);
  return new Blob([...local,...cent,eocd],{type:'application/zip'});
}
async function downloadFlaggedZip(){
  const cards=[...document.querySelectorAll('#results .card')].filter(c=>c.dataset.problem==='1' && c._file);
  if(!cards.length){ alert('확인필요(❌·⚠️·🆕) 사진이 없습니다. 검수가 끝난 뒤 눌러주세요.'); return; }
  const btn=document.getElementById('zipbtn'), old=btn.textContent; btn.textContent='압축 중…'; btn.disabled=true;
  try{
    const files=[]; const cnt={};
    for(const c of cards){
      let nm=c._file.name; cnt[nm]=(cnt[nm]||0)+1;
      if(cnt[nm]>1){ const d=nm.lastIndexOf('.'); nm=(d>0?nm.slice(0,d)+'_'+cnt[nm]+nm.slice(d):nm+'_'+cnt[nm]); }
      const buf=await c._file.arrayBuffer();
      files.push({name:nm, data:new Uint8Array(buf)});
    }
    const blob=makeZip(files);
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='확인필요_'+files.length+'장.zip'; a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href),2000);
  }catch(e){ alert('ZIP 생성 오류: '+e); }
  btn.textContent=old; btn.disabled=false;
}
document.getElementById('zipbtn').onclick=downloadFlaggedZip;
function AUTO_ZIP(){ const c=document.getElementById('autozip-cb'); return c?c.checked:true; }
// 모든 검수(재시도 대기 포함) 완료 감지 → '완료 시 자동저장' 켜져 있고 확인필요 있으면 ZIP 자동 다운로드
function maybeAllDone(){
  if(active===0 && Q.length===0 && waiting===0 && doneArmed && nImg>0){
    doneArmed=false;
    if(AUTO_ZIP()){
      const has=[...document.querySelectorAll('#results .card')].some(c=>c.dataset.problem==='1' && c._file);
      if(has) downloadFlaggedZip();
    }
  }
}

function labelHtml(m,first){
  const reg=m.registered, pct=m.pct;
  const badge=!reg?['nf','🆕 마스터에 미등록']
    :(m.has_mismatch?['mis','❌ 불일치'+(pct<100?' ('+pct+'%)':'')]
    :(m.has_verify?['ver','⚠️ 확인 필요']
    :['ok','✅ 전체 일치']));
  const comp=m.rows.map(r=>`<tr class="r-${r.status}"><td>${esc(r.field)}</td>
     <td class="mono">${((r.status==='verify'||r.status==='mismatch')&&r.label)?diffMark(r.label,r.master):(esc(r.label)||'—')}${r.corrected?' <span class="corr">OCR보정</span>':''}</td><td class="mono">${esc(r.master)||'—'}</td>
     <td class="st">${ICON[r.status]}</td></tr>${r.note?`<tr class="r-note"><td></td>
     <td colspan="3" class="note">⚠️ ${esc(r.note)}</td></tr>`:''}`).join('');
  const info=m.info.map(i=>`<span class="ic"><b>${esc(i.field)}</b>${esc(i.label)||'—'}</span>`).join('');
  const cnt=labCount(m);
  return `${first?'':'<div class="sub-lab">같은 사진의 라벨 추가</div>'}
    <div class="chead"><div class="vpn mono">${esc(m.vpn)||'(부품번호 못 읽음)'}</div>
      <div style="display:flex;align-items:center;gap:8px">
        <div class="lcnt${cnt>1?' multi':''}" title="이 사진에 붙어 있는 이 라벨의 장수입니다(사진 장수 아님). 다르면 직접 고치세요 — 합계에 바로 반영됩니다">라벨
          <input type="number" min="1" max="999" step="1" class="lcnt-in" value="${cnt}">장</div>
        <div class="badge ${badge[0]}">${badge[1]}</div>
      </div></div>
    <div class="gauge ${(pct<100||m.has_verify||m.has_mismatch)?'lo':''}"><div class="bar" style="width:${reg?pct:0}%"></div>
      <span>${pct}% · ${m.matched}/${m.comparable} 항목${m.has_verify?' · ⚠️확인필요 포함':''}</span></div>
    <table class="cmp"><thead><tr><th>항목</th><th>라벨에서 읽음</th><th>기준값 (마스터)</th><th></th></tr></thead>
      <tbody>${comp}</tbody></table>
    <div class="info">${info}</div>`;
}

function handle(files){
  for(const f of files){ if(f.type.startsWith('image/')) uploadOne(f); }
}
// 동시 호출 제한 + 자동 백오프 재시도 (529 과부하 흡수)
const MAX_CONCURRENT=10, MAX_AUTORETRY=5;  // 5→10: 서버 직렬화(async 안 블로킹 호출)를 푼 뒤 상향.
                                           // 천장은 노트북 성능이 아니라 API 분당 토큰한도 — 429 재시도율 보고 조절할 것.
let active=0; const Q=[]; let waiting=0, doneArmed=false;   // waiting=재시도 대기중, doneArmed=완료 감지 대상
function setPending(card,txt){ card.querySelector('.cbody').innerHTML=`<div class="pending"><span class="spin"></span> ${esc(txt)}</div>`; }

function uploadOne(f){
  nImg++; doneArmed=true; refresh();
  const card=document.createElement('div'); card.className='card';
  const url=URL.createObjectURL(f);   // object URL 표시(480장 메모리 절약 — base64는 판독 시점에만 읽음)
  card.innerHTML=`<div class="cimg"><img src="${url}"><div class="fn">${esc(f.name)}</div></div><div class="cbody"></div>`;
  card._img=url; card._file=f;
  results.prepend(card); card.dataset.problem=''; setPending(card,'대기 중…'); applyOne(card);
  enqueue(card,0);
}
function enqueue(card,attempt){ card.dataset.problem=''; applyOne(card); Q.push({card,attempt}); pump(); }
function pump(){ while(active<MAX_CONCURRENT && Q.length){ active++; runJob(Q.shift()); } }

function runJob(job){
  const {card,attempt}=job;
  setPending(card, attempt?`재시도 중… (${attempt}/${MAX_AUTORETRY})`:'라벨 읽는 중…');
  const reader=new FileReader();
  reader.onload=()=>{
    fetch('/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:reader.result})})
    .then(r=>r.json()).then(res=>{ active--;
      if(res.error){ handleErr(card,attempt,res.error); } else { renderResult(card,res); }
      pump(); maybeAllDone();
    }).catch(e=>{ active--; handleErr(card,attempt,String(e)); pump(); maybeAllDone(); });
  };
  reader.onerror=()=>{ active--; handleErr(card,attempt,'파일 읽기 실패'); pump(); maybeAllDone(); };
  reader.readAsDataURL(card._file);
}

function handleErr(card,attempt,msg){
  const retriable=/Overloaded|529|429|rate|overloaded|timeout|ETIMEDOUT|ECONN|Connection|APIConnection/i.test(msg);
  const fatal=/credit|balance/i.test(msg);
  if(retriable && !fatal && attempt<MAX_AUTORETRY){
    const wait=Math.min(3000*Math.pow(1.8,attempt),25000);  // 3s,5.4s,9.7s,17.5s,25s
    setPending(card,`API 혼잡 — ${Math.round(wait/1000)}초 후 자동 재시도 (${attempt+1}/${MAX_AUTORETRY})`);
    waiting++; setTimeout(()=>{ waiting--; enqueue(card,attempt+1); },wait);   // 재시도 대기중=미완료로 카운트
    return;
  }
  errCard(card,msg);
}

function renderResult(card,res){
  const body=card.querySelector('.cbody');
  card._res=res;
  if(!res.labels.length){ body.innerHTML='<div class="err">라벨을 찾지 못했습니다.</div>'; card.dataset.problem='1'; applyOne(card); updateBad(); return; }
  body.innerHTML=res.labels.map((m,i)=>labelHtml(m,i===0)).join('');
  bindCounts(body,res.labels);
  // nLab 은 '라벨 장수' 합계 — 사진에 같은 라벨이 10장이면 10을 더한다(1이 아니라).
  // 일치율·상태 카운터(nOk/nMis/…)는 판독 단위인 라벨 종류 기준 그대로 둔다.
  res.labels.forEach(m=>{ nLab+=labCount(m); sumM+=m.matched; sumC+=m.comparable;
    if(!m.registered)nNf++; else if(m.has_mismatch)nMis++; else if(m.has_verify)nVer++; else nOk++; });
  card.dataset.problem=res.labels.some(m=>!m.registered||m.has_mismatch||m.has_verify)?'1':'0';
  markMixed();
  applyOne(card); updateBad(); refresh();
}

// ── 확인 모드 (게임화): 확인필요 이미지만 한 장씩 → 사람이 보고 ✅확인 → 다음 ──
// (B안) '✅ 정상도 확인' 켜면 정상(전체일치) 카드까지 육안 검산 대상에 포함 → false ✅ 통과 방지.
let REVIEW_ALL=false;
function problemCards(){ return [...document.querySelectorAll('#results .card')]
  .filter(c=>c.dataset.problem==='1' && c.dataset.confirmed!=='1'); }
function reviewCards(){ return [...document.querySelectorAll('#results .card')]
  .filter(c=> c.dataset.confirmed!=='1' && (REVIEW_ALL ? (c.dataset.problem==='1'||c.dataset.problem==='0') : c.dataset.problem==='1')); }
function updateReviewBtn(){ const n=reviewCards().length; const b=document.getElementById('reviewbtn');
  b.disabled=n===0; b.textContent=(n?`🔍 확인 모드 (${n})`:'🔍 확인 모드')+(REVIEW_ALL?' · 전수':''); }
let REV={list:[],i:0};
function startReview(){ REV.list=reviewCards(); REV.i=0; if(!REV.list.length) return;
  document.getElementById('rev').style.display='flex'; renderRev(); }
function closeReview(){ document.getElementById('rev').style.display='none'; }
function revWhy(m){ return !m.registered?'🆕 마스터에 미등록 — 신규 부품인지 확인'
  : m.has_mismatch?'❌ 불일치 항목 있음 — 라벨과 마스터가 다름'
  : m.has_verify?'⚠️ 확인 필요 — 한 글자 차이/판독 불확실'
  : '✅ 전체 일치 — 읽은 값이 마스터와 같은지 눈으로 검산'; }
function renderRev(){
  const total=REV.list.length;
  document.getElementById('rev-bar').style.width=(total?REV.i/total*100:0)+'%';
  const C=document.getElementById('rev-content');
  if(REV.i>=total){
    document.getElementById('rev-cnt').textContent=`${total} / ${total}`;
    document.getElementById('rev-bar').style.width='100%';
    const sum=buildSummary();
    C.innerHTML=`<div class="done"><div class="em">🎉</div><h2>모두 확인 완료!</h2>
      <p>${total}건을 검수했습니다. 수고하셨습니다.</p>
      <div style="text-align:left;margin:20px auto 0;max-width:560px"><div style="font-size:12px;font-weight:700;color:var(--mut);margin-bottom:8px">📊 MOBIS ID별 수량 (라벨 총 ${sum.total}개 · ${sum.distinct} MOBIS ID)</div>${sum.html}</div>
      <div style="margin-top:22px"><button class="ok" style="max-width:220px;margin:0 auto" id="rev-fin">닫기</button></div></div>`;
    C.querySelector('#rev-fin').onclick=closeReview; return;
  }
  document.getElementById('rev-cnt').textContent=`${REV.i+1} / ${total}`;
  const card=REV.list[REV.i], res=card._res||{labels:[]};
  // (B안) 문제 라벨만이 아니라 이 사진의 모든 라벨(✅ 포함)을 보여줘 육안 검산 가능하게.
  const shown=res.labels;
  const rowCls=s=> s==='mismatch'?'bad' : s==='verify'?'ver' : (s==='match'?'okrow':'');
  const blocks=shown.map(m=>{
    // 오류난 행만이 아니라 전체 확인란(모든 필드)을 보여준다. 오류 행은 색으로 강조.
    const tbl=`<table><thead><tr><th>항목</th><th>라벨에서 읽음</th><th>마스터</th><th></th></tr></thead><tbody>${
      m.rows.map(r=>`<tr class="${rowCls(r.status)}"><td>${esc(r.field)}</td><td>${((r.status==='verify'||r.status==='mismatch')&&r.label)?diffMark(r.label,r.master):(esc(r.label)||'—')}</td><td>${esc(r.master)||'—'}</td><td style="text-align:center">${ICON[r.status]||''}</td></tr>`).join('')}</tbody></table>`;
    const info=(m.info||[]).map(i=>`<span class="ic"><b>${esc(i.field)}</b>${esc(i.label)||'—'}</span>`).join('');
    const badge=!m.registered?'🆕 미등록':(m.has_mismatch?('❌ 불일치'+(m.pct<100?' ('+m.pct+'%)':'')):(m.has_verify?'⚠️ 확인필요':'✅ 일치'));
    const cnt=labCount(m);
    // 사진을 크게 보는 자리 = 장수를 눈으로 세기 가장 좋은 자리. 여기서 고친 값이 합계에 바로 반영된다.
    const cbox=`<div class="lcnt${cnt>1?' multi':''}" style="margin:8px 0 2px" title="사진에 보이는 이 라벨의 장수 — 직접 세어 고치세요">라벨 <input type="number" min="1" max="999" step="1" class="lcnt-in" value="${cnt}">장</div>`;
    return `<div class="rvpn">${esc(m.vpn)||'(부품번호 못 읽음)'}<span class="rbadge">${badge}</span></div>
      <div class="rwhy">${revWhy(m)}</div>${cbox}${tbl}${info?`<div class="info" style="margin-top:10px">${info}</div>`:''}`;
  }).join('<hr style="border:none;border-top:1px dashed #eee;margin:16px 0">');
  C.innerHTML=`<img class="rimg" src="${card._img||''}">
    <div class="rbody">${blocks||'<div class="rwhy">확인 필요</div>'}</div>
    <div class="ract">
      <button class="prev" id="rev-prev" ${REV.i===0?'disabled':''}>← 이전</button>
      <button class="ok" id="rev-ok">✅ 확인 (맞음) <span style="opacity:.7;font-weight:600">⏎</span></button>
      <button class="bad" id="rev-bad">❌ 문제</button>
    </div>`;
  bindCounts(C,res.labels);
  C.querySelector('#rev-ok').onclick=()=>{ markCard(card,true); REV.i++; renderRev(); };
  C.querySelector('#rev-bad').onclick=()=>{ markCard(card,false); REV.i++; renderRev(); };
  const pv=C.querySelector('#rev-prev'); if(pv) pv.onclick=()=>{ REV.i=Math.max(0,REV.i-1); renderRev(); };
  const rimg=C.querySelector('.rimg'); if(rimg) rimg.onclick=()=>openLB(rimg.src);
}
function markCard(card,ok){
  card.dataset.confirmed='1'; card.classList.add('confirmed');
  if(!ok) card.dataset.realproblem='1';
  const fn=card.querySelector('.fn');
  if(fn && !fn.querySelector('.cfmtag')) fn.insertAdjacentHTML('beforeend',` <span class="cfmtag">${ok?'확인됨':'문제표시'}</span>`);
  updateBad();
}
document.getElementById('reviewbtn').onclick=startReview;
document.getElementById('revall-cb').onchange=(e)=>{ REVIEW_ALL=e.target.checked; updateReviewBtn(); };
document.getElementById('rev-x').onclick=closeReview;
document.addEventListener('keydown',e=>{
  if(document.getElementById('rev').style.display!=='flex') return;
  if(lb.classList.contains('show')){ if(e.key==='Escape'){ e.preventDefault(); lb.classList.remove('show'); } return; }
  // 라벨 장수 입력칸에서 Enter/화살표는 숫자 수정용이다 — 다음 카드로 넘기면 고친 값이 날아간다.
  if(e.target && e.target.classList && e.target.classList.contains('lcnt-in')){
    if(e.key==='Enter'){ e.preventDefault(); e.target.blur(); }   // blur → change 발생 → 합계 반영
    return; }
  if(e.key==='Escape'){ closeReview(); }
  else if(e.key==='Enter'||e.key==='ArrowRight'){ const b=document.getElementById('rev-ok')||document.getElementById('rev-fin'); if(b){e.preventDefault();b.click();} }
  else if(e.key==='ArrowLeft'){ const b=document.getElementById('rev-prev'); if(b&&!b.disabled) b.click(); }
});

function errCard(card,msg){
  const friendly = /credit|balance/i.test(msg) ? 'API 크레딧 부족 — 충전 후 ↻ 다시 시도하세요.'
                 : /Overloaded|529/i.test(msg) ? 'API 과부하가 계속됩니다(529). 잠시 뒤 ↻ 다시 시도하세요.'
                 : '오류: '+msg;
  const b=card.querySelector('.cbody');
  b.innerHTML=`<div class="err">${esc(friendly)}</div><button class="retry">↻ 다시 시도</button>`;
  card.dataset.problem='1'; applyOne(card); updateBad();
  b.querySelector('.retry').onclick=()=>enqueue(card,0);
}
</script></body></html>"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="부품 라벨 검수 웹앱")
    ap.add_argument("--port", type=int, default=8775)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    env_port = os.environ.get("PORT")
    if env_port:                                   # 클라우드(Render 등): 0.0.0.0 + $PORT
        host, port = "0.0.0.0", int(env_port); args.no_open = True
    else:
        host, port = "127.0.0.1", find_free_port(args.port)
    url = f"http://localhost:{port}/"
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"\n라벨 검수 웹앱 → {url} (bind {host}:{port})\n종료: Ctrl+C")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료."); httpd.shutdown()
