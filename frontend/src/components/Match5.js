import React, { useState, useRef, useMemo } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

const NUMERIC = new Set([
  "Q'ty", "Lead Time", "Demand Total", "Balance",
  "2023년", "2024년", "2025년", "2026년", "BLOG TTL",
  "6월", "7월", "8월", "9월", "10월", "11월", "12월", "1월", "2월", "3월",
]);

function fmt(v, col) {
  if (v == null || v === "") return "-";
  if (NUMERIC.has(col) && typeof v === "number") return v.toLocaleString();
  return v;
}

function Match5() {
  const [files, setFiles] = useState({ inventory: null, fcst: null, blog: null, shipment: null });
  const [password, setPassword] = useState("9178");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [query, setQuery] = useState("");
  const refs = { inventory: useRef(), fcst: useRef(), blog: useRef(), shipment: useRef() };

  const SLOTS = [
    { key: "inventory", label: "재고 (daily inventory)", color: "#0ea5e9" },
    { key: "fcst", label: "FCST (Sales Revenue)", color: "#8b5cf6" },
    { key: "blog", label: "백록 (Blog)", color: "#f59e0b" },
    { key: "shipment", label: "출고내역", color: "#10b981" },
  ];

  const setFile = (key, f) => { setFiles((p) => ({ ...p, [key]: f })); setResult(null); };

  const reset = () => {
    setFiles({ inventory: null, fcst: null, blog: null, shipment: null });
    setError(null); setResult(null); setQuery("");
    Object.values(refs).forEach((r) => { if (r.current) r.current.value = ""; });
  };

  const allReady = files.inventory && files.fcst && files.blog && files.shipment;

  const handleRun = async () => {
    if (!allReady) { setError("4개 파일을 모두 선택하세요."); return; }
    setLoading(true); setError(null); setResult(null);
    try {
      const fd = new FormData();
      fd.append("inventory", files.inventory);
      fd.append("fcst", files.fcst);
      fd.append("blog", files.blog);
      fd.append("shipment", files.shipment);
      fd.append("password", password || "9178");
      const res = await axios.post(`${API_URL}/api/match5/upload`, fd);
      if (res.data.error) { setError(res.data.error); return; }
      setResult(res.data);
    } catch (e) {
      setError(`요청 실패: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!result) return;
    setExporting(true); setError(null);
    try {
      const res = await axios.post(
        `${API_URL}/api/match5/export`,
        { columns: result.columns, data: result.data },
        { responseType: "blob" }
      );
      const blob = new Blob([res.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      let fname = "영업5실_매칭.xlsx";
      const cd = res.headers["content-disposition"];
      if (cd) {
        const mt = cd.match(/filename\*=UTF-8''([^;]+)/i);
        if (mt) { try { fname = decodeURIComponent(mt[1]); } catch {} }
      }
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = fname;
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError(`내보내기 실패: ${e.response?.data?.detail || e.message}`);
    } finally {
      setExporting(false);
    }
  };

  const dashCols = result?.dashboard_columns || [];
  const filtered = useMemo(() => {
    if (!result) return [];
    const q = query.trim().toLowerCase();
    if (!q) return result.data;
    return result.data.filter((r) =>
      dashCols.some((c) => { const v = r[c]; return v != null && String(v).toLowerCase().includes(q); })
    );
  }, [result, query, dashCols]);

  return (
    <div>
      <div className="page-header">
        <h1>영업5실 매칭 (Uniquant)</h1>
        <p className="subtitle">
          재고·FCST·백록·출고내역 4개 파일을 <b>MIX#(더존코드+PART#)</b> 기준으로 매칭 · 월별 BLOG PDD는 엑셀에만 출력
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "flex", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        {SLOTS.map((s) => {
          const f = files[s.key];
          return (
            <div key={s.key}
              style={{ flex: "1 1 200px", border: `2px dashed ${f ? s.color : "#cbd5e1"}`,
                background: f ? "#f8fafc" : "white", borderRadius: 8, padding: 16,
                textAlign: "center", cursor: "pointer" }}
              onClick={() => refs[s.key].current && refs[s.key].current.click()}>
              <input ref={refs[s.key]} type="file" accept=".xlsx,.xlsm,.xls" style={{ display: "none" }}
                onChange={(e) => setFile(s.key, e.target.files[0] || null)} />
              <div style={{ fontSize: 12, fontWeight: 700, color: s.color, marginBottom: 6 }}>{s.label}</div>
              {f ? (
                <div style={{ fontSize: 11, color: "#0f172a", wordBreak: "break-all" }}>📄 {f.name}</div>
              ) : (
                <div style={{ fontSize: 11, color: "#94a3b8" }}>클릭하여 파일 선택</div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 18, alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontSize: 12, color: "#475569" }}>재고 비밀번호&nbsp;
          <input type="text" value={password} onChange={(e) => setPassword(e.target.value)}
            style={{ padding: "6px 8px", fontSize: 12, border: "1px solid #cbd5e1", borderRadius: 6, width: 90 }} />
        </label>
        <button onClick={handleRun} disabled={loading || !allReady}
          style={{ padding: "11px 24px", background: loading || !allReady ? "#94a3b8" : "#3b82f6",
            color: "white", border: 0, borderRadius: 6, fontWeight: 700, fontSize: 14,
            cursor: loading || !allReady ? "not-allowed" : "pointer" }}>
          {loading ? "매칭 중..." : "🔗 매칭 실행"}
        </button>
        <button onClick={handleExport} disabled={exporting || !result}
          style={{ padding: "11px 22px", background: exporting || !result ? "#94a3b8" : "#10b981",
            color: "white", border: 0, borderRadius: 6, fontWeight: 700, fontSize: 14,
            cursor: exporting || !result ? "not-allowed" : "pointer" }}>
          {exporting ? "내보내는 중..." : "📥 엑셀 내려보내기"}
        </button>
        <button onClick={reset} disabled={loading || exporting}
          style={{ padding: "11px 18px", background: "white", color: "#475569",
            border: "1px solid #cbd5e1", borderRadius: 6, fontSize: 13 }}>초기화</button>
      </div>

      {result?.warnings?.length > 0 && (
        <div style={{ background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8,
          padding: 12, fontSize: 12, color: "#78350f", marginBottom: 12 }}>
          {result.warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
        </div>
      )}

      {result && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center" }}>
            <input type="text" placeholder="🔎 믹스#/품번/고객 검색" value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ marginLeft: "auto", padding: "6px 10px", fontSize: 12,
                border: "1px solid #cbd5e1", borderRadius: 6, width: 240 }} />
            <span style={{ fontSize: 12, color: "#64748b" }}>{filtered.length} / {result.total_rows} 행</span>
          </div>
          <div style={{ overflow: "auto", border: "1px solid #e2e8f0", borderRadius: 8, background: "white", maxHeight: 620 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, whiteSpace: "nowrap" }}>
              <thead style={{ position: "sticky", top: 0, zIndex: 1 }}>
                <tr style={{ background: "#1f3a8a", color: "white" }}>
                  {dashCols.map((c) => (
                    <th key={c} style={{ padding: "8px 10px", fontSize: 11, fontWeight: 700,
                      textAlign: NUMERIC.has(c) ? "right" : "left" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    {dashCols.map((c) => (
                      <td key={c} style={{ padding: "5px 10px",
                        textAlign: NUMERIC.has(c) ? "right" : "left",
                        fontVariantNumeric: NUMERIC.has(c) ? "tabular-nums" : "normal" }}>
                        {fmt(r[c], c)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!result && (
        <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: 14, fontSize: 12, color: "#475569", lineHeight: 1.7 }}>
          <b>업로드 파일</b><br />
          · 재고: <code>Jun inventory</code> 시트 (암호화 — 기본 비번 9178) → PART#별 available Q'ty<br />
          · FCST: <code>Sales Revenue</code> 시트 → Demand Total<br />
          · 백록: 첫 시트 → BLOG TTL·Lead Time·Cancel Window·월별 PDD<br />
          · 출고내역: 첫 시트 → 담당자·고객·품번·2026 출하<br />
          <b>※ 6~3월(BLOG PDD)·추이·25-26(w/BL)은 대시보드에 숨김 → 엑셀 내려보내기에만 출력.</b>
        </div>
      )}
    </div>
  );
}

export default Match5;
