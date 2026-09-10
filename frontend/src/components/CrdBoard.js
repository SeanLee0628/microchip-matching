import React, { useState, useRef, useMemo } from "react";
import axios from "axios";

const API = process.env.REACT_APP_API_URL || "";

// 2026-09-10 영업1실 요청서 반영:
//   현황보기 = 경과된 것(경과일수 = MAD-CRD > 0)만 표로, 검색, 원본+경과일수 엑셀 다운
//   변화보기 = 현재 백록 기준 변화된 라인(GAP≠0 + 신규 N/A)만 표로, 원본+이전MAD·GAP 엑셀 다운
// 신호등(위험/임박/여유)은 버리지 않고 경과일수 칸 색과 요약 버튼으로 남긴다.

const RISK = {
  red:     { label: "위험",   dot: "🔴", color: "#dc2626", bg: "#fef2f2", border: "#fecaca" },
  yellow:  { label: "임박",   dot: "🟡", color: "#d97706", bg: "#fffbeb", border: "#fde68a" },
  green:   { label: "여유",   dot: "🟢", color: "#16a34a", bg: "#f0fdf4", border: "#bbf7d0" },
  unknown: { label: "CRD미상", dot: "⚪", color: "#64748b", bg: "#f8fafc", border: "#e2e8f0" },
};
const ORDER = ["red", "yellow", "green", "unknown"];
const TYPE_LABEL = { OR: "양산", FD: "샘플" };

const fmtQty = (q) => (q == null || q === "" ? "" : Number(q).toLocaleString());
const NA = "N/A";
const PAGE = 300;   // 백록이 3천 행대다 — 화면은 나눠 그리고 엑셀은 전수로 내려준다

// 검색 필드 — 요청서 A6: "검색은 주로 ORDER TYPE, PO#, MPN, DID, END CUSTOMER, FSE, CUST"
const SEARCH_FIELDS = [
  { key: "", label: "전체" },
  { key: "order_type", label: "ORDER TYPE" },
  { key: "so", label: "SO" },
  { key: "po", label: "PO#" },
  { key: "mpn", label: "MPN" },
  { key: "did", label: "DID" },
  { key: "customer", label: "END CUSTOMER" },
  { key: "fse", label: "FSE" },
  { key: "cust", label: "CUST" },
];
const SEARCHABLE = SEARCH_FIELDS.filter(f => f.key).map(f => f.key);

const hit = (row, field, value) => {
  const v = value.trim().toLowerCase();
  if (!v) return true;
  const keys = field ? [field] : SEARCHABLE;
  return keys.some(k => String(row[k] ?? "").toLowerCase().includes(v));
};

/** 사내 공통 검색바(필드 + 값 AND 필드 + 값 · 조회/초기화). */
function SearchBar({ onSearch }) {
  const blank = { f1: "", v1: "", f2: "mpn", v2: "" };
  const [q, setQ] = useState(blank);
  const set = (k) => (e) => setQ({ ...q, [k]: e.target.value });
  const fire = () => onSearch({ ...q });
  const onKey = (e) => { if (e.key === "Enter") fire(); };

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                  background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                  padding: "10px 12px", marginBottom: 14 }}>
      <select value={q.f1} onChange={set("f1")} style={S.sel}>
        {SEARCH_FIELDS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
      </select>
      <input value={q.v1} onChange={set("v1")} onKeyDown={onKey} placeholder="값 입력…" style={S.inp} />
      <span style={{ fontSize: 11.5, color: "#94a3b8", fontWeight: 600 }}>AND</span>
      <select value={q.f2} onChange={set("f2")} style={S.sel}>
        {SEARCH_FIELDS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
      </select>
      <input value={q.v2} onChange={set("v2")} onKeyDown={onKey} placeholder="값 입력…" style={S.inp} />
      <button onClick={fire} style={S.btnRed}>조회</button>
      <button onClick={() => { setQ(blank); onSearch(blank); }} style={S.btnGhost}>초기화</button>
    </div>
  );
}

