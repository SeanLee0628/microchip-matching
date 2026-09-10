#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unitrontech 라벨 생성기 (label-maker 통합).

Mobis 출고내역 엑셀 업로드 → MOBIS shipping lot management 파싱 →
자재 선택 → 최근 LOT 목록에서 체크 + 수량 입력 → 라벨(QR+텍스트) PDF 생성.

표준 라이브러리 + openpyxl/msoffcrypto/segno/PIL.
"""
from __future__ import annotations
import base64, io, json, os, socket, threading, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dataurl
import labelmaker_core as LM
import labelmaker_lot as LP

DEFAULT_PW = "9178"
DEFAULT_MSL = "3"        # 파일에 MSL 열이 없을 때 쓰는 값 (예전엔 이 값이 코드에 박혀 있었다)

# ---- 전역 상태 ----
LOTS = []                 # 파싱된 전체 LOT
BY_MOBIS = {}             # mobis_id -> [lot,...]
MATERIALS = []            # [{mobis_id, material_code, vpn, n}]


def load_decrypted(raw, password):
    """업로드 bytes → openpyxl 로드 가능한 buffer. OLE2(암호화)면 복호화."""
    if raw[:4] == b"\xd0\xcf\x11\xe0":            # OLE2 = 암호화된 OOXML
        import msoffcrypto
        off = msoffcrypto.OfficeFile(io.BytesIO(raw))
        off.load_key(password=password or "")
        out = io.BytesIO(); off.decrypt(out); out.seek(0); return out
    return io.BytesIO(raw)                          # 평문 xlsx


def ingest(raw, password):
    global LOTS, BY_MOBIS, MATERIALS
    buf = load_decrypted(raw, password)
    LOTS = LP.parse(buf)
    BY_MOBIS = {}
    for l in LOTS:
        BY_MOBIS.setdefault(l["mobis_id"], []).append(l)
    MATERIALS = []
    for m, ls in BY_MOBIS.items():
        MATERIALS.append(dict(mobis_id=m, material_code=ls[0]["material_code"],
                              vpn=ls[0]["vpn"], n=len(ls)))
    MATERIALS.sort(key=lambda x: x["vpn"])
    return len(LOTS), len(MATERIALS)


def lots_for(mobis_id, limit=1500):
    ls = BY_MOBIS.get(mobis_id, [])
    ls = sorted(ls, key=lambda x: x["row"], reverse=True)      # 최근(=아래 행) 먼저
    return ls[:limit], len(ls)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
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
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length)) if length else {}
            if self.path == "/upload":
                n_lots, n_mat = ingest(dataurl.to_bytes(payload["file"]),
                                       payload.get("password") or DEFAULT_PW)
                result = {"ok": True, "lots": n_lots, "materials": MATERIALS}
            elif self.path == "/lots":
                ls, total = lots_for(payload["mobis_id"])
                result = {"lots": ls, "total": total, "shown": len(ls)}
            elif self.path == "/generate":
                today = datetime.datetime.now().strftime("%Y%m%d")
                fields = []
                for it in payload["items"]:
                    if not str(it.get("qty") or "").strip():   # 수량은 파일에서만 온다
                        raise ValueError(f"수량이 없는 LOT 입니다: {it.get('lot')}")
                    fields.append(dict(
                        material=it.get("material_code") or LM.material_code(it.get("mobis_id", "")),
                        serial=LM.serial_from(it["datecode"], it["lot"]),
                        qty=it["qty"], maker="SJYV",
                        vpn=it["vpn"], msl=str(it.get("msl") or DEFAULT_MSL), lot=it["lot"],
                        stock_day=today))
                html = LM.render_page(fields)
                self._send(200, html, "text/html; charset=utf-8"); return
            else:
                self._send(404, json.dumps({"error": "not found"})); return
            self._send(200, json.dumps(result, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))


PAGE = r"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Unitrontech 라벨 생성</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
:root{--red:#c43a3a;--ink:#1d1d20;--mut:#8a8a92;--line:#ececef;--bg:#f5f5f7;--green:#3f9d6b;}
*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',system-ui,sans-serif;}
.mono{font-family:ui-monospace,Consolas,monospace;}
header{background:linear-gradient(135deg,#1d1d20,#2c2c33 55%,#3a2326);color:#fff;padding:22px 30px;}
header h1{margin:0;font-size:20px;font-weight:800;}
header .sub{margin-top:6px;font-size:12px;color:#b9b9c2;}
.wrap{max-width:1150px;margin:0 auto;padding:22px 26px 80px;}
.card{background:#fff;border-radius:13px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 4px 18px rgba(0,0,0,.05);}
.drop{border:2.5px dashed #cfcfd6;border-radius:14px;padding:26px;text-align:center;cursor:pointer;}
.drop.hot{border-color:var(--red);background:#fdf4f4;}
.drop .big{font-size:15px;font-weight:700;} .drop .hint{font-size:12px;color:var(--mut);margin-top:5px;}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
.btn{background:var(--ink);color:#fff;border:none;font-family:inherit;font-weight:700;font-size:13px;padding:9px 18px;border-radius:9px;cursor:pointer;}
.btn:hover{background:#000;} .btn[disabled]{opacity:.4;cursor:default;}
.btn.red{background:var(--red);} .btn.red:hover{filter:brightness(.95);}
input[type=text],input[type=password],input[type=number],select{font-family:inherit;font-size:13px;padding:8px 10px;border:1px solid #d4d4da;border-radius:8px;}
label.f{font-size:12px;color:var(--mut);font-weight:600;margin-right:4px;}
.matlist{max-height:230px;overflow:auto;border:1px solid var(--line);border-radius:10px;margin-top:10px;}
.mat{display:flex;gap:12px;align-items:center;padding:9px 13px;border-bottom:1px solid var(--line);cursor:pointer;font-size:13px;}
.mat:hover{background:#faf5f5;} .mat.on{background:#fbe9e9;}
.mat .vpn{font-weight:700;flex:1;} .mat .mid{color:var(--mut);font-size:11.5px;} .mat .n{font-size:11px;color:var(--mut);}
table.lots{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px;}
table.lots th{position:sticky;top:0;background:#fafafb;text-align:left;color:var(--mut);font-size:10.5px;text-transform:uppercase;padding:7px 9px;border-bottom:1px solid var(--line);}
table.lots td{padding:6px 9px;border-bottom:1px solid var(--line);}
.lotswrap{max-height:400px;overflow:auto;border:1px solid var(--line);border-radius:10px;}
table.lots input[type=number]{width:78px;padding:5px 7px;}
.tray{position:sticky;bottom:0;background:#fff;border-top:2px solid var(--ink);padding:13px 20px;display:flex;gap:14px;align-items:center;box-shadow:0 -4px 18px rgba(0,0,0,.06);}
.tray .cnt{font-weight:800;font-size:14px;} .muted{color:var(--mut);font-size:12px;}
.pill{font-size:11px;background:#eef;border-radius:6px;padding:2px 8px;color:#446;}
.err{color:var(--red);font-size:12.5px;} .ok{color:var(--green);font-weight:700;}
.hide{display:none;}
</style></head><body>
<header><h1>Unitrontech 라벨 생성 · 모비스향</h1>
<div class="sub">LOT 엑셀 업로드 → 자재 선택 → LOT 체크 → 라벨(QR) 생성·인쇄 · 수량은 엑셀의 수량 열에서 읽습니다</div></header>
<div class="wrap">

  <div class="card" id="c-up">
    <div class="drop" id="drop">
      <div class="big">📄 Mobis 출고내역 엑셀을 여기로 끌어다 놓거나 클릭</div>
      <div class="hint">.xlsx · 암호화 파일 자동 복호화 (기본 비번 9178)</div>
      <input id="file" type="file" accept=".xlsx" class="hide">
    </div>
    <div class="row" style="margin-top:12px">
      <label class="f">복호화 비번</label><input id="pw" type="password" value="9178" style="width:120px">
      <span id="upstat" class="muted"></span>
    </div>
  </div>

  <div class="card hide" id="c-mat">
    <div class="row"><b>1. 자재 선택</b><span id="matcount" class="muted"></span>
      <input id="matsearch" type="text" placeholder="부품명 / MOBIS ID 검색" style="margin-left:auto;width:260px"></div>
    <div class="matlist" id="matlist"></div>
  </div>

  <div class="card hide" id="c-lots">
    <div class="row"><b>2. LOT 선택</b><span id="lotstat" class="muted"></span>
      <label style="margin-left:auto;font-size:12.5px"><input type="checkbox" id="selall"> 보이는 것 전체선택</label></div>
    <div class="lotswrap"><table class="lots">
      <thead><tr><th style="width:34px"></th><th>DATE CODE</th><th>LOT</th><th>입고일</th><th>SERIAL(자동)</th><th style="width:130px">수량(파일)</th><th style="width:80px">MSL</th></tr></thead>
      <tbody id="lotbody"></tbody></table></div>
  </div>

</div>

<div class="tray hide" id="tray">
  <span class="cnt" id="traycnt">0장 선택됨</span>
  <span id="trayerr" class="err"></span>
  <button class="btn red" id="gen" style="margin-left:auto" disabled>🏷️ 라벨 생성 · 인쇄</button>
</div>

<script>
const DEFAULT_MSL='__DEFAULT_MSL__';   // 파일에 MSL 열이 없을 때
const $=s=>document.querySelector(s);
const drop=$('#drop'), file=$('#file');
let MATERIALS=[], CURRENT=null, LOTROWS=[];   // LOTROWS: [{data, qty, checked}]

// ---- 업로드 ----
drop.onclick=()=>file.click();
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hot');}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hot');}));
drop.addEventListener('drop',ev=>{const f=ev.dataTransfer.files[0]; if(f) upload(f);});
file.addEventListener('change',ev=>{const f=ev.target.files[0]; if(f) upload(f);});

function upload(f){
  $('#upstat').textContent='업로드·파싱 중… (수 초 걸립니다)';
  const rd=new FileReader();
  rd.onload=()=>fetch('/labelmaker/upload',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file:rd.result, password:$('#pw').value})})
    .then(r=>r.json()).then(d=>{
      if(d.error){ $('#upstat').innerHTML='<span class="err">오류: '+esc(d.error)+'</span>'; return; }
      MATERIALS=d.materials;
      $('#upstat').innerHTML='<span class="ok">✔ LOT '+d.lots.toLocaleString()+'건 · 자재 '+d.materials.length+'종 로드</span>';
      $('#matcount').textContent='('+d.materials.length+'종)';
      $('#c-mat').classList.remove('hide'); renderMats('');
    }).catch(e=>{ $('#upstat').innerHTML='<span class="err">전송 오류: '+esc(String(e))+'</span>'; });
  rd.readAsDataURL(f);
}
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// ---- 자재 목록 ----
$('#matsearch').addEventListener('input',e=>renderMats(e.target.value));
function renderMats(q){
  q=(q||'').toLowerCase();
  const box=$('#matlist'); box.innerHTML='';
  const f=MATERIALS.filter(m=>!q||m.vpn.toLowerCase().includes(q)||m.mobis_id.toLowerCase().includes(q)||m.material_code.toLowerCase().includes(q));
  f.slice(0,400).forEach(m=>{
    const d=document.createElement('div'); d.className='mat'+(CURRENT&&CURRENT.mobis_id===m.mobis_id?' on':'');
    d.innerHTML=`<span class="vpn mono">${esc(m.vpn)}</span><span class="mid mono">${esc(m.mobis_id)} · ${esc(m.material_code)}</span><span class="n">LOT ${m.n}건</span>`;
    d.onclick=()=>pickMat(m); box.appendChild(d);
  });
  if(!f.length) box.innerHTML='<div class="mat muted">일치하는 자재 없음</div>';
}

// ---- LOT 목록 ----
function pickMat(m){
  CURRENT=m; renderMats($('#matsearch').value);
  $('#c-lots').classList.remove('hide');
  $('#lotstat').textContent='불러오는 중…'; $('#selall').checked=false;
  fetch('/labelmaker/lots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mobis_id:m.mobis_id})})
    .then(r=>r.json()).then(d=>{
      // 수량·MSL 은 파일에 있으면 그 값으로 채운다 (없으면 수량은 빈칸, MSL 은 기본 3). 둘 다 수정 가능.
      LOTROWS=d.lots.map(x=>({data:x, qty:x.qty||'', msl:x.msl||DEFAULT_MSL, checked:false}));
      const fromFile=d.lots.filter(x=>x.qty).length;
      $('#lotstat').textContent=`${esc(m.vpn)} — ${d.shown}건 표시${d.total>d.shown?' (전체 '+d.total+'건 중 최근)':''}`
        +(fromFile?` · 수량 ${fromFile}건`
                  :' · ⚠️ 이 파일에는 수량 열이 없어 라벨을 만들 수 없습니다 (엑셀에 수량 열을 추가하세요)');
      renderLots();
    });
}
function serialOf(x){ const dc=(x.datecode||'').replace(/\D/g,''); const yyww=dc.slice(-4); return dc?yyww+'00'+x.lot:'(datecode 없음)'; }
function renderLots(){
  const tb=$('#lotbody'); tb.innerHTML='';
  LOTROWS.forEach((r,i)=>{
    const tr=document.createElement('tr');
    // 수량은 파일에서만 온다. 수량 없는 LOT 은 고를 수 없다 (엑셀에 수량 열을 넣어야 한다).
    tr.innerHTML=`<td><input type="checkbox" data-i="${i}" class="ck" ${r.checked?'checked':''}
                      ${r.qty?'':'disabled title="파일에 수량이 없어 라벨을 만들 수 없습니다"'}></td>
      <td class="mono">${esc(r.data.datecode)||'—'}</td>
      <td class="mono"><b>${esc(r.data.lot)}</b></td>
      <td class="muted">${esc(r.data.received)||'—'}</td>
      <td class="mono muted">${esc(serialOf(r.data))}</td>
      <td class="mono">${r.qty?Number(r.qty).toLocaleString('ko-KR'):'<span class="err">— 파일에 수량 없음</span>'}</td>
      <td><input type="number" min="1" max="6" data-i="${i}" class="ms" value="${esc(r.msl)}"
                 title="${r.data.msl?'파일 값 (수정 가능)':'파일에 MSL 없음 — 기본 '+DEFAULT_MSL}"></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll('.ck').forEach(c=>c.onchange=e=>{LOTROWS[+e.target.dataset.i].checked=e.target.checked; syncTray();});
  tb.querySelectorAll('.ms').forEach(c=>c.oninput=e=>{LOTROWS[+e.target.dataset.i].msl=e.target.value; syncTray();});
  syncTray();
}
$('#selall').onchange=e=>{LOTROWS.forEach(r=>{ if(r.qty) r.checked=e.target.checked; }); renderLots();};

// ---- 트레이 / 생성 ----
function chosen(){ return LOTROWS.filter(r=>r.checked); }
function syncTray(){
  const c=chosen(); $('#tray').classList.toggle('hide', c.length===0);
  $('#traycnt').textContent=c.length+'장 선택됨';
  $('#gen').disabled=c.length===0;
  const nomsl=c.filter(r=>!(+r.msl>0)).length;
  $('#trayerr').textContent=nomsl?`⚠️ MSL 미입력 ${nomsl}건`:'';
}
$('#gen').onclick=()=>{
  const c=chosen();
  if(c.some(r=>!(+r.msl>0))){ alert('선택한 LOT의 MSL 을 모두 입력하세요.'); return; }
  const items=c.map(r=>({material_code:r.data.material_code, vpn:r.data.vpn, datecode:r.data.datecode,
                         lot:r.data.lot, qty:String(r.qty), msl:String(r.msl)}));
  fetch('/labelmaker/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})})
    .then(r=>r.text()).then(html=>{ const w=window.open('','_blank'); w.document.write(html); w.document.close(); setTimeout(()=>w.print(),400); });
};
</script></body></html>"""

PAGE = PAGE.replace("__DEFAULT_MSL__", DEFAULT_MSL)


def find_free_port(pref):
    for p in (pref, pref + 1, pref + 2, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p)); port = s.getsockname()[1]; s.close(); return port
        except OSError:
            s.close()
    return pref


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Unitrontech 라벨 생성 웹앱")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    port = find_free_port(args.port)
    url = f"http://localhost:{port}/"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n라벨 생성 웹앱 → {url}\n종료: Ctrl+C")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료."); httpd.shutdown()
