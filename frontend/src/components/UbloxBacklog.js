import React, { useState, useEffect, useMemo } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API_URL = process.env.REACT_APP_API_URL || "";

const NUMBER_COLS = new Set([
  "DELINQ", "Qty Ordered", "Qty Invoiced", "Price per unit", "Total Value",
]);

function formatVal(val, col) {
  if (val === null || val === undefined || val === "" || val === "None" || val === "nan") return "";
  if (NUMBER_COLS.has(col)) {
    const num = Number(val);
    if (isNaN(num)) return val;
    if (num === 0) return "-";
    return num.toLocaleString();
  }
  return val;
}

function UbloxBacklog() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [searchResult, setSearchResult] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const fileRef = React.useRef();
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    axios.get(`${API_URL}/api/ublox/data`).then((res) => {
      if (res.data.total_rows > 0) setData(res.data);
    }).catch(() => {});
  }, []);

  const handleFile = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setSearchResult(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/ublox/upload`, formData);
      if (res.data.error) {
        setError(res.data.error);
      } else {
        setData(res.data);
        const newCount = res.data.data.filter(r => r._change?.type === "new").length;
        const modCount = res.data.data.filter(r => r._change?.type === "modified").length;
        const delCount = (res.data.deleted || []).length;
        if (res.data.has_prev) {
          setUploadResult({ newCount, modCount, delCount, prev_date: res.data.prev_date });
          setTimeout(() => setUploadResult(null), 8000);
        }
      }
    } catch (err) {
      setError("업로드 실패: " + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!search.trim()) { setSearchResult(null); return; }
    try {
      const res = await axios.get(`${API_URL}/api/ublox/search/${encodeURIComponent(search.trim())}`);
      setSearchResult(res.data);
    } catch { setSearchResult(null); }
  };

  const handleExport = async () => {
    if (!displayRows.length) return;
    const exportData = displayRows.map((row) => {
      const obj = {};
      displayColumns.forEach((col) => { obj[col] = row[col] ?? ""; });
      return obj;
    });
    const res = await axios.post(`${API_URL}/api/export`,
      { data: exportData, columns: displayColumns },
      { responseType: "blob" }
    );
    saveAs(res.data, `ublox_백로그_${new Date().toISOString().slice(0, 10)}.xlsx`);
  };

  const displayColumns = data?.columns || [];
  const allRows = searchResult?.data || data?.data || [];

  const filtered = useMemo(() => {
    if (searchResult) return allRows;
    if (!search.trim()) return allRows;
    const q = search.toLowerCase();
    return allRows.filter((row) =>
      displayColumns.some((col) => {
        const v = row[col];
        return v !== null && v !== undefined && String(v).toLowerCase().includes(q);
      })
    );
  }, [allRows, search, displayColumns, searchResult]);

  const displayRows = filtered;

  return (
    <>
      <div className="page-header">
        <h1>UBLOX 백로그</h1>
        <p className="subtitle">
          u-blox Salesforce 백로그 현황
          {data?.upload_date && <span className="db-status"> | 업로드: {data.upload_date}</span>}
          {data?.has_prev && data?.prev_date && <span className="db-status"> | 전일: {data.prev_date}</span>}
        </p>
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
            <div className="upload-text">u-blox 백로그 엑셀 업로드 (첫 업로드 후 다시 올리면 전일 대비 비교)</div>
            <div className="upload-hint">.xlsx 파일 지원</div>
          </>
        )}
      </div>

      {uploadResult && (
        <div className="success-banner">
          전일({uploadResult.prev_date}) 대비: 신규 <strong>{uploadResult.newCount}</strong>건 /
          변경 <strong>{uploadResult.modCount}</strong>건 /
          삭제 <strong>{uploadResult.delCount}</strong>건
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {searchResult?.summary && (
        <div className="search-summary">
          <strong>{searchResult.summary.type_number}</strong> |
          백로그: <strong>{searchResult.summary.total_qty.toLocaleString()}</strong>개
          ({searchResult.summary.order_count}건) |
          금액: <strong>${searchResult.summary.total_value.toLocaleString()}</strong> |
          고객: {searchResult.summary.customers.join(", ")}
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
              placeholder="품명(Type Number) 검색 — Enter로 상세 조회"
              value={search}
              onChange={(e) => { setSearch(e.target.value); if (!e.target.value) setSearchResult(null); }}
              onKeyDown={(e) => { if (e.key === "Enter") handleSearch(); }}
            />
          </div>

          <div className="ublox-legend">
            <span className="legend-new">■ 신규</span>
            <span className="legend-modified">■ 변경</span>
            <span className="legend-deleted">■ 삭제</span>
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  {displayColumns.map((col) => <th key={col}>{col}</th>)}
                </tr>
              </thead>
              <tbody>
                {displayRows.map((row, i) => {
                  const change = row._change || {};
                  let rowClass = "";
                  if (change.type === "new") rowClass = "row-new";
                  else if (change.type === "modified") rowClass = "row-modified";
                  else if (change.type === "deleted") rowClass = "row-deleted";

                  return (
                    <tr key={i} className={rowClass}>
                      <td style={{ color: "#999", textAlign: "center" }}>{i + 1}</td>
                      {displayColumns.map((col) => {
                        const isChanged = change.changed_fields?.includes(col);
                        return (
                          <td key={col}
                            className={`${NUMBER_COLS.has(col) ? "cell-number" : ""} ${isChanged ? "cell-changed" : ""}`}
                          >
                            {formatVal(row[col], col)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

export default UbloxBacklog;