function CrdBoard() {
  const [mode, setMode] = useState("now");   // "now" 현황 / "change" 변화

  // ── 현황 보기 ──
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [fileObj, setFileObj] = useState(null);
  const [buffer, setBuffer] = useState(7);
  const [elapsedOnly, setElapsedOnly] = useState(true);   // 요청: 경과된 것만
  const [riskFilter, setRiskFilter] = useState(null);
  const [typeFilter, setTypeFilter] = useState(null);
  const [query, setQuery] = useState({ f1: "", v1: "", f2: "mpn", v2: "" });
  const [limit, setLimit] = useState(PAGE);
  const [dl, setDl] = useState(false);
  const fileRef = useRef();

  // ── 변화 보기 ──
  const [prevFile, setPrevFile] = useState(null);
  const [curFile, setCurFile] = useState(null);
  const [cmp, setCmp] = useState(null);
  const [cmpLoading, setCmpLoading] = useState(false);
  const [cmpError, setCmpError] = useState(null);
  const [indefOnly, setIndefOnly] = useState(false);
  const [cmpQuery, setCmpQuery] = useState({ f1: "", v1: "", f2: "mpn", v2: "" });
  const [cmpLimit, setCmpLimit] = useState(PAGE);
  const [cmpDl, setCmpDl] = useState(false);
  const prevRef = useRef();
  const curRef = useRef();

  const doUpload = async (file, buf) => {
    if (!file) return;
    setLoading(true); setError(null);
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await axios.post(`${API}/api/crd-board?buffer_days=${buf}`, fd);
      if (res.data.error) { setError(res.data.error); setData(null); }
      else { setData(res.data); setLimit(PAGE); }
    } catch (err) { setError("업로드 실패: " + err.message); }
    setLoading(false);
  };
  const onPick = (file) => { setFileObj(file); doUpload(file, buffer); };

  const doCompare = async () => {
    if (!prevFile || !curFile) return;
    setCmpLoading(true); setCmpError(null);
    const fd = new FormData();
    fd.append("prev", prevFile); fd.append("current", curFile);
    try {
      const res = await axios.post(`${API}/api/crd-board/compare`, fd);
      if (res.data.error) { setCmpError(res.data.error); setCmp(null); }
      else { setCmp(res.data); setCmpLimit(PAGE); }
    } catch (err) { setCmpError("비교 실패: " + err.message); }
    setCmpLoading(false);
  };

  // 엑셀은 화면 필터와 무관하게 원본 전체 행 — 요청서 N7 코멘트
  const download = async (url, fd, setBusy, setErr) => {
    setBusy(true); setErr(null);
    try {
      const res = await axios.post(`${API}${url}`, fd, { responseType: "blob" });
      const cd = res.headers["content-disposition"] || "";
      const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
      const name = m ? decodeURIComponent(m[1]) : "backlog.xlsx";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(res.data);
      a.download = name;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) { setErr("다운로드 실패: " + err.message); }
    setBusy(false);
  };

  const downloadNow = () => {
    const fd = new FormData(); fd.append("file", fileObj);
    download("/api/crd-board/export", fd, setDl, setError);
  };
  const downloadCmp = () => {
    const fd = new FormData(); fd.append("prev", prevFile); fd.append("current", curFile);
    download("/api/crd-board/compare/export", fd, setCmpDl, setCmpError);
  };

  const rows = useMemo(() => (data?.board || []).filter(c =>
    (!elapsedOnly || c.elapsed) &&
    (!riskFilter || c.risk === riskFilter) &&
    (!typeFilter || c.order_type === typeFilter) &&
    hit(c, query.f1, query.v1) && hit(c, query.f2, query.v2)
  ), [data, elapsedOnly, riskFilter, typeFilter, query]);

  const cmpRows = useMemo(() => (cmp?.changed || []).filter(c =>
    (!indefOnly || c.indefinite) &&
    hit(c, cmpQuery.f1, cmpQuery.v1) && hit(c, cmpQuery.f2, cmpQuery.v2)
  ), [cmp, indefOnly, cmpQuery]);

  const tab = (key, label) => (
    <button onClick={() => setMode(key)}
            style={{ padding: "8px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer",
                     border: "none", borderBottom: `2px solid ${mode === key ? "#0a0e12" : "transparent"}`,
                     background: "none", color: mode === key ? "#0a0e12" : "#94a3b8" }}>
      {label}
    </button>
  );

  return (
    <div style={{ background: "#f7f7f8", minHeight: "100vh", padding: "24px 28px", boxSizing: "border-box" }}>
      <div style={{ maxWidth: 1720, margin: "0 auto" }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, color: "#0a0e12" }}>영업1실 · CRD 현황판</h1>

        <div style={{ display: "flex", gap: 4, borderBottom: "1px solid #e2e8f0", margin: "14px 0 18px" }}>
          {tab("now", "현황 보기")}
          {tab("change", "변화 보기 (MAD 비교)")}
        </div>

        {/* ───────── 현황 보기 ───────── */}
        {mode === "now" && (<>
          <p style={{ fontSize: 13, color: "#64748b", marginTop: 0 }}>
            Backlog Shipment Report를 올리면 미출하 주문의 <b>경과일수(자재 가용일 MAD − 고객 요청일 CRD)</b>를
            계산해 자재 가용일 빠른 순으로 보여줍니다. 엑셀 다운로드는 화면 필터와 무관하게 <b>원본 전체 행</b>입니다.
          </p>
          <div style={{ margin: "16px 0", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
                   onChange={(e) => onPick(e.target.files[0])} />
            <button onClick={() => fileRef.current?.click()} disabled={loading} style={S.btnDark}>
              {loading ? "분석 중…" : "Backlog 리포트 업로드"}
            </button>
            {fileObj && <span style={{ fontSize: 12, color: "#64748b" }}>{fileObj.name}</span>}
            <span style={{ fontSize: 12.5, color: "#475569" }}>
              위험 기준:{" "}
              <input type="number" min={0} value={buffer} onChange={(e) => setBuffer(Number(e.target.value))}
                     style={{ width: 54, padding: "5px 8px", borderRadius: 6, border: "1px solid #e2e8f0", fontSize: 13 }} />
              일 초과
            </span>
            <button onClick={() => doUpload(fileObj, buffer)} disabled={!fileObj || loading} style={S.btnGhost}>적용</button>
            <button onClick={downloadNow} disabled={!fileObj || dl}
                    style={{ ...S.btnRed, marginLeft: "auto", opacity: fileObj ? 1 : 0.5 }}>
              {dl ? "만드는 중…" : "⬇ Backlog 엑셀 다운로드"}
            </button>
          </div>

          {error && <Banner>{error}</Banner>}

          {data && (<>
            <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
              {ORDER.map(r => (
                <button key={r} onClick={() => setRiskFilter(riskFilter === r ? null : r)}
                        style={{ padding: "8px 14px", borderRadius: 999, fontSize: 13, fontWeight: 600, cursor: "pointer",
                                 background: RISK[r].bg, color: RISK[r].color,
                                 border: `1.5px solid ${riskFilter === r ? RISK[r].color : RISK[r].border}` }}>
                  {RISK[r].dot} {RISK[r].label} {data.summary?.[r] ?? 0}
                </button>
              ))}
              {["OR", "FD"].map(t => (
                <button key={t} onClick={() => setTypeFilter(typeFilter === t ? null : t)}
                        style={{ padding: "8px 12px", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer",
                                 background: typeFilter === t ? "#0a0e12" : "#fff",
                                 color: typeFilter === t ? "#fff" : "#64748b", border: "1px solid #e2e8f0" }}>
                  {TYPE_LABEL[t]}
                </button>
              ))}
              <label style={{ fontSize: 12.5, color: "#475569", display: "inline-flex", alignItems: "center", gap: 6 }}>
                <input type="checkbox" checked={elapsedOnly} onChange={(e) => setElapsedOnly(e.target.checked)} />
                경과된 것만 (경과일수 &gt; 0)
              </label>
              <span style={{ fontSize: 12, color: "#64748b", marginLeft: "auto" }}>
                기준일 {data.today} · 오픈 {data.open_count.toLocaleString()}건 · 경과 {data.elapsed_count.toLocaleString()}건
                {data.shipped_skipped ? ` (출하완료 ${data.shipped_skipped} 제외)` : ""}
              </span>
            </div>

            {data.part_summary?.filter(p => p.red > 0).length > 0 && (
              <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "12px 14px", marginBottom: 14 }}>
                <div style={{ fontSize: 12.5, fontWeight: 700, color: "#0a0e12", marginBottom: 8 }}>🔴 위험 집중 부품 Top</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {data.part_summary.filter(p => p.red > 0).map((p, i) => (
                    <div key={i} style={{ fontSize: 12, background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 6, padding: "5px 9px" }}>
                      <b>{p.did}/{p.mpn}</b> · 위험 {p.red}건 · {Number(p.red_qty).toLocaleString()}ea
                    </div>
                  ))}
                </div>
              </div>
            )}

            <SearchBar onSearch={(q) => { setQuery(q); setLimit(PAGE); }} />

            <Count shown={Math.min(limit, rows.length)} total={rows.length} of={data.open_count} />
            {rows.length === 0
              ? <Empty>조건에 맞는 라인이 없습니다.</Empty>
              : <Table cols={NOW_COLS} rows={rows.slice(0, limit)} />}
            {rows.length > limit && (
              <More onClick={() => setLimit(limit + PAGE)} left={rows.length - limit} />
            )}
          </>)}
        </>)}

        {/* ───────── 변화 보기 ───────── */}
        {mode === "change" && (<>
          <p style={{ fontSize: 13, color: "#64748b", marginTop: 0 }}>
            이전·현재 두 Backlog 리포트를 올리면 <b>현재 백록 기준</b>으로 SO별 MAD 변화를 봅니다.
            <b> GAP = 현재 MAD − 이전 MAD</b> (양수 밀림 / 음수 당겨짐). 이전 백록에 없던 신규 SO는 {NA}.
            엑셀 다운로드는 변화된 라인만이 아니라 <b>전체 백록 라인</b>입니다.
          </p>
          <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", margin: "16px 0" }}>
            <FileBtn label="이전 백록" file={prevFile} inputRef={prevRef} onPick={setPrevFile} />
            <span style={{ color: "#94a3b8" }}>→</span>
            <FileBtn label="현재 백록" file={curFile} inputRef={curRef} onPick={setCurFile} />
            <button onClick={doCompare} disabled={!prevFile || !curFile || cmpLoading}
                    style={{ ...S.btnDark, background: (prevFile && curFile) ? "#0a0e12" : "#cbd5e1" }}>
              {cmpLoading ? "비교 중…" : "비교"}
            </button>
            {cmp && <span style={{ fontSize: 12, color: "#64748b" }}>기준일 {cmp.today}</span>}
            <button onClick={downloadCmp} disabled={!prevFile || !curFile || cmpDl}
                    style={{ ...S.btnRed, marginLeft: "auto", opacity: (prevFile && curFile) ? 1 : 0.5 }}>
              {cmpDl ? "만드는 중…" : "⬇ Backlog 엑셀 다운로드"}
            </button>
          </div>

          {cmpError && <Banner>{cmpError}</Banner>}

          {cmp && (<>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
              <Stat label="◆ 변화" value={cmp.summary.changed} color="#0a0e12" big />
              <Stat label="🔺 밀림" value={cmp.summary.slipped} color="#dc2626" />
              <Stat label="🔻 당겨짐" value={cmp.summary.improved} color="#16a34a" />
              <Stat label="＋ 신규" value={cmp.summary.new} color="#2563eb" />
              <Stat label="⛔ 무기한" value={cmp.summary.indefinite} color="#7c2d12" />
              <Stat label="✔ 소진" value={cmp.summary.gone} color="#64748b" />
              <Stat label="▬ 동일" value={cmp.summary.same} color="#94a3b8" />
            </div>

            <label style={{ fontSize: 12.5, color: "#475569", display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 12 }}>
              <input type="checkbox" checked={indefOnly} onChange={(e) => setIndefOnly(e.target.checked)} />
              무기한 연기된 것만 보기
            </label>

            <SearchBar onSearch={(q) => { setCmpQuery(q); setCmpLimit(PAGE); }} />

            <Count shown={Math.min(cmpLimit, cmpRows.length)} total={cmpRows.length} of={cmp.summary.changed} />
            {cmpRows.length === 0
              ? <Empty>조건에 맞는 변화 라인이 없습니다.</Empty>
              : <Table cols={CHANGE_COLS} rows={cmpRows.slice(0, cmpLimit)} />}
            {cmpRows.length > cmpLimit && (
              <More onClick={() => setCmpLimit(cmpLimit + PAGE)} left={cmpRows.length - cmpLimit} />
            )}
          </>)}
        </>)}
      </div>
    </div>
  );
}

