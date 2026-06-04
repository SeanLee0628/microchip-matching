import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function PoReportConvert() {
  const [t1Cols, setT1Cols] = useState([]);
  const [t1Rows, setT1Rows] = useState([]);
  const [t1Sum, setT1Sum] = useState(null);
  const [t2Rows, setT2Rows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef();

  const handleFile = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setT1Rows([]);
    setT2Rows([]);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/po-report/preview`, fd);
      if (res.data.error) setError(res.data.error);
      else {
        setT1Cols(res.data.t1_columns || []);
        setT1Rows(res.data.t1_rows || []);
        setT1Sum(res.data.t1_sum || null);
        setT2Rows(res.data.t2_rows || []);
      }
    } catch (e) {
      setError("실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!t1Rows.length) return;
    try {
      const res = await axios.post(`${API_URL}/api/po-report/export`,
        { t1_rows: t1Rows, t1_sum: t1Sum, t2_rows: t2Rows },
        { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `발주요청서_보고_${new Date().toISOString().slice(0, 10)}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
  };

  const fmt = (v) => {
    if (v == null || v === "") return "-";
    if (typeof v === "number") return v.toLocaleString();
    return String(v);
  };

  const t2Cols = ["Date", "Sales", "PART NO.", "Customer", "기 수주", "신규 수주",
    "Inventory", "Backlog", "매출 M", "매출 +1M", "매출 +2M", "매출 +3M", "매출 ~+4M", "Remarks"];

  return (
    <div>
      <div className="page-header">
        <h1>발주요청서 변환</h1>
        <p className="subtitle">데이터 양식 → 보고 양식 (표1 자동, 표2 부분, 표3 수동)</p>
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
        {t1Rows.length > 0 && (
          <button
            onClick={handleExport}
            style={{ padding: "10px 20px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
          >
            📥 보고 양식 다운로드
          </button>
        )}
      </div>

      {t1Rows.length > 0 && (
        <>
          <h3 style={{ marginTop: 0 }}>표1 — 발주 라인 ({t1Rows.length}건)</h3>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6, marginBottom: 24 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#dcfce7" }}>
                  {t1Cols.map(c => (
                    <th key={c} style={{ padding: 8, textAlign: "left", borderBottom: "2px solid #cbd5e1", whiteSpace: "nowrap" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t1Rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    {t1Cols.map(c => (
                      <td key={c} style={{ padding: 8, whiteSpace: "nowrap" }}>{fmt(r[c])}</td>
                    ))}
                  </tr>
                ))}
                {t1Sum && (
                  <tr style={{ background: "#fff2cc", fontWeight: 700 }}>
                    {t1Cols.map(c => {
                      if (c === "Qty") return <td key={c} style={{ padding: 8 }}>{fmt(t1Sum.Qty)}</td>;
                      if (c === "Resale AMT") return <td key={c} style={{ padding: 8 }}>{fmt(t1Sum["Resale AMT"])}</td>;
                      return <td key={c} style={{ padding: 8 }}></td>;
                    })}
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <h3>표2 — MPN별 집계 ({t2Rows.length}건) — <span style={{ color: "#94a3b8", fontWeight: 400, fontSize: 13 }}>신규수주만 자동, 나머지 빈칸은 수동 입력 필요</span></h3>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#fef9c3" }}>
                  {t2Cols.map(c => (
                    <th key={c} style={{ padding: 8, textAlign: "left", borderBottom: "2px solid #cbd5e1", whiteSpace: "nowrap" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t2Rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    {t2Cols.map(c => (
                      <td key={c} style={{ padding: 8, whiteSpace: "nowrap", color: r[c] == null ? "#cbd5e1" : "inherit" }}>
                        {fmt(r[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ marginTop: 16, padding: 12, background: "#f1f5f9", borderRadius: 6, fontSize: 13, color: "#475569" }}>
            💡 다운받은 엑셀에는 표1, 표2와 함께 <b>표3 (월별 수급 시뮬) 빈 시트</b>가 포함됩니다. 표2의 빈칸과 표3은 직접 채워주세요.
          </div>
        </>
      )}
    </div>
  );
}

export default PoReportConvert;
