import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

const VENDORS = [
  { key: "DELTA", label: "DELTA", color: "#10b981" },
  { key: "RAMXEED", label: "RAMXEED (FUJITSU)", color: "#f59e0b" },
  { key: "KEC", label: "KEC", color: "#3b82f6" },
];

function DropZone({ vendor, files, onAdd, onRemove }) {
  const inputRef = useRef();
  const [isOver, setIsOver] = useState(false);

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsOver(false);
    onAdd(e.dataTransfer.files);
  };

  return (
    <div
      onDrop={handleDrop}
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsOver(true); }}
      onDragEnter={(e) => { e.preventDefault(); setIsOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setIsOver(false); }}
      onClick={() => inputRef.current.click()}
      style={{
        border: `2px dashed ${isOver ? vendor.color : "#cbd5e1"}`,
        borderRadius: 8,
        padding: 16,
        background: isOver ? `${vendor.color}15` : "#fafbfc",
        minHeight: 160,
        cursor: "pointer",
        transition: "all 0.15s",
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls"
        multiple
        style={{ display: "none" }}
        onChange={(e) => { onAdd(e.target.files); e.target.value = ""; }}
      />
      <div style={{ textAlign: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, color: vendor.color, fontSize: 16 }}>{vendor.label}</div>
        <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
          파일을 여기로 드래그 또는 클릭해서 선택
        </div>
      </div>
      {files.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
          {files.map((f, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "5px 8px",
                background: "white",
                border: "1px solid #e2e8f0",
                borderRadius: 4,
                fontSize: 12,
              }}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                📄 {f.name}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onRemove(i); }}
                style={{ background: "transparent", border: 0, color: "#dc2626", cursor: "pointer", fontSize: 16, padding: "0 4px", lineHeight: 1 }}
                title="제거"
              >×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SalesSummary() {
  const [filesByVendor, setFilesByVendor] = useState({ KEC: [], DELTA: [], RAMXEED: [] });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const addFiles = (vendorKey, fileList) => {
    if (!fileList || !fileList.length) return;
    const arr = Array.from(fileList);
    setFilesByVendor(prev => ({ ...prev, [vendorKey]: [...prev[vendorKey], ...arr] }));
  };

  const removeFile = (vendorKey, idx) => {
    setFilesByVendor(prev => ({
      ...prev,
      [vendorKey]: prev[vendorKey].filter((_, i) => i !== idx),
    }));
  };

  const totalFiles = filesByVendor.KEC.length + filesByVendor.DELTA.length + filesByVendor.RAMXEED.length;

  const handleAggregate = async () => {
    const allFiles = [...filesByVendor.KEC, ...filesByVendor.DELTA, ...filesByVendor.RAMXEED];
    if (!allFiles.length) {
      setError("업로드할 파일이 없습니다.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    const fd = new FormData();
    allFiles.forEach(f => fd.append("files", f));
    try {
      const res = await axios.post(`${API_URL}/api/sales-summary/aggregate`, fd);
      if (res.data.error) setError(res.data.error);
      else setResult(res.data);
    } catch (e) {
      setError("실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!result) return;
    try {
      const res = await axios.post(`${API_URL}/api/sales-summary/export`, result, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `매출현황_영업4실_${new Date().toISOString().slice(0, 10)}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
  };

  const handleClearAll = () => {
    setFilesByVendor({ KEC: [], DELTA: [], RAMXEED: [] });
    setResult(null);
    setError(null);
  };

  // 수입환율 인라인 수정 → GP(KRW), GP%(KRW) 재계산
  const handleRateEdit = (idx, value) => {
    setResult(prev => {
      if (!prev) return prev;
      const rows = prev.rows.slice();
      const row = { ...rows[idx] };
      const newRate = value === "" ? null : Number(value);
      row["수입환율"] = newRate;
      const amount = Number(row["AMOUNT"]) || 0;
      const salesKrw = Number(row["Sales Amt(KRW)"]) || 0;
      if (newRate != null && !isNaN(newRate)) {
        const buyKrw = amount * newRate;
        const gpKrw = salesKrw - buyKrw;
        row["GP(KRW)"] = Math.round(gpKrw);
        row["GP%(KRW)"] = salesKrw ? gpKrw / salesKrw : 0;
      }
      rows[idx] = row;
      return { ...prev, rows };
    });
  };

  const fmt = (v) => {
    if (v == null || v === "") return "-";
    if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return String(v);
  };
  const fmt2 = (v) => {
    if (v == null || v === "") return "-";
    if (typeof v === "number") return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return String(v);
  };
  const fmt3 = (v) => {
    if (v == null || v === "") return "-";
    if (typeof v === "number") return v.toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
    return String(v);
  };
  const fmtPct = (v) => v == null ? "-" : `${(v * 100).toFixed(2)}%`;

  const sumCols = ["Vendor", "Sales Total ($)", "GP Total ($)", "Rate ($) (%)",
                   "Sales Total (KRW)", "GP Total (KRW)", "Rate (KRW) (%)", "건수"];
  const dataCols = ["Vendor", "PN", "QTY", "U/P", "AMOUNT",
                    "수입신고일", "수입환율", "FSE", "한글업체명",
                    "SP ($)", "Sales Amt ($)", "매출환율", "Sales Amt(KRW)", "GP($)", "GP(KRW)"];

  const pivotRows = (() => {
    if (!result?.rows?.length) return [];
    const groups = new Map();
    const order = [];
    for (const r of result.rows) {
      const v = r.Vendor || "Others";
      const cust = r["한글업체명"] || "(미지정)";
      if (!groups.has(v)) { groups.set(v, new Map()); order.push(v); }
      const g = groups.get(v);
      if (!g.has(cust)) g.set(cust, { sales_usd: 0, gp_usd: 0, sales_krw: 0, gp_krw: 0 });
      const c = g.get(cust);
      c.sales_usd += +r["Sales Amt ($)"] || 0;
      c.gp_usd += +r["GP($)"] || 0;
      c.sales_krw += +r["Sales Amt(KRW)"] || 0;
      c.gp_krw += +r["GP(KRW)"] || 0;
    }
    const out = [];
    const grand = { sales_usd: 0, gp_usd: 0, sales_krw: 0, gp_krw: 0 };
    for (const v of order) {
      const custs = groups.get(v);
      const vt = { sales_usd: 0, gp_usd: 0, sales_krw: 0, gp_krw: 0 };
      for (const c of custs.values()) {
        for (const k of Object.keys(vt)) { vt[k] += c[k]; grand[k] += c[k]; }
      }
      out.push({ kind: "vendor", label: v, ...vt });
      for (const [cust, c] of custs.entries()) {
        out.push({ kind: "cust", label: cust, ...c });
      }
    }
    out.push({ kind: "grand", label: "총합계", ...grand });
    return out;
  })();

  return (
    <div>
      <div className="page-header">
        <h1>주간 영업실적 취합 (4실)</h1>
        <p className="subtitle">벤더별 칸에 파일을 드래그앤드롭 → 자동 취합</p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginBottom: 16 }}>
        {VENDORS.map(v => (
          <DropZone
            key={v.key}
            vendor={v}
            files={filesByVendor[v.key]}
            onAdd={(fl) => addFiles(v.key, fl)}
            onRemove={(idx) => removeFile(v.key, idx)}
          />
        ))}
      </div>

      <div style={{ marginBottom: 20, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button
          onClick={handleAggregate}
          disabled={loading || totalFiles === 0}
          style={{
            padding: "12px 24px",
            background: totalFiles === 0 ? "#94a3b8" : "#3b82f6",
            color: "white", border: 0, borderRadius: 6,
            cursor: totalFiles === 0 ? "not-allowed" : "pointer",
            fontWeight: 600,
          }}
        >
          {loading ? "취합 중..." : `🔄 취합 실행 (${totalFiles}개 파일)`}
        </button>
        {totalFiles > 0 && (
          <button
            onClick={handleClearAll}
            disabled={loading}
            style={{ padding: "10px 16px", background: "white", color: "#64748b", border: "1px solid #cbd5e1", borderRadius: 6, cursor: "pointer", fontSize: 13 }}
          >
            전체 비우기
          </button>
        )}
        {result && (
          <button
            onClick={handleExport}
            style={{ padding: "10px 20px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
          >
            📥 영업실적 엑셀 다운로드
          </button>
        )}
      </div>

      {result?.files?.length > 0 && (
        <div style={{ marginBottom: 16, padding: 12, background: "#f1f5f9", borderRadius: 6, fontSize: 13 }}>
          <b>처리 결과:</b>
          <ul style={{ margin: "6px 0 0 20px" }}>
            {result.files.map((f, i) => (
              <li key={i}>
                <span style={{ color: f.vendor === "ERROR" || f.vendor === "UNKNOWN" ? "#dc2626" : "#059669", fontWeight: 600 }}>
                  [{f.vendor}]
                </span>
                {" "}{f.name} — {f.rows}건 {f.error && <span style={{ color: "#dc2626" }}>({f.error})</span>}
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 6 }}>
            기간: <b>{result.date_min || "-"} ~ {result.date_max || "-"}</b> · 총 <b>{result.total_rows}</b>건
          </div>
        </div>
      )}

      {result?.rows?.length > 0 && (
        <>
          <h3>📋 라인별 상세 ({result.rows.length}건)</h3>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6, maxHeight: 500 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ background: "#f1f5f9", position: "sticky", top: 0, zIndex: 1 }}>
                  {dataCols.map(c => (
                    <th key={c} style={{ padding: 6, textAlign: "left", borderBottom: "2px solid #cbd5e1", whiteSpace: "nowrap" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    {dataCols.map(c => {
                      const v = r[c];
                      if (c === "수입환율") {
                        return (
                          <td key={c} style={{ padding: 2, whiteSpace: "nowrap" }}>
                            <input
                              type="number"
                              step="0.01"
                              value={v ?? ""}
                              onChange={(e) => handleRateEdit(i, e.target.value)}
                              style={{ width: 80, padding: "3px 5px", border: "1px solid #cbd5e1", borderRadius: 3, fontSize: 11, textAlign: "right" }}
                            />
                          </td>
                        );
                      }
                      let txt;
                      if (c === "U/P") txt = fmt3(v);
                      else if (c === "AMOUNT" || c === "Sales Amt ($)" || c === "GP($)") txt = fmt2(v);
                      else txt = fmt(v);
                      return <td key={c} style={{ padding: 5, whiteSpace: "nowrap" }}>{txt}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export default SalesSummary;