// ── 표 컬럼 정의 (요청서 10행 헤더 순서 그대로) ──
const elapsedCell = (c) => {
  if (c.delay_days == null) return { text: NA, color: "#94a3b8" };
  return { text: Number(c.delay_days).toLocaleString(), color: (RISK[c.risk] || RISK.unknown).color,
           bold: c.delay_days > 0 };
};

const NOW_COLS = [
  { h: "ORDER_TYPE", get: (c) => c.order_type, w: 90, mid: true },
  { h: "SO", get: (c) => c.so, w: 130 },
  { h: "PURCH_ORDER_NO", get: (c) => c.po, w: 140 },
  { h: "CUSTOMER_MATERIAL", get: (c) => c.cust_material, w: 130 },
  { h: "MPN", get: (c) => c.mpn, w: 190 },
  { h: "DID", get: (c) => c.did, w: 70, mid: true },
  { h: "END_CUSTOMER_NAME", get: (c) => c.customer, w: 150 },
  { h: "CRD", get: (c) => c.crd, w: 96, mid: true, cell: (c) => ({ text: c.crd || NA, color: c.overdue ? "#dc2626" : null }) },
  { h: "MAD", get: (c) => c.mad, w: 96, mid: true, cell: (c) => ({ text: c.mad || NA, bold: true }) },
  { h: "경과일수", get: (c) => c.delay_days, w: 82, mid: true, cell: elapsedCell },
  { h: "PLANT", get: (c) => c.plant, w: 72, mid: true },
  { h: "BOX_TYPE", get: (c) => c.box_type, w: 100, mid: true },
  { h: "QTY", get: (c) => fmtQty(c.qty), w: 88, right: true },
  { h: "DELIVERY_NUMBER", get: (c) => c.delivery_number, w: 120, mid: true },
  { h: "FSE", get: (c) => c.fse, w: 76, mid: true },
  { h: "CUST", get: (c) => c.cust, w: 170 },
];

