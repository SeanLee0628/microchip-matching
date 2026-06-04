import React, { useState, useEffect, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

const STAGE_COLORS = {
  0: "#94a3b8",
  1: "#3b82f6",
  2: "#f59e0b",
  3: "#8b5cf6",
  4: "#10b981",
};

const FIELD_LABELS = {
  unitron_po_date: "Unitron PO Date",
  unitron_po_no: "Unitron PO#",
  crd: "CRD",
  part_name: "Part Name",
  auo_pn: "AUO P/N",
  qty: "Qty",
  customer: "Customer",
  note: "참고",
  au_ship_date: "AU Shipping Date",
  au_invoice_no: "AU Invoice #",
  bl_no: "B/L #",
  bl_date: "B/L Date",
  payment_date: "T/T·L/C 결제일",
  import_date: "수입신고일/입고일",
  import_no: "수입신고번호",
  delivery_date: "Delivery Date",
  tax_invoice_date: "계산서 Date",
  unit_price: "Unit Price",
  amount: "Amount",
};

const DISPLAY_COLS = [
  "customer", "part_name", "auo_pn", "qty",
  "unitron_po_no", "unitron_po_date", "au_ship_date",
  "import_date", "delivery_date", "tax_invoice_date",
  "amount", "note",
];

// 현재 단계 → 다음 단계로 올리려면 필요한 날짜 + 보조 필드
const ADVANCE_MAP = {
  0: { date: "unitron_po_date", companion: "unitron_po_no", nextLabel: "발주 완료" },
  1: { date: "au_ship_date", companion: "au_invoice_no", nextLabel: "유니트론 입고 단계" },
  2: { date: "import_date", companion: "import_no", nextLabel: "고객 납품 단계" },
  3: { date: "tax_invoice_date", companion: null, nextLabel: "계산서 발행 완료" },
  4: null,
};

// 단계 → 전 단계로 복귀하려면 비워야 할 필드
const REVERT_MAP = {
  1: { clear: ["unitron_po_date", "unitron_po_no"], prevLabel: "발주 대기" },
  2: { clear: ["au_ship_date", "au_invoice_no"], prevLabel: "발주 완료" },
  3: { clear: ["import_date", "import_no"], prevLabel: "유니트론 입고 단계" },
  4: { clear: ["tax_invoice_date"], prevLabel: "고객 납품 단계" },
};

function AuoBacklog() {
  const [stages, setStages] = useState([]);
  const [totalRows, setTotalRows] = useState(0);
  const [activeStage, setActiveStage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [showManualForm, setShowManualForm] = useState(false);
  const [manualForm, setManualForm] = useState({});
  const [editingRow, setEditingRow] = useState(null);
  const [editForm, setEditForm] = useState({});
  const fileRef = useRef();

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_URL}/api/auo/data`);
      setStages(res.data.stages || []);
      setTotalRows(res.data.total_rows || 0);
      if (activeStage === null && res.data.stages && res.data.stages.length) {
        setActiveStage(res.data.stages[0].id);
      }
    } catch (e) {
      setError("데이터 조회 실패");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); /* eslint-disable-next-line */ }, []);

  const handleFile = async (file) => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setUploadResult(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/auo/upload`, fd);
      if (res.data.error) {
        setError(res.data.error);
      } else {
        setUploadResult(res.data);
        await loadData();
      }
    } catch (e) {
      setError("업로드 실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setUploading(false);
    }
  };

  const handleManualSubmit = async (e) => {
    e.preventDefault();
    try {
      await axios.post(`${API_URL}/api/auo/manual-add`, manualForm);
      setManualForm({});
      setShowManualForm(false);
      await loadData();
    } catch (err) {
      setError("추가 실패: " + err.message);
    }
  };

  const handleDeleteRow = async (rowId) => {
    if (!window.confirm("이 행을 삭제하시겠습니까?")) return;
    await axios.delete(`${API_URL}/api/auo/row/${rowId}`);
    await loadData();
  };

  const handleRevert = async (row) => {
    const revert = REVERT_MAP[row.stage];
    if (!revert) return;
    if (!window.confirm(`"${revert.prevLabel}" 단계로 되돌립니다. 현재 단계의 날짜 정보가 비워집니다.`)) return;
    const payload = { row_id: row.row_id };
    revert.clear.forEach(f => { payload[f] = ""; });
    await axios.post(`${API_URL}/api/auo/update`, payload);
    await loadData();
  };

  const openEdit = (row) => {
    setEditingRow(row);
    setEditForm({});
  };

  const handleEditSubmit = async (e) => {
    e.preventDefault();
    const advance = ADVANCE_MAP[editingRow.stage];
    if (!advance) return;
    const payload = { row_id: editingRow.row_id };
    payload[advance.date] = editForm[advance.date] || "";
    if (advance.companion) {
      payload[advance.companion] = editForm[advance.companion] || editingRow[advance.companion] || "";
    }
    try {
      await axios.post(`${API_URL}/api/auo/update`, payload);
      setEditingRow(null);
      setEditForm({});
      await loadData();
    } catch (err) {
      setError("저장 실패: " + err.message);
    }
  };

  const handleResetAll = async () => {
    if (!window.confirm(`전체 ${totalRows}건 삭제합니다. 정말 진행?`)) return;
    await axios.delete(`${API_URL}/api/auo/data`);
    await loadData();
  };

  const handleExport = async () => {
    try {
      const res = await axios.get(`${API_URL}/api/auo/export`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `AUO_Backlog_${new Date().toISOString().slice(0,10).replace(/-/g,"")}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("내보내기 실패: " + e.message);
    }
  };

  const fmt = (v) => {
    if (v === null || v === undefined || v === "") return "-";
    if (typeof v === "number") return v.toLocaleString();
    return String(v);
  };

  const currentStage = stages.find(s => s.id === activeStage);

  return (
    <div>
      <div className="page-header">
        <h1>AUO 백로그</h1>
        <p className="subtitle">단계별 자동 분류 · 총 {totalRows}건</p>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {uploadResult && (
        <div className="success-banner">
          신규 {uploadResult.inserted}건 / 업데이트 {uploadResult.updated}건 (총 {uploadResult.total_rows}건 처리)
        </div>
      )}

      <div style={{ display: "flex", gap: 12, marginBottom: 20, alignItems: "center" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files[0])}
        />
        <button
          onClick={() => fileRef.current.click()}
          disabled={uploading}
          style={{ padding: "10px 20px", background: "#3b82f6", color: "white", border: 0, borderRadius: 6, cursor: "pointer" }}
        >
          {uploading ? "처리 중..." : "📥 AUO 엑셀 업로드"}
        </button>
        <button
          onClick={() => setShowManualForm(!showManualForm)}
          style={{ padding: "10px 20px", background: "#94a3b8", color: "white", border: 0, borderRadius: 6, cursor: "pointer" }}
        >
          ➕ 발주 대기 수동 입력
        </button>
        <button
          onClick={handleExport}
          style={{ padding: "10px 20px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer" }}
        >
          📥 엑셀 내보내기
        </button>
        <button
          onClick={handleResetAll}
          style={{ padding: "10px 20px", background: "#ef4444", color: "white", border: 0, borderRadius: 6, cursor: "pointer", marginLeft: "auto" }}
        >
          전체 초기화
        </button>
      </div>

      {showManualForm && (
        <form onSubmit={handleManualSubmit} style={{ background: "#f8fafc", padding: 20, borderRadius: 8, marginBottom: 20 }}>
          <h3 style={{ marginTop: 0 }}>발주 대기 행 추가</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            {["customer", "part_name", "auo_pn", "qty", "unit_price", "unitron_po_no", "unitron_po_date", "crd", "note"].map(f => (
              <label key={f} style={{ display: "flex", flexDirection: "column", fontSize: 12, color: "#64748b" }}>
                {FIELD_LABELS[f]}
                <input
                  type={f.includes("date") ? "date" : (f === "qty" || f === "unit_price") ? "number" : "text"}
                  value={manualForm[f] || ""}
                  onChange={(e) => setManualForm({ ...manualForm, [f]: e.target.value })}
                  style={{ padding: 6, border: "1px solid #cbd5e1", borderRadius: 4, marginTop: 4 }}
                />
              </label>
            ))}
          </div>
          <div style={{ marginTop: 12 }}>
            <button type="submit" style={{ padding: "8px 16px", background: "#10b981", color: "white", border: 0, borderRadius: 4, cursor: "pointer", marginRight: 8 }}>저장</button>
            <button type="button" onClick={() => setShowManualForm(false)} style={{ padding: "8px 16px", background: "#e2e8f0", border: 0, borderRadius: 4, cursor: "pointer" }}>취소</button>
          </div>
        </form>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {stages.map(s => (
          <button
            key={s.id}
            onClick={() => setActiveStage(s.id)}
            style={{
              flex: 1,
              padding: "12px",
              background: s.id === activeStage ? STAGE_COLORS[s.id] : "white",
              color: s.id === activeStage ? "white" : "#334155",
              border: `2px solid ${STAGE_COLORS[s.id]}`,
              borderRadius: 8,
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            <div style={{ fontSize: 11, opacity: 0.8 }}>Stage {s.id}</div>
            <div style={{ fontWeight: 700, fontSize: 14 }}>{s.label}</div>
            <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>{s.count}건</div>
            <div style={{ fontSize: 11, opacity: 0.8 }}>Qty {Math.round(s.qty).toLocaleString()} · ${Math.round(s.amount).toLocaleString()}</div>
          </button>
        ))}
      </div>

      {loading && <div>로딩 중...</div>}

      {currentStage && (
        <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#f1f5f9" }}>
                <th style={{ padding: 10, borderBottom: "2px solid #cbd5e1", width: 110 }}></th>
                {DISPLAY_COLS.map(c => (
                  <th key={c} style={{ padding: 10, textAlign: "left", borderBottom: "2px solid #cbd5e1", whiteSpace: "nowrap" }}>
                    {FIELD_LABELS[c]}
                  </th>
                ))}
                <th style={{ padding: 10, borderBottom: "2px solid #cbd5e1" }}></th>
              </tr>
            </thead>
            <tbody>
              {currentStage.rows.length === 0 ? (
                <tr><td colSpan={DISPLAY_COLS.length + 2} style={{ padding: 40, textAlign: "center", color: "#94a3b8" }}>이 단계에 해당하는 행 없음</td></tr>
              ) : currentStage.rows.map(r => {
                const canUpgrade = ADVANCE_MAP[r.stage] != null;
                const canRevert = REVERT_MAP[r.stage] != null;
                return (
                  <tr key={r.row_id} style={{ borderBottom: "1px solid #e2e8f0" }}>
                    <td style={{ padding: 8, whiteSpace: "nowrap" }}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {canUpgrade ? (
                          <button
                            onClick={() => openEdit(r)}
                            style={{
                              padding: "6px 12px",
                              background: "#10b981",
                              color: "white",
                              border: 0,
                              borderRadius: 4,
                              cursor: "pointer",
                              fontWeight: 700,
                              fontSize: 12,
                              letterSpacing: "0.5px",
                              boxShadow: "0 1px 2px rgba(0,0,0,0.1)",
                            }}
                            title="다음 단계로 올리기"
                          >
                            ↑ UPGRADE
                          </button>
                        ) : (
                          <span style={{ fontSize: 11, color: "#94a3b8", padding: "6px 12px", textAlign: "center" }}>최종</span>
                        )}
                        {canRevert && (
                          <button
                            onClick={() => handleRevert(r)}
                            style={{
                              padding: "4px 10px",
                              background: "white",
                              color: "#64748b",
                              border: "1px solid #cbd5e1",
                              borderRadius: 4,
                              cursor: "pointer",
                              fontSize: 11,
                            }}
                            title="전 단계로 되돌리기"
                          >
                            ↩ 되돌리기
                          </button>
                        )}
                      </div>
                    </td>
                    {DISPLAY_COLS.map(c => (
                      <td key={c} style={{ padding: 8, whiteSpace: "nowrap" }}>{fmt(r[c])}</td>
                    ))}
                    <td style={{ padding: 8, whiteSpace: "nowrap" }}>
                      <button onClick={() => handleDeleteRow(r.row_id)} style={{ background: "transparent", border: 0, color: "#ef4444", cursor: "pointer", fontSize: 16 }} title="삭제">🗑</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editingRow && (() => {
        const advance = ADVANCE_MAP[editingRow.stage];
        return (
          <div
            onClick={() => setEditingRow(null)}
            style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
          >
            <form
              onClick={(e) => e.stopPropagation()}
              onSubmit={handleEditSubmit}
              style={{ background: "white", padding: 24, borderRadius: 8, width: 480 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <h3 style={{ margin: 0 }}>단계 올리기</h3>
                <button type="button" onClick={() => setEditingRow(null)} style={{ background: "transparent", border: 0, fontSize: 22, cursor: "pointer" }}>×</button>
              </div>

              <div style={{ fontSize: 13, color: "#475569", marginBottom: 16, padding: 12, background: "#f1f5f9", borderRadius: 6 }}>
                <div><b>{editingRow.auo_pn}</b> · {editingRow.customer}</div>
                <div style={{ marginTop: 6, fontSize: 12 }}>Qty {editingRow.qty} · PO {editingRow.unitron_po_no || "-"}</div>
              </div>

              {!advance ? (
                <div style={{ padding: 20, textAlign: "center", color: "#64748b" }}>
                  이미 최종 단계입니다. 더 올릴 수 없습니다.
                </div>
              ) : (
                <>
                  <div style={{ marginBottom: 16, fontSize: 14 }}>
                    현재: <b style={{ color: STAGE_COLORS[editingRow.stage] }}>{(stages.find(s => s.id === editingRow.stage) || {}).label}</b> → 다음: <b style={{ color: STAGE_COLORS[editingRow.stage + 1] }}>{advance.nextLabel}</b>
                  </div>

                  <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: "#64748b", marginBottom: 12 }}>
                    {FIELD_LABELS[advance.date]} <span style={{ color: "#ef4444" }}>*</span>
                    <input
                      type="date"
                      required
                      value={editForm[advance.date] || ""}
                      onChange={(e) => setEditForm({ ...editForm, [advance.date]: e.target.value })}
                      style={{ padding: 8, border: "1px solid #cbd5e1", borderRadius: 4, marginTop: 4, fontSize: 14 }}
                    />
                  </label>

                  {advance.companion && (
                    <label style={{ display: "flex", flexDirection: "column", fontSize: 12, color: "#64748b", marginBottom: 16 }}>
                      {FIELD_LABELS[advance.companion]} <span style={{ color: "#94a3b8" }}>(선택)</span>
                      <input
                        type="text"
                        value={editForm[advance.companion] ?? (editingRow[advance.companion] || "")}
                        onChange={(e) => setEditForm({ ...editForm, [advance.companion]: e.target.value })}
                        style={{ padding: 8, border: "1px solid #cbd5e1", borderRadius: 4, marginTop: 4, fontSize: 14 }}
                      />
                    </label>
                  )}
                </>
              )}

              <div style={{ marginTop: 20, display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button type="button" onClick={() => setEditingRow(null)} style={{ padding: "10px 20px", background: "#e2e8f0", border: 0, borderRadius: 6, cursor: "pointer" }}>취소</button>
                {advance && (
                  <button type="submit" style={{ padding: "10px 20px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}>단계 올리기</button>
                )}
              </div>
            </form>
          </div>
        );
      })()}
    </div>
  );
}

export default AuoBacklog;
