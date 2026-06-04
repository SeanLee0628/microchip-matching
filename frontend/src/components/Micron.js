import React, { useState, useEffect, useRef } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API = process.env.REACT_APP_API_URL || "";

const EDITABLE_COLS = new Set([
  "Status", "수입면장번호", "BL번호",
  "Booking Customer & FSE", "Qty_booking", "비고",
]);
const NUM_COLS = new Set(["QTY", "Qty_booking"]);
const STATUS_OPTIONS = ["재고", "입고예정", "출고"];
const STATUS_FILTER_OPTIONS = ["재고", "입고예정", "출고"];

function Micron() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [statusFilters, setStatusFilters] = useState([]);
  const [didFilter, setDidFilter] = useState("");
  const [mpnFilter, setMpnFilter] = useState("");
  const [notesOnly, setNotesOnly] = useState(false);
  const [summary, setSummary] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editValues, setEditValues] = useState({});
  const fileRef = useRef();

  const fetchData = async () => {
    const res = await axios.get(`${API}/api/micron/data`, {
      params: { status: statusFilters.join(","), did: didFilter, mpn: mpnFilter, notes_only: notesOnly ? "true" : "" },
    });
    setData(res.data);
  };

  const toggleStatusFilter = (s) => {
    setStatusFilters(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
  };

  // 페이지 로드 시 DB에서 자동 조회
  useEffect(() => {
    fetchData();
    // eslint-disable-next-line
  }, []);

  const handleUpload = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setSummary(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await axios.post(`${API}/api/micron/upload`, formData);
      if (res.data.error) setError(res.data.error);
      else setData(res.data);
    } catch (err) {
      setError("업로드 실패: " + err.message);
    }
    setLoading(false);
  };

  const handleSearch = () => { fetchData(); };

  const handleSummary = async (did) => {
    const res = await axios.get(`${API}/api/micron/summary/${did}`);
    setSummary(res.data);
  };

  const startEdit = (row) => {
    setEditingId(row["_id"]);
    setEditValues({
      "Status": row["Status"] || "",
      "수입면장번호": row["수입면장번호"] || "",
      "BL번호": row["BL번호"] || "",
      "Booking Customer & FSE": row["Booking Customer & FSE"] || "",
      "Qty_booking": row["Qty_booking"] || "",
      "비고": row["비고"] || "",
    });
  };

  const saveEdit = async () => {
    await axios.post(`${API}/api/micron/update`, { _id: editingId, ...editValues });
    setEditingId(null);
    fetchData();
  };

  const handleStatusInlineChange = async (row, newStatus) => {
    await axios.post(`${API}/api/micron/update`, {
      _id: row["_id"],
      Status: newStatus,
      "수입면장번호": row["수입면장번호"] || "",
      "BL번호": row["BL번호"] || "",
      "Booking Customer & FSE": row["Booking Customer & FSE"] || "",
      "Qty_booking": row["Qty_booking"] || "",
      "비고": row["비고"] || "",
    });
    fetchData();
  };

  const handleExport = async () => {
    if (!data?.data?.length) return;
    const res = await axios.post(`${API}/api/export`,
      { data: data.data, columns: data.columns },
      { responseType: "blob" }
    );
    saveAs(res.data, `마이크론_재고_${new Date().toISOString().slice(0, 10)}.xlsx`);
  };

  const columns = data?.columns || [];
  const rows = data?.data || [];

  return (
    <>
      <div className="page-header">
        <h1>마이크론 재고</h1>
        <p className="subtitle">실시간 재고 조회 · 입고예정 통합 · Booking 관리</p>
      </div>

      {/* 업로드 */}
      <div className="upload-area" onClick={() => fileRef.current.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); handleUpload(e.dataTransfer.files[0]); }}
        style={{ cursor: "pointer", marginBottom: 16 }}>
        <input ref={fileRef} type="file" accept=".xlsx" style={{ display: "none" }}
          onChange={(e) => handleUpload(e.target.files[0])} />
        {loading ? <div className="upload-loading">처리 중...</div> : (
          <>
            <div className="upload-icon">&#128196;</div>
            <div className="upload-text">마이크론 재고 엑셀 업로드</div>
            <div className="upload-hint">.xlsx (Detail 시트 기준)</div>
          </>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* 필터 */}
      {data && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", padding: "6px 12px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13, background: "#fff" }}>
              <span style={{ color: "#64748b", fontWeight: 600 }}>Status:</span>
              {STATUS_FILTER_OPTIONS.map(s => (
                <label key={s} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", whiteSpace: "nowrap" }}>
                  <input
                    type="checkbox"
                    checked={statusFilters.includes(s)}
                    onChange={() => toggleStatusFilter(s)}
                  />
                  {s}
                </label>
              ))}
              {statusFilters.length > 0 && (
                <button
                  onClick={() => setStatusFilters([])}
                  style={{ background: "transparent", border: 0, color: "#64748b", cursor: "pointer", fontSize: 12, padding: "0 4px" }}
                  title="전체 해제"
                >초기화</button>
              )}
            </div>
            <input style={{ padding: "6px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13, width: 100 }}
              placeholder="DID" value={didFilter} onChange={(e) => setDidFilter(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()} />
            <input style={{ padding: "6px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13, width: 200 }}
              placeholder="MPN" value={mpnFilter} onChange={(e) => setMpnFilter(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()} />
            <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
              <input type="checkbox" checked={notesOnly} onChange={(e) => setNotesOnly(e.target.checked)} />
              비고 있는 것만
            </label>
            <button className="sidebar-btn" style={{ width: "auto", padding: "6px 16px" }} onClick={handleSearch}>검색</button>
            <button className="export-btn" style={{ marginLeft: "auto" }} onClick={handleExport}>
              <span>&#128229;</span> 엑셀 내보내기
            </button>
          </div>

          {/* 통합 조회 결과 */}
          {summary && (
            <div style={{ background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 8, padding: 16, marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <h3 style={{ fontSize: 15, fontWeight: 700 }}>
                  {summary.did} 통합 조회 — {summary.mpns?.join(", ")}
                </h3>
                <button style={{ background: "none", border: "none", cursor: "pointer", color: "#999", fontSize: 18 }}
                  onClick={() => setSummary(null)}>&times;</button>
              </div>

              {/* 요약 카드 */}
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
                {Object.entries(summary.by_status || {}).map(([status, info]) => (
                  <div key={status} style={{ background: "#fff", borderRadius: 6, padding: "8px 16px", border: "1px solid #e5e5e5" }}>
                    <div style={{ fontSize: 11, color: "#888" }}>{status === "재고" ? "📦 " : status.includes("입고") ? "🚚 " : ""}{status}</div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{info.qty?.toLocaleString()}</div>
                    <div style={{ fontSize: 11, color: "#aaa" }}>{info.count}건</div>
                  </div>
                ))}
                <div style={{ background: "#1a1a2e", borderRadius: 6, padding: "8px 16px", color: "#fff" }}>
                  <div style={{ fontSize: 11, color: "#aaa" }}>합계</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{summary.total_qty?.toLocaleString()}</div>
                </div>
              </div>

              {/* 입고일별 상세 테이블 */}
              {(() => {
                const items = summary.items || [];
                const statusGroups = {};
                items.forEach(item => {
                  const s = item.Status || "기타";
                  if (!statusGroups[s]) statusGroups[s] = [];
                  statusGroups[s].push(item);
                });
                // 각 그룹 내 입고일 정렬
                Object.values(statusGroups).forEach(arr => arr.sort((a, b) => {
                  const da = a["Ship Date"] || "9999";
                  const db2 = b["Ship Date"] || "9999";
                  return da < db2 ? -1 : da > db2 ? 1 : 0;
                }));

                return Object.entries(statusGroups).map(([status, groupItems]) => (
                  <div key={status} style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6, color: status === "재고" ? "#2e7d32" : status.includes("입고") ? "#e67e22" : "#666" }}>
                      {status === "재고" ? "📦" : "🚚"} {status} ({groupItems.length}건, {groupItems.reduce((s, i) => s + (Number(i.QTY) || 0), 0).toLocaleString()}개)
                    </div>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, background: "#fff", borderRadius: 4 }}>
                      <thead>
                        <tr style={{ background: "#f5f5f5" }}>
                          <th style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid #eee" }}>입고일</th>
                          <th style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid #eee" }}>PO</th>
                          <th style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid #eee" }}>MPN</th>
                          <th style={{ padding: "6px 8px", textAlign: "right", borderBottom: "1px solid #eee" }}>수량</th>
                          <th style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid #eee" }}>Type</th>
                          <th style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid #eee" }}>End Customer</th>
                          <th style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid #eee" }}>비고</th>
                        </tr>
                      </thead>
                      <tbody>
                        {groupItems.map((item, j) => {
                          const shipDate = item["Ship Date"];
                          const displayDate = shipDate && shipDate !== "None" && shipDate !== "nan" ? shipDate : "미정";
                          const note = item["비고"];
                          const displayNote = note && note !== "None" && note !== "nan" ? note : "";
                          return (
                            <tr key={j} style={{ borderBottom: "1px solid #f5f5f5" }}>
                              <td style={{ padding: "5px 8px", fontWeight: 600 }}>{displayDate}</td>
                              <td style={{ padding: "5px 8px" }}>{item.PO || ""}</td>
                              <td style={{ padding: "5px 8px" }}>{item.MPN || ""}</td>
                              <td style={{ padding: "5px 8px", textAlign: "right", fontWeight: 700 }}>{Number(item.QTY || 0).toLocaleString()}</td>
                              <td style={{ padding: "5px 8px", color: "#888" }}>{item.Type || ""}</td>
                              <td style={{ padding: "5px 8px", color: "#888" }}>{item["End customer"] || ""}</td>
                              <td style={{ padding: "5px 8px", color: displayNote ? "#e74c3c" : "#ccc" }}>{displayNote || "-"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ));
              })()}
            </div>
          )}

          <div className="table-header">
            <div className="table-info">총 <strong>{rows.length}</strong>건</div>
          </div>

          {/* 테이블 */}
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  {columns.map((col) => (
                    <React.Fragment key={col}>
                      {col === "Booking Customer & FSE" && <th>CS수정</th>}
                      <th style={EDITABLE_COLS.has(col) ? { background: "#2d5a27", color: "#fff" } : {}}>
                        {col}
                      </th>
                    </React.Fragment>
                  ))}
                  <th>조회</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} style={
                    row.Status === "입고예정" || row.Status === "입고 예정 자재" ? { background: "#fff8e1" } :
                    row.Status === "출고" ? { background: "#e0f2fe" } :
                    row.Status === "무상샘플 재고" ? { background: "#f3e5f5" } : {}
                  }>
                    <td style={{ color: "#999", textAlign: "center" }}>{i + 1}</td>
                    {columns.map((col, colIdx) => {
                      const isEditing = editingId === row["_id"] && EDITABLE_COLS.has(col);
                      const val = row[col];
                      const display = val === null || val === undefined || String(val) === "None" || String(val) === "nan" ? "" : val;
                      const isFirstEditable = col === "Booking Customer & FSE";

                      return (
                        <React.Fragment key={col}>
                          {isFirstEditable && (
                            <td style={{ textAlign: "center" }}>
                              {editingId === row["_id"] ? (
                                <button style={{ background: "#27ae60", color: "#fff", border: "none", padding: "3px 8px", borderRadius: 3, fontSize: 11, cursor: "pointer" }}
                                  onClick={saveEdit}>저장</button>
                              ) : (
                                <button style={{ background: "#e67e22", color: "#fff", border: "none", padding: "3px 8px", borderRadius: 3, fontSize: 11, cursor: "pointer" }}
                                  onClick={() => startEdit(row)}>편집</button>
                              )}
                            </td>
                          )}
                          {col === "Status" ? (
                            <td style={{ background: "#f0fff0", padding: 2 }}>
                              <select
                                style={{ width: "100%", padding: "3px 4px", border: "1px solid #cbd5e1", borderRadius: 3, fontSize: 12, background: "#fff" }}
                                value={display || ""}
                                onChange={(e) => handleStatusInlineChange(row, e.target.value)}
                              >
                                <option value="">-</option>
                                {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
                                {display && !STATUS_OPTIONS.includes(display) && (
                                  <option value={display}>{display}</option>
                                )}
                              </select>
                            </td>
                          ) : isEditing ? (
                            <td>
                              <input style={{ width: "100%", padding: 4, border: "1px solid #4caf50", borderRadius: 3, fontSize: 12 }}
                                value={editValues[col] || ""}
                                onChange={(e) => setEditValues({ ...editValues, [col]: e.target.value })} />
                            </td>
                          ) : (
                            <td className={NUM_COLS.has(col) ? "cell-number" : ""}
                              style={EDITABLE_COLS.has(col) ? { background: "#f0fff0" } : {}}>
                              {NUM_COLS.has(col) && display ? Number(display).toLocaleString() : display}
                            </td>
                          )}
                        </React.Fragment>
                      );
                    })}
                    <td style={{ textAlign: "center" }}>
                      <button style={{ background: "#3498db", color: "#fff", border: "none", padding: "3px 8px", borderRadius: 3, fontSize: 11, cursor: "pointer" }}
                        onClick={() => handleSummary(row.DID)}>통합</button>
                    </td>
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

export default Micron;