const gapCell = (c) => {
  if (c.gap_days == null) return { text: NA, color: "#94a3b8" };
  const g = c.gap_days;
  return { text: (g > 0 ? "+" : "") + Number(g).toLocaleString(), bold: true,
           color: g > 0 ? "#dc2626" : g < 0 ? "#16a34a" : "#94a3b8" };
};

const CHANGE_COLS = [
  { h: "ORDER_TYPE", get: (c) => c.order_type, w: 90, mid: true },
  { h: "SO", get: (c) => c.so, w: 130,
    cell: (c) => ({ text: (c.is_new ? "＋ " : "") + (c.so || ""), bold: c.is_new,
                    color: c.is_new ? "#2563eb" : null }) },
  { h: "PURCH_ORDER_NO", get: (c) => c.po, w: 140 },
  { h: "CUSTOMER_MATERIAL", get: (c) => c.cust_material, w: 130 },
  { h: "MPN", get: (c) => c.mpn, w: 190 },
  { h: "DID", get: (c) => c.did, w: 70, mid: true },
  { h: "END_CUSTOMER_NAME", get: (c) => c.customer, w: 150 },
  { h: "CRD", get: (c) => c.crd, w: 96, mid: true, cell: (c) => ({ text: c.crd || NA, color: c.overdue ? "#dc2626" : null }) },
  { h: "현재 MAD", get: (c) => c.mad, w: 100, mid: true, cell: (c) => ({ text: c.mad || NA, bold: true }) },
  { h: "이전 MAD", get: (c) => c.prev_mad, w: 100, mid: true,
    cell: (c) => ({ text: c.prev_mad || NA, color: c.prev_mad ? "#475569" : "#94a3b8" }) },
  { h: "GAP", get: (c) => c.gap_days, w: 76, mid: true, cell: gapCell },
  { h: "PLANT", get: (c) => c.plant, w: 72, mid: true },
  { h: "BOX_TYPE", get: (c) => c.box_type, w: 100, mid: true },
  { h: "QTY", get: (c) => fmtQty(c.qty), w: 88, right: true },
  { h: "DELIVERY_NUMBER", get: (c) => c.delivery_number, w: 120, mid: true },
  { h: "FSE", get: (c) => c.fse, w: 76, mid: true },
  { h: "CUST", get: (c) => c.cust, w: 170 },
];

