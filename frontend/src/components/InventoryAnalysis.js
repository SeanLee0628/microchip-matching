import React, { useState, useMemo, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

const COLORS = {
  bg: "#fafbfc",
  card: "#ffffff",
  border: "#e6e8eb",
  borderSoft: "#eef0f2",
  text: "#0f172a",
  textMute: "#64748b",
  textFaint: "#94a3b8",
  accent: "#4f46e5",
  accentSoft: "#eef2ff",
  rose: "#e11d48",
  roseSoft: "#fff1f2",
  amber: "#d97706",
  amberSoft: "#fffbeb",
  slate: "#475569",
  slateSoft: "#f1f5f9",
};

const ABC_STYLE = {
  A: { bg: "#fef2f2", color: "#b91c1c", dot: "#dc2626" },
  B: { bg: "#fffbeb", color: "#b45309", dot: "#d97706" },
  C: { bg: "#f1f5f9", color: "#475569", dot: "#64748b" },
};

function fmtNum(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (!isFinite(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtCompact(n) {
  if (n === null || n === undefined || !isFinite(Number(n))) return "—";
  const v = Number(n);
  const abs = Math.abs(v);
  if (abs >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (abs >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function LineChart({ months, data }) {
  const w = 700, h = 260;
  const pad = { l: 56, r: 16, t: 16, b: 36 };
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const [hover, setHover] = useState(null);
  if (!months || months.length === 0) return <div style={{ color: COLORS.textMute, padding: 40, textAlign: "center" }}>데이터 없음</div>;
  const maxQ = Math.max(1, ...data.map((d) => d.qty));
  const xStep = months.length > 1 ? iw / (months.length - 1) : iw;
  const points = data.map((d, i) => ({
    x: pad.l + i * xStep,
    y: pad.t + ih - (d.qty / maxQ) * ih,
    qty: d.qty,
    ym: d.ym,
  }));
  // smooth path
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const areaPath = `${path} L${points[points.length - 1].x.toFixed(1)},${pad.t + ih} L${points[0].x.toFixed(1)},${pad.t + ih} Z`;
  const ticks = 4;
  const yLabels = Array.from({ length: ticks + 1 }, (_, i) => Math.round((maxQ * (ticks - i)) / ticks));
  const xLabelEvery = Math.max(1, Math.ceil(months.length / 10));

  return (
    <svg width={w} height={h} style={{ display: "block", width: "100%", height: "auto" }}>
      <defs>
        <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={COLORS.accent} stopOpacity="0.18" />
          <stop offset="100%" stopColor={COLORS.accent} stopOpacity="0" />
        </linearGradient>
      </defs>
      {yLabels.map((v, i) => {
        const y = pad.t + (ih * i) / ticks;
        return (
          <g key={i}>
            <line x1={pad.l} x2={pad.l + iw} y1={y} y2={y} stroke={COLORS.borderSoft} strokeDasharray={i === ticks ? "" : "2 3"} />
            <text x={pad.l - 10} y={y + 4} fontSize="10.5" textAnchor="end" fill={COLORS.textFaint} fontFamily="ui-sans-serif, system-ui">
              {fmtCompact(v)}
            </text>
          </g>
        );
      })}
      {months.map((m, i) => {
        if (i % xLabelEvery !== 0 && i !== months.length - 1) return null;
        const x = pad.l + i * xStep;
        return (
          <text key={m} x={x} y={pad.t + ih + 18} fontSize="10.5" textAnchor="middle" fill={COLORS.textFaint} fontFamily="ui-sans-serif, system-ui">
            {m.slice(2)}
          </text>
        );
      })}
      <path d={areaPath} fill="url(#areaGrad)" />
      <path d={path} fill="none" stroke={COLORS.accent} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {points.map((p, i) => (
        <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <circle cx={p.x} cy={p.y} r={hover === i ? 5 : 3} fill="#fff" stroke={COLORS.accent} strokeWidth="2" style={{ transition: "r 120ms" }} />
          <rect x={p.x - 12} y={pad.t} width="24" height={ih} fill="transparent" />
        </g>
      ))}
      {hover !== null && (() => {
        const p = points[hover];
        const tw = 110, th = 44;
        let tx = p.x - tw / 2;
        if (tx < pad.l) tx = pad.l;
        if (tx + tw > pad.l + iw) tx = pad.l + iw - tw;
        const ty = Math.max(pad.t, p.y - th - 10);
        return (
          <g pointerEvents="none">
            <line x1={p.x} x2={p.x} y1={pad.t} y2={pad.t + ih} stroke={COLORS.accent} strokeOpacity="0.25" strokeDasharray="3 3" />
            <rect x={tx} y={ty} width={tw} height={th} rx="6" fill="#0f172a" />
            <text x={tx + tw / 2} y={ty + 16} fontSize="10.5" textAnchor="middle" fill="#cbd5e1">{p.ym}</text>
            <text x={tx + tw / 2} y={ty + 32} fontSize="12" fontWeight="700" textAnchor="middle" fill="#fff">{fmtNum(p.qty)}</text>
          </g>
        );
      })()}
    </svg>
  );
}

function Stat({ label, value, hint, accent }) {
  return (
    <div style={{
      flex: "1 1 0",
      minWidth: 140,
      background: COLORS.card,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 12,
      padding: "16px 18px",
      position: "relative",
      overflow: "hidden",
    }}>
      {accent && <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, background: accent }} />}
      <div style={{ fontSize: 11, color: COLORS.textMute, fontWeight: 600, letterSpacing: 0.3, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: COLORS.text, marginTop: 6, letterSpacing: "-0.02em" }}>{value}</div>
      {hint && <div style={{ fontSize: 11.5, color: COLORS.textFaint, marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

function FilterChip({ active, onClick, children }) {
  return (
    <button onClick={onClick} style={{
      padding: "6px 12px",
      borderRadius: 999,
      border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
      background: active ? COLORS.accentSoft : COLORS.card,
      color: active ? COLORS.accent : COLORS.slate,
      fontSize: 12.5,
      fontWeight: active ? 600 : 500,
      cursor: "pointer",
      transition: "all 120ms",
      whiteSpace: "nowrap",
    }}>{children}</button>
  );
}

function InventoryAnalysis() {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const [sortKey, setSortKey] = useState("recent_total");
  const [sortDir, setSortDir] = useState("desc");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  const onPickFile = (f) => { if (f) { setFile(f); setError(null); } };

  const upload = async () => {
    if (!file) { setError("파일을 선택하세요"); return; }
    setError(null); setData(null); setSelected(null); setLoading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await axios.post(`${API_URL}/api/inventory-analysis`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 600000,
      });
      if (res.data.error) setError(res.data.error);
      else setData(res.data);
    } catch (e) {
      setError("실패: " + (e.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  const [exporting, setExporting] = useState(false);
  const exportExcel = async () => {
    if (!data || !data.items?.length) return;
    setExporting(true);
    try {
      const res = await axios.post(
        `${API_URL}/api/inventory-analysis/export`,
        { items: data.items },
        { responseType: "blob", timeout: 120000 }
      );
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `재고분석_분류별_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError("엑셀 내보내기 실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setExporting(false);
    }
  };

  // 화면에서 필터한 "필요한 것만" 단일 시트로 추출
  const FILTER_LABELS = { all: "전체", stock: "재고보유", history: "판매이력", shortage: "재고부족", abc_a: "A등급", abc_b: "B등급", abc_c: "C등급" };
  const exportFiltered = async () => {
    if (!filteredSorted.length) return;
    setExporting(true);
    try {
      const label = FILTER_LABELS[filter] || "추출";
      const res = await axios.post(
        `${API_URL}/api/inventory-analysis/export`,
        { items: filteredSorted, single: true, label },
        { responseType: "blob", timeout: 120000 }
      );
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `재고분석_${label}_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError("엑셀 내보내기 실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setExporting(false);
    }
  };

  const filteredSorted = useMemo(() => {
    if (!data) return [];
    let arr = data.items.slice();
    if (filter === "stock") arr = arr.filter((x) => x.stock > 0);
    else if (filter === "history") arr = arr.filter((x) => x.recent_total > 0);
    else if (filter === "shortage") arr = arr.filter((x) => x.recommended > 0 && x.stock < x.recommended);
    else if (["abc_a", "abc_b", "abc_c"].includes(filter)) {
      const t = filter.split("_")[1].toUpperCase();
      arr = arr.filter((x) => x.abc === t);
    }
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      arr = arr.filter((x) => (x.pn || "").toUpperCase().includes(q));
    }
    arr.sort((a, b) => {
      const va = a[sortKey], vb = b[sortKey];
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      if (typeof va === "number") return sortDir === "asc" ? va - vb : vb - va;
      return sortDir === "asc" ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });
    return arr;
  }, [data, sortKey, sortDir, filter, search]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  };

  const sortIcon = (key) => {
    if (sortKey !== key) return <span style={{ color: COLORS.textFaint, marginLeft: 4, fontSize: 9 }}>▲▼</span>;
    return <span style={{ color: COLORS.accent, marginLeft: 4, fontSize: 10 }}>{sortDir === "asc" ? "▲" : "▼"}</span>;
  };

  const cols = [
    { k: "pn", label: "P/N", align: "left", w: "auto" },
    { k: "stock", label: "현재고", align: "right", w: 110 },
    { k: "monthly_avg", label: "월평균 판매", align: "right", w: 110 },
    { k: "last_sale", label: "최근 판매일", align: "left", w: 110 },
    { k: "recommended", label: "적정재고", align: "right", w: 110 },
    { k: "abc", label: "ABC", align: "center", w: 64 },
  ];

  return (
    <div style={{ background: COLORS.bg, minHeight: "100vh", width: "100%", padding: "24px 28px", color: COLORS.text, fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto", boxSizing: "border-box" }}>
      {/* Header */}
      <div style={{ marginBottom: 20, width: "100%", maxWidth: 1400, margin: "0 auto 20px" }}>
        <div style={{ fontSize: 12, color: COLORS.textMute, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase" }}>영업4실</div>
        <h1 style={{ fontSize: 26, fontWeight: 700, margin: "4px 0 6px", letterSpacing: "-0.02em" }}>재고 분석</h1>
        <p style={{ fontSize: 13.5, color: COLORS.textMute, margin: 0 }}>
          자재별 현재고 · 월평균 판매 · 적정재고 · ABC 분석 <span style={{ color: COLORS.textFaint }}>· 최근 6개월 기준</span>
        </p>
      </div>

      {/* Upload zone */}
      {!data && (
        <div style={{ width: "100%", maxWidth: 1400, margin: "0 auto" }}>
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault(); setDragOver(false);
              const f = e.dataTransfer.files?.[0];
              if (f) onPickFile(f);
            }}
            style={{
              background: COLORS.card,
              border: `1.5px dashed ${dragOver ? COLORS.accent : COLORS.border}`,
              borderRadius: 14,
              padding: "40px 24px",
              textAlign: "center",
              transition: "all 150ms",
              marginBottom: 16,
            }}>
          <div style={{ width: 44, height: 44, margin: "0 auto 12px", borderRadius: 12, background: COLORS.accentSoft, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={COLORS.accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
            </svg>
          </div>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
            {file ? file.name : "재고 엑셀 파일을 끌어다 놓거나 선택하세요"}
          </div>
          <div style={{ fontSize: 12.5, color: COLORS.textMute, marginBottom: 16 }}>
            <code style={{ background: COLORS.slateSoft, padding: "1px 6px", borderRadius: 4, fontSize: 11.5 }}>May inventory</code>
            {" + "}
            <code style={{ background: COLORS.slateSoft, padding: "1px 6px", borderRadius: 4, fontSize: 11.5 }}>shipping management</code>
            {" 시트 포함 .xlsx"}
          </div>
          <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }} onChange={(e) => onPickFile(e.target.files[0])} />
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            <button onClick={() => fileRef.current?.click()} style={{
              padding: "9px 18px", borderRadius: 8, border: `1px solid ${COLORS.border}`,
              background: COLORS.card, color: COLORS.text, fontSize: 13, fontWeight: 600, cursor: "pointer",
            }}>파일 선택</button>
            <button onClick={upload} disabled={loading || !file} style={{
              padding: "9px 18px", borderRadius: 8, border: 0,
              background: !file ? COLORS.borderSoft : COLORS.accent,
              color: !file ? COLORS.textFaint : "#fff",
              fontSize: 13, fontWeight: 600, cursor: !file ? "not-allowed" : "pointer",
              boxShadow: file ? "0 1px 2px rgba(79,70,229,0.25)" : "none",
            }}>{loading ? "분석 중..." : "분석 시작"}</button>
          </div>
        </div>
        </div>
      )}

      {error && (
        <div style={{ width: "100%", maxWidth: 1400, margin: "0 auto" }}>
          <div style={{ padding: "12px 16px", background: COLORS.roseSoft, border: `1px solid #fecdd3`, borderRadius: 10, color: COLORS.rose, fontSize: 13, marginBottom: 16, whiteSpace: "pre-wrap" }}>
            {error}
          </div>
        </div>
      )}

      {data && (
        <div style={{ width: "100%", maxWidth: 1400, margin: "0 auto" }}>
          {/* KPI Grid */}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
            <Stat label="총 자재" value={fmtNum(data.summary.total_pns)} accent={COLORS.accent} />
            <Stat label="재고 보유" value={fmtNum(data.summary.with_stock)} hint={`${(data.summary.with_stock / data.summary.total_pns * 100).toFixed(0)}%`} />
            <Stat label="6개월 판매" value={fmtNum(data.summary.with_history)} hint={`${(data.summary.with_history / data.summary.total_pns * 100).toFixed(0)}%`} />
            <Stat label="A 등급" value={fmtNum(data.summary.a_count)} accent={ABC_STYLE.A.dot} hint="상위 70%" />
            <Stat label="B 등급" value={fmtNum(data.summary.b_count)} accent={ABC_STYLE.B.dot} hint="다음 20%" />
            <Stat label="C 등급" value={fmtNum(data.summary.c_count)} accent={ABC_STYLE.C.dot} hint="하위 10%" />
          </div>

          {/* Filter bar */}
          <div style={{ background: COLORS.card, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: "12px 14px", marginBottom: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>전체</FilterChip>
              <FilterChip active={filter === "stock"} onClick={() => setFilter("stock")}>재고 보유</FilterChip>
              <FilterChip active={filter === "history"} onClick={() => setFilter("history")}>판매 이력</FilterChip>
              <FilterChip active={filter === "shortage"} onClick={() => setFilter("shortage")}>재고 부족</FilterChip>
              <FilterChip active={filter === "abc_a"} onClick={() => setFilter("abc_a")}>A</FilterChip>
              <FilterChip active={filter === "abc_b"} onClick={() => setFilter("abc_b")}>B</FilterChip>
              <FilterChip active={filter === "abc_c"} onClick={() => setFilter("abc_c")}>C</FilterChip>
            </div>
            <div style={{ flex: 1, minWidth: 180, position: "relative" }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={COLORS.textFaint} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)" }}>
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
              </svg>
              <input
                placeholder="P/N 검색"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  width: "100%", padding: "8px 12px 8px 32px",
                  border: `1px solid ${COLORS.border}`, borderRadius: 8,
                  fontSize: 13, outline: "none", color: COLORS.text, background: "#fff",
                }}
              />
            </div>
            <span style={{ fontSize: 12, color: COLORS.textMute, fontWeight: 500 }}>{filteredSorted.length.toLocaleString()}건</span>
            <button onClick={exportExcel} disabled={exporting || !data?.items?.length} title="ABC 등급·재고부족 분류별 시트로 엑셀 추출" style={{
              padding: "7px 12px", borderRadius: 8, border: 0,
              background: exporting ? COLORS.borderSoft : COLORS.accent,
              color: exporting ? COLORS.textFaint : "#fff",
              fontSize: 12, fontWeight: 600, cursor: exporting ? "default" : "pointer",
              boxShadow: exporting ? "none" : "0 1px 2px rgba(79,70,229,0.25)",
            }}>{exporting ? "내보내는 중…" : "📥 엑셀 내보내기 (분류별)"}</button>
            <button onClick={exportFiltered} disabled={exporting || !filteredSorted.length}
              title="현재 필터·검색으로 거른 행만 단일 시트로 추출" style={{
              padding: "7px 12px", borderRadius: 8, border: `1px solid ${COLORS.accent}`,
              background: COLORS.card, color: COLORS.accent,
              fontSize: 12, fontWeight: 600, cursor: (exporting || !filteredSorted.length) ? "default" : "pointer",
              opacity: (exporting || !filteredSorted.length) ? 0.5 : 1,
            }}>📥 현재 분류만 ({FILTER_LABELS[filter]})</button>
            <button onClick={() => { setData(null); setFile(null); setSelected(null); setSearch(""); setFilter("all"); }} style={{
              padding: "7px 12px", borderRadius: 8, border: `1px solid ${COLORS.border}`,
              background: COLORS.card, color: COLORS.textMute, fontSize: 12, fontWeight: 500, cursor: "pointer",
            }}>새 파일</button>
          </div>

          {/* Main split */}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 740px", gap: 14, alignItems: "start" }}>
            {/* Table */}
            <div style={{ background: COLORS.card, border: `1px solid ${COLORS.border}`, borderRadius: 12, overflow: "hidden" }}>
              <div style={{ maxHeight: 640, overflow: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0, fontSize: 13 }}>
                  <thead>
                    <tr>
                      {cols.map((c) => (
                        <th key={c.k}
                          onClick={() => toggleSort(c.k)}
                          style={{
                            position: "sticky", top: 0, zIndex: 1,
                            padding: "11px 14px", borderBottom: `1px solid ${COLORS.border}`,
                            background: "#fbfbfc",
                            textAlign: c.align, cursor: "pointer", userSelect: "none",
                            fontSize: 11.5, fontWeight: 600, color: COLORS.textMute,
                            textTransform: "uppercase", letterSpacing: 0.4,
                            width: c.w,
                          }}>
                          {c.label}{sortIcon(c.k)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSorted.map((it, i) => {
                      const sel = selected && selected.pn === it.pn;
                      const shortage = it.recommended > 0 && it.stock < it.recommended;
                      const abc = ABC_STYLE[it.abc] || ABC_STYLE.C;
                      return (
                        <tr key={i}
                          onClick={() => setSelected(it)}
                          style={{
                            background: sel ? COLORS.accentSoft : "transparent",
                            cursor: "pointer",
                            transition: "background 100ms",
                          }}
                          onMouseEnter={(e) => { if (!sel) e.currentTarget.style.background = "#f8fafc"; }}
                          onMouseLeave={(e) => { if (!sel) e.currentTarget.style.background = "transparent"; }}>
                          <td style={{ padding: "10px 14px", borderBottom: `1px solid ${COLORS.borderSoft}`, fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace", fontSize: 12.5, color: COLORS.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 280 }}>
                            {it.pn}
                          </td>
                          <td style={{ padding: "10px 14px", borderBottom: `1px solid ${COLORS.borderSoft}`, textAlign: "right", color: it.stock > 0 ? COLORS.text : COLORS.textFaint, fontVariantNumeric: "tabular-nums" }}>
                            {fmtNum(it.stock)}
                          </td>
                          <td style={{ padding: "10px 14px", borderBottom: `1px solid ${COLORS.borderSoft}`, textAlign: "right", color: COLORS.slate, fontVariantNumeric: "tabular-nums" }}>
                            {fmtNum(it.monthly_avg)}
                          </td>
                          <td style={{ padding: "10px 14px", borderBottom: `1px solid ${COLORS.borderSoft}`, color: COLORS.slate, fontSize: 12.5 }}>
                            {it.last_sale || "—"}
                          </td>
                          <td style={{ padding: "10px 14px", borderBottom: `1px solid ${COLORS.borderSoft}`, textAlign: "right", color: shortage ? COLORS.rose : COLORS.slate, fontWeight: shortage ? 600 : 400, fontVariantNumeric: "tabular-nums" }}>
                            {fmtNum(it.recommended)}
                            {shortage && <span style={{ marginLeft: 4, fontSize: 9, color: COLORS.rose }}>●</span>}
                          </td>
                          <td style={{ padding: "10px 14px", borderBottom: `1px solid ${COLORS.borderSoft}`, textAlign: "center" }}>
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: 4,
                              padding: "2px 8px", borderRadius: 6,
                              background: abc.bg, color: abc.color,
                              fontSize: 11, fontWeight: 700, letterSpacing: 0.4,
                            }}>{it.abc}</span>
                          </td>
                        </tr>
                      );
                    })}
                    {filteredSorted.length === 0 && (
                      <tr><td colSpan={cols.length} style={{ padding: 60, textAlign: "center", color: COLORS.textFaint }}>조건에 맞는 자재가 없습니다</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Detail panel */}
            <div style={{ position: "sticky", top: 16 }}>
              <div style={{ background: COLORS.card, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: 20, minHeight: 420 }}>
                {selected ? (
                  <>
                    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 11, color: COLORS.textMute, fontWeight: 600, letterSpacing: 0.4, textTransform: "uppercase" }}>P/N</div>
                        <div style={{ fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace", fontSize: 16, fontWeight: 700, marginTop: 3, color: COLORS.text, wordBreak: "break-all" }}>{selected.pn}</div>
                      </div>
                      <span style={{
                        padding: "4px 10px", borderRadius: 6,
                        background: ABC_STYLE[selected.abc].bg, color: ABC_STYLE[selected.abc].color,
                        fontSize: 12, fontWeight: 700, letterSpacing: 0.4,
                      }}>{selected.abc} 등급</span>
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 18 }}>
                      {[
                        { label: "현재고", value: fmtNum(selected.stock) },
                        { label: "월평균", value: fmtNum(selected.monthly_avg) },
                        { label: "적정재고", value: fmtNum(selected.recommended), warn: selected.recommended > 0 && selected.stock < selected.recommended },
                        { label: "최근 판매", value: selected.last_sale || "—", small: true },
                      ].map((s, i) => (
                        <div key={i} style={{
                          padding: "10px 12px",
                          background: s.warn ? COLORS.roseSoft : "#fafbfc",
                          border: `1px solid ${s.warn ? "#fecdd3" : COLORS.borderSoft}`,
                          borderRadius: 10,
                        }}>
                          <div style={{ fontSize: 10.5, color: COLORS.textMute, fontWeight: 600, letterSpacing: 0.3, textTransform: "uppercase" }}>{s.label}</div>
                          <div style={{ fontSize: s.small ? 13 : 16, fontWeight: 700, marginTop: 4, color: s.warn ? COLORS.rose : COLORS.text, fontVariantNumeric: "tabular-nums" }}>{s.value}</div>
                        </div>
                      ))}
                    </div>

                    <div style={{ borderTop: `1px solid ${COLORS.borderSoft}`, paddingTop: 14 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                        <div style={{ fontSize: 12.5, fontWeight: 600, color: COLORS.text }}>월별 판매량 추이</div>
                        <div style={{ fontSize: 11, color: COLORS.textFaint }}>{data.months[0]} ~ {data.months[data.months.length - 1]}</div>
                      </div>
                      <LineChart months={data.months} data={selected.monthly} />
                    </div>
                  </>
                ) : (
                  <div style={{ textAlign: "center", padding: "80px 20px" }}>
                    <div style={{ width: 48, height: 48, margin: "0 auto 14px", borderRadius: 12, background: COLORS.slateSoft, display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={COLORS.textFaint} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>
                      </svg>
                    </div>
                    <div style={{ fontSize: 14, color: COLORS.text, fontWeight: 600, marginBottom: 4 }}>자재를 선택하세요</div>
                    <div style={{ fontSize: 12.5, color: COLORS.textMute }}>좌측 표에서 행을 클릭하면<br/>월별 판매 추이가 표시됩니다</div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default InventoryAnalysis;
