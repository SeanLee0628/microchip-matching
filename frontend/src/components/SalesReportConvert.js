import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";
const AUTH_KEY = "sales_report_auth";
const PASSWORD = "micronauto";

function SalesReportConvert() {
  const [authed, setAuthed] = useState(() => sessionStorage.getItem(AUTH_KEY) === "1");
  const [pwInput, setPwInput] = useState("");
  const [pwError, setPwError] = useState("");
  const [columns, setColumns] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef();

  const handleUnlock = (e) => {
    e.preventDefault();
    if (pwInput === PASSWORD) {
      sessionStorage.setItem(AUTH_KEY, "1");
      setAuthed(true);
      setPwInput("");
      setPwError("");
    } else {
      setPwError("비밀번호가 틀렸습니다");
      setPwInput("");
    }
  };

  if (!authed) {
    return (
      <div>
        <div className="page-header">
          <h1>영업실적 변환</h1>
          <p className="subtitle">🔒 접근하려면 비밀번호를 입력하세요</p>
        </div>
        <form onSubmit={handleUnlock} style={{ maxWidth: 360, marginTop: 20 }}>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="비밀번호"
            autoFocus
            style={{ width: "100%", padding: 12, fontSize: 14, border: "1px solid #cbd5e1", borderRadius: 6, marginBottom: 12, boxSizing: "border-box" }}
          />
          {pwError && <div style={{ color: "#dc2626", fontSize: 13, marginBottom: 12 }}>{pwError}</div>}
          <button
            type="submit"
            style={{ padding: "10px 20px", background: "#3b82f6", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
          >
            잠금 해제
          </button>
        </form>
      </div>
    );
  }

  const handleFile = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setRows([]);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/sales-report/preview`, fd);
      if (res.data.error) setError(res.data.error);
      else {
        setColumns(res.data.columns || []);
        setRows(res.data.rows || []);
      }
    } catch (e) {
      setError("실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!rows.length) return;
    try {
      const res = await axios.post(`${API_URL}/api/sales-report/export`,
        { rows }, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `영업실적_보고_${new Date().toISOString().slice(0, 10)}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
  };

  const PCT_COLS = new Set(["GP%($)", "GP%(KRW)"]);

  const fmt = (v, col) => {
    if (v == null) return "-";
    if (PCT_COLS.has(col) && typeof v === "number") {
      return Math.round(v * 100) + "%";
    }
    if (typeof v === "number") return v.toLocaleString();
    return String(v);
  };

  return (
    <div>
      <div className="page-header">
        <h1>영업실적 변환</h1>
        <p className="subtitle">데이터 양식(68열) → 보고 양식(14열) 자동 변환</p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ marginBottom: 20, display: "flex", gap: 12, alignItems: "center" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files[0])}
        />
        <button
          onClick={() => fileRef.current.click()}
          disabled={loading}
          style={{ padding: "12px 24px", background: "#3b82f6", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
        >
          {loading ? "변환 중..." : "📥 데이터 양식 엑셀 업로드"}
        </button>
        {rows.length > 0 && (
          <button
            onClick={handleExport}
            style={{ padding: "10px 20px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
          >
            📥 보고 양식 다운로드
          </button>
        )}
        <span style={{ fontSize: 12, color: "#64748b" }}>
          시트명: <code>DATA</code>
        </span>
      </div>

      {rows.length > 0 && (
        <>
          <div style={{ marginBottom: 8, fontSize: 14, color: "#334155" }}>
            <b>{rows.length}</b>건 변환 완료
          </div>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f1f5f9" }}>
                  {columns.map(c => (
                    <th key={c} style={{ padding: 8, textAlign: "left", borderBottom: "2px solid #cbd5e1", whiteSpace: "nowrap" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    {columns.map(c => (
                      <td key={c} style={{ padding: 8, whiteSpace: "nowrap" }}>{fmt(r[c], c)}</td>
                    ))}
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

export default SalesReportConvert;