// ── 표현용 컴포넌트 ──
const S = {
  sel: { padding: "7px 10px", borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12.5,
         background: "#fff", color: "#0a0e12" },
  inp: { padding: "7px 12px", borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12.5,
         minWidth: 190, flex: "1 1 190px" },
  btnRed: { padding: "8px 16px", background: "#c43a3a", color: "#fff", border: "none",
            borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: "pointer" },
  btnDark: { padding: "9px 18px", background: "#0a0e12", color: "#fff", border: "none",
             borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" },
  btnGhost: { padding: "7px 12px", background: "#fff", color: "#0a0e12", border: "1px solid #cbd5e1",
              borderRadius: 7, fontSize: 12.5, cursor: "pointer" },
};

const Table = ({ cols, rows }) => (
  <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                overflowX: "auto", maxHeight: "72vh", overflowY: "auto" }}>
    <table style={{ borderCollapse: "separate", borderSpacing: 0, fontSize: 12, width: "100%" }}>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c.h} style={{ position: "sticky", top: 0, zIndex: 1, background: "#0a0e12",
                                   color: "#fff", fontWeight: 600, fontSize: 11, whiteSpace: "nowrap",
                                   padding: "9px 10px", textAlign: "left", minWidth: c.w }}>
              {c.h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} style={{ background: i % 2 ? "#fafafa" : "#fff" }}>
            {cols.map((c) => {
              const spec = c.cell ? c.cell(r) : { text: c.get(r) };
              const text = spec.text == null || spec.text === "" ? "" : spec.text;
              return (
                <td key={c.h} style={{ padding: "7px 10px", borderTop: "1px solid #f1f5f9",
                                       whiteSpace: "nowrap", color: spec.color || "#334155",
                                       fontWeight: spec.bold ? 700 : 400,
                                       textAlign: c.right ? "right" : c.mid ? "center" : "left" }}>
                  {text}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const Count = ({ shown, total, of }) => (
  <div style={{ fontSize: 12, color: "#64748b", marginBottom: 8 }}>
    {shown.toLocaleString()} / {total.toLocaleString()}행 표시
    {of != null && total !== of && ` (전체 ${Number(of).toLocaleString()}행 중 필터)`}
    {" · 엑셀 다운로드는 원본 전체 행"}
  </div>
);
const More = ({ onClick, left }) => (
  <div style={{ textAlign: "center", marginTop: 12 }}>
    <button onClick={onClick} style={S.btnGhost}>
      더 보기 ({Number(left).toLocaleString()}행 남음)
    </button>
  </div>
);
const Banner = ({ children }) => (
  <div style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#dc2626",
                padding: "12px 16px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>{children}</div>
);
const Empty = ({ children }) => (
  <div style={{ padding: 48, textAlign: "center", color: "#94a3b8", fontSize: 14,
                background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10 }}>{children}</div>
);
const Stat = ({ label, value, color, big }) => (
  <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "10px 16px", minWidth: 92 }}>
    <div style={{ fontSize: 11.5, color: "#64748b" }}>{label}</div>
    <div style={{ fontSize: big ? 22 : 18, fontWeight: 700, color }}>{Number(value).toLocaleString()}</div>
  </div>
);
const FileBtn = ({ label, file, inputRef, onPick }) => (
  <div>
    <input ref={inputRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
           onChange={(e) => onPick(e.target.files[0])} />
    <button onClick={() => inputRef.current?.click()} style={S.btnGhost}>
      📄 {label}{file ? " ✓" : ""}
    </button>
    {file && <div style={{ fontSize: 11, color: "#64748b", marginTop: 3, maxWidth: 180, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{file.name}</div>}
  </div>
);

export default CrdBoard;
