import React, { useState, useMemo } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API_URL = process.env.REACT_APP_API_URL || "";

const NUMBER_COLS = new Set([
  "QTY", "DCPL($)", "매입금액($)", "SP($)", "매출금액($)", "매출환율",
  "SP(KRW)", "매출금액(KRW)", "GP($)", "GP%($)", "GP(KRW)", "GP%(KRW)",
]);

function formatVal(val, col) {
  if (val === null || val === undefined || val === "") return "";
  if (NUMBER_COLS.has(col)) {
    const num = Number(val);
    if (isNaN(num)) return val;
    if (num === 0) return "-";
    if (col.includes("%")) return num.toFixed(2) + "%";
    return num.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return val;
}

function SalesPerformance() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const fileRef = React.useRef();
  const [dragging, setDragging] = useState(false);

  const handleFile = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/sales/upload`, formData);
      if (res.data.error) {
        setError(res.data.error);
      } else {
        setData(res.data);
      }
    } catch (err) {
      setError("업로드 실패: " + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!displayRows.length) return;
    const exportData = displayRows.map((row) => {
      const obj = {};
      columns.forEach((col) => { obj[col] = row[col] ?? ""; });
      return obj;
    });
    const res = await axios.post(`${API_URL}/api/export`,
      { data: exportData, columns },
      { responseType: "blob" }
    );
    saveAs(res.data, `영업실적_${new Date().toISOString().slice(0, 10)}.xlsx`);
  };

  const columns = data?.columns || [];
  const allRows = data?.data || [];

  const filtered = useMemo(() => {
    if (!search.trim()) return allRows;
    const q = search.toLowerCase();
    return allRows.filter((row) =>
      columns.some((col) => {
        const v = row[col];
        return v !== null && v !== undefined && String(v).toLowerCase().includes(q);
      })
    );
  }, [allRows, search, columns]);

  const displayRows = filtered;
  const summary = data?.summary;

  return (
    <>
      <div className="page-header">
        <h1>영업실적</h1>
        <p className="subtitle">더존 출고현황 → 매출현황 자동 변환 (환율: $1 = ₩1,400 고정)</p>
      </div>

      <div
        className={`upload-area ${dragging ? "dragging" : ""}`}
        onClick={() => fileRef.current.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); handleFile(e.dataTransfer.files[0]); }}
      >
        <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files[0])} />
        {loading ? (
          <div className="upload-loading">처리 중...</div>
        ) : (
          <>
            <div className="upload-icon">&#128196;</div>
            <div className="upload-text">더존 출고현황 엑셀 업로드</div>
            <div className="upload-hint">.xls / .xlsx 파일 지원</div>
          </>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}

      {summary && (
        <div className="sales-summary">
          <div className="summary-card">
            <div className="summary-label">매출($)</div>
            <div className="summary-value">${summary.total_sales_usd.toLocaleString()}</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">매입($)</div>
            <div className="summary-value">${summary.total_buy_usd.toLocaleString()}</div>
          </div>
          <div className="summary-card card-gp">
            <div className="summary-label">GP($)</div>
            <div className="summary-value">${summary.total_gp_usd.toLocaleString()} ({summary.total_gp_pct}%)</div>
          </div>
          <div className="summary-card">
            <div className="summary-label">매출(KRW)</div>
            <div className="summary-value">₩{summary.total_sales_krw.toLocaleString()}</div>
          </div>
          <div className="summary-card card-gp">
            <div className="summary-label">GP(KRW)</div>
            <div className="summary-value">₩{summary.total_gp_krw.toLocaleString()} ({summary.total_gp_pct_krw}%)</div>
          </div>
        </div>
      )}

      {data && (
        <>
          <div className="table-header">
            <div className="table-info">
              총 <strong>{displayRows.length}</strong>건
            </div>
            <button className="export-btn" onClick={handleExport}>
              <span>&#128229;</span> 엑셀 내보내기
            </button>
          </div>

          <div className="search-bar">
            <input
              className="search-input"
              type="text"
              placeholder="검색 (품명, 고객명, 담당자 등)"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  {columns.map((col) => <th key={col}>{col}</th>)}
                </tr>
              </thead>
              <tbody>
                {displayRows.map((row, i) => (
                  <tr key={i}>
                    <td style={{ color: "#999", textAlign: "center" }}>{i + 1}</td>
                    {columns.map((col) => (
                      <td key={col} className={NUMBER_COLS.has(col) ? "cell-number" : ""}>
                        {formatVal(row[col], col)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

export default SalesPerformance;
