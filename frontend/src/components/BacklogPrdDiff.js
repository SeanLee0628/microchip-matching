import React, { useState, useRef, useMemo } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

const ALL_COLS = [
  "Mchp Catalog Part Number", "End Customer Name", "ODM/SubCon Name", "Customer PO#",
  "SO#", "Quote No.", "Qty Due", "Unit Price", "Amount Due",
  "ORD", "CRD", "PRD",
  "일정변동 현황", "변경전 일정", "변경일자",
  "업체명", "더존업체명코드",
];

const NUMERIC_COLS = new Set(["Qty Due", "Unit Price", "Amount Due", "변경일자", "더존업체명코드"]);
const CENTER_COLS = new Set(["ORD", "CRD", "PRD", "변경전 일정", "일정변동 현황"]);

function fmt(v, col) {
  if (v == null || v === "") return "-";
  if (col === "Qty Due" || col === "더존업체명코드") return Number(v).toLocaleString();
  if (col === "Unit Price") return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 5 });
  if (col === "Amount Due") return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (col === "변경일자") return `${v}일`;
  return v;
}

function BacklogPrdDiff() {
  const [beforeFile, setBeforeFile] = useState(null);
  const [afterFile, setAfterFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [filter, setFilter] = useState("all"); // all | push | pull
  const [query, setQuery] = useState("");
  const [dragOverBefore, setDragOverBefore] = useState(false);
  const [dragOverAfter, setDragOverAfter] = useState(false);
  const beforeRef = useRef();
  const afterRef = useRef();

  const reset = () => {
    setBeforeFile(null);
    setAfterFile(null);
    setError(null);
    setPreview(null);
    setFilter("all");
    setQuery("");
    setDragOverBefore(false);
    setDragOverAfter(false);
    if (beforeRef.current) beforeRef.current.value = "";
    if (afterRef.current) afterRef.current.value = "";
  };

  const acceptDroppedFile = (e, setFile, setDragOver) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    const name = f.name.toLowerCase();
    if (!(name.endsWith(".xlsx") || name.endsWith(".xlsm"))) {
      setError(".xlsx 또는 .xlsm 파일만 지원합니다.");
      return;
    }
    setFile(f);
    setPreview(null);
    setError(null);
  };

  const handleCompare = async () => {
    if (!beforeFile || !afterFile) {
      setError("전일(BEFORE)·금일(AFTER) 두 파일을 모두 선택하세요.");
      return;
    }
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const fd = new FormData();
      fd.append("before", beforeFile);
      fd.append("after", afterFile);
      const res = await axios.post(`${API_URL}/api/backlog/prd-diff/preview`, fd);
      if (res.data.error) {
        setError(res.data.error);
        return;
      }
      setPreview(res.data);
    } catch (e) {
      setError(`요청 실패: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!beforeFile || !afterFile) return;
    setExporting(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("before", beforeFile);
      fd.append("after", afterFile);
      const res = await axios.post(`${API_URL}/api/backlog/prd-diff`, fd, { responseType: "blob" });

      if (res.data.type && res.data.type.includes("json")) {
        const text = await res.data.text();
        try {
          setError(JSON.parse(text).error || "내보내기 실패");
        } catch {
          setError("내보내기 실패");
        }
        return;
      }

      let fname = "마이크로칩 백록_PRD변동.xlsx";
      const cd = res.headers["content-disposition"];
      if (cd) {
        const m = cd.match(/filename\*=UTF-8''([^;]+)/i);
        if (m) {
          try { fname = decodeURIComponent(m[1]); } catch {}
        }
      }

      const blob = new Blob([res.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      if (e.response && e.response.data instanceof Blob) {
        try {
          const text = await e.response.data.text();
          setError(JSON.parse(text).error || `내보내기 실패: ${e.message}`);
          return;
        } catch {}
      }
      setError(`내보내기 실패: ${e.response?.data?.detail || e.message}`);
    } finally {
      setExporting(false);
    }
  };

  const fileSlot = (label, file, setFile, refEl, color, dragOver, setDragOver) => (
    <div
      style={{
        flex: 1,
        border: `2px dashed ${dragOver ? color : file ? color : "#cbd5e1"}`,
        background: dragOver ? "#eff6ff" : file ? "#f8fafc" : "white",
        borderRadius: 8,
        padding: 16,
        textAlign: "center",
        cursor: "pointer",
        transition: "all 0.15s",
      }}
      onClick={() => refEl.current && refEl.current.click()}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragEnter={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
      onDrop={(e) => acceptDroppedFile(e, setFile, setDragOver)}
    >
      <input
        ref={refEl}
        type="file"
        accept=".xlsx,.xlsm"
        style={{ display: "none" }}
        onChange={(e) => { setFile(e.target.files[0] || null); setPreview(null); }}
      />
      <div style={{ fontSize: 12, fontWeight: 700, color, marginBottom: 6 }}>{label}</div>
      {file ? (
        <>
          <div style={{ fontSize: 12, color: "#0f172a", marginBottom: 2, wordBreak: "break-all" }}>
            📄 {file.name}
          </div>
          <div style={{ fontSize: 10, color: "#64748b" }}>
            {(file.size / 1024).toFixed(1)} KB · 클릭하여 변경
          </div>
        </>
      ) : (
        <div style={{ fontSize: 11, color: dragOver ? color : "#94a3b8" }}>
          {dragOver ? "여기에 놓으세요" : "클릭하거나 .xlsx 파일을 끌어다 놓으세요"}
        </div>
      )}
    </div>
  );

  const filtered = useMemo(() => {
    if (!preview) return [];
    const q = query.trim().toLowerCase();
    return preview.rows.filter((r) => {
      if (filter === "push" && r["일정변동 현황"] !== "PUSH-OUT") return false;
      if (filter === "pull" && r["일정변동 현황"] !== "PULL-IN") return false;
      if (!q) return true;
      return ALL_COLS.some((c) => {
        const v = r[c];
        return v != null && String(v).toLowerCase().includes(q);
      });
    });
  }, [preview, filter, query]);

  return (
    <div>
      <div className="page-header">
        <h1>백록 PRD 변동 비교</h1>
        <p className="subtitle">
          마이크로칩 백록(벤더발주) 두 스냅샷을 <b>SO# 기준</b> 비교 · 웹에서 확인 후 엑셀로 내보내기
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "flex", gap: 14, marginBottom: 14 }}>
        {fileSlot("전일 (BEFORE)", beforeFile, setBeforeFile, beforeRef, "#64748b", dragOverBefore, setDragOverBefore)}
        <div style={{ alignSelf: "center", fontSize: 24, color: "#94a3b8" }}>→</div>
        {fileSlot("금일 (AFTER)", afterFile, setAfterFile, afterRef, "#3b82f6", dragOverAfter, setDragOverAfter)}
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <button
          onClick={handleCompare}
          disabled={loading || !beforeFile || !afterFile}
          style={{
            padding: "11px 24px",
            background: loading || !beforeFile || !afterFile ? "#94a3b8" : "#3b82f6",
            color: "white",
            border: 0,
            borderRadius: 6,
            cursor: loading || !beforeFile || !afterFile ? "not-allowed" : "pointer",
            fontWeight: 700,
            fontSize: 14,
          }}
        >
          {loading ? "비교 중..." : "🔍 비교 실행"}
        </button>
        <button
          onClick={handleExport}
          disabled={exporting || !preview}
          style={{
            padding: "11px 22px",
            background: exporting || !preview ? "#94a3b8" : "#10b981",
            color: "white",
            border: 0,
            borderRadius: 6,
            cursor: exporting || !preview ? "not-allowed" : "pointer",
            fontWeight: 700,
            fontSize: 14,
          }}
        >
          {exporting ? "내보내는 중..." : "📥 엑셀 내보내기"}
        </button>
        <button
          onClick={reset}
          disabled={loading || exporting}
          style={{
            padding: "11px 18px",
            background: "white",
            color: "#475569",
            border: "1px solid #cbd5e1",
            borderRadius: 6,
            cursor: loading || exporting ? "not-allowed" : "pointer",
            fontSize: 13,
          }}
        >
          초기화
        </button>
      </div>

      {preview && (
        <>
          <div
            style={{
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
              borderRadius: 8,
              padding: 14,
              marginBottom: 12,
            }}
          >
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8 }}>
              <Stat label="BEFORE SO#" value={preview.before_count} />
              <Stat label="AFTER SO#" value={preview.after_count} />
              <Stat label="총 변동" value={preview.changed_count} bg="#fef3c7" color="#854d0e" />
              <Stat label="PUSH-OUT" value={preview.push_out} bg="#fee2e2" color="#991b1b" />
              <Stat label="PULL-IN" value={preview.pull_in} bg="#dcfce7" color="#166534" />
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center", flexWrap: "wrap" }}>
            <FilterBtn active={filter === "all"} onClick={() => setFilter("all")}>
              전체 {preview.changed_count}
            </FilterBtn>
            <FilterBtn active={filter === "push"} onClick={() => setFilter("push")} color="#991b1b" bg="#fee2e2">
              PUSH-OUT {preview.push_out}
            </FilterBtn>
            <FilterBtn active={filter === "pull"} onClick={() => setFilter("pull")} color="#166534" bg="#dcfce7">
              PULL-IN {preview.pull_in}
            </FilterBtn>
            <input
              type="text"
              placeholder="🔎 SO# / Part / 고객사 검색"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{
                marginLeft: "auto",
                padding: "6px 10px",
                fontSize: 12,
                border: "1px solid #cbd5e1",
                borderRadius: 6,
                width: 220,
              }}
            />
            <span style={{ fontSize: 12, color: "#64748b" }}>
              {filtered.length} / {preview.changed_count} 행
            </span>
          </div>

          {preview.changed_count === 0 ? (
            <div
              style={{
                padding: 30,
                textAlign: "center",
                background: "white",
                border: "1px solid #e2e8f0",
                borderRadius: 8,
                color: "#64748b",
              }}
            >
              PRD 변동된 SO#가 없습니다.
            </div>
          ) : (
            <div style={{ overflow: "auto", border: "1px solid #e2e8f0", borderRadius: 8, background: "white", maxHeight: 600 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, whiteSpace: "nowrap" }}>
                <thead style={{ position: "sticky", top: 0, zIndex: 1 }}>
                  <tr style={{ background: "#1f3a8a", color: "white" }}>
                    {ALL_COLS.map((c) => (
                      <th
                        key={c}
                        style={{
                          padding: "8px 10px",
                          textAlign: CENTER_COLS.has(c) ? "center" : NUMERIC_COLS.has(c) ? "right" : "left",
                          fontWeight: 700,
                          borderBottom: "2px solid #1e3a8a",
                          fontSize: 11,
                        }}
                      >
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r, i) => {
                    const status = r["일정변동 현황"];
                    const rowBg = status === "PUSH-OUT" ? "#fff5f5" : status === "PULL-IN" ? "#f0fdf4" : "white";
                    return (
                      <tr key={i} style={{ background: rowBg, borderBottom: "1px solid #f1f5f9" }}>
                        {ALL_COLS.map((c) => {
                          const v = r[c];
                          const isStatus = c === "일정변동 현황";
                          return (
                            <td
                              key={c}
                              style={{
                                padding: "5px 10px",
                                textAlign: CENTER_COLS.has(c) ? "center" : NUMERIC_COLS.has(c) ? "right" : "left",
                                fontVariantNumeric: NUMERIC_COLS.has(c) ? "tabular-nums" : "normal",
                              }}
                            >
                              {isStatus ? (
                                <span
                                  style={{
                                    display: "inline-block",
                                    padding: "2px 8px",
                                    borderRadius: 10,
                                    fontWeight: 700,
                                    fontSize: 10.5,
                                    background: status === "PUSH-OUT" ? "#fee2e2" : "#dcfce7",
                                    color: status === "PUSH-OUT" ? "#991b1b" : "#166534",
                                  }}
                                >
                                  {status}
                                </span>
                              ) : (
                                fmt(v, c)
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {!preview && (
        <div style={{ background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, padding: 14, fontSize: 12, color: "#78350f", lineHeight: 1.7 }}>
          <b>지원 포맷 (자동 인식)</b><br />
          · 시트 <code>인풋</code> (헤더 2행, 키 <code>SO#</code>) 또는<br />
          · 시트 <code>마이크로칩백록(벤더발주)</code> (헤더 3행, 키 <code>Mchp Sales Order #</code>)<br />
          <br />
          <b>일정변동 분류</b>: 금일 PRD가 늦어졌으면 <span style={{ color: "#991b1b", fontWeight: 700 }}>PUSH-OUT</span> / 빨라졌으면 <span style={{ color: "#166534", fontWeight: 700 }}>PULL-IN</span> · 변경일자 = |금일 − 전일| (절대값)
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, bg = "white", color = "#0f172a" }) {
  return (
    <div
      style={{
        background: bg,
        border: "1px solid #e2e8f0",
        borderRadius: 6,
        padding: "8px 10px",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 10.5, color: "#64748b", marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color }}>{Number(value).toLocaleString()}</div>
    </div>
  );
}

function FilterBtn({ active, onClick, children, color = "#0f172a", bg = "#e2e8f0" }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "5px 12px",
        background: active ? bg : "white",
        color: active ? color : "#64748b",
        border: `1px solid ${active ? color : "#cbd5e1"}`,
        borderRadius: 14,
        cursor: "pointer",
        fontWeight: active ? 700 : 500,
        fontSize: 12,
      }}
    >
      {children}
    </button>
  );
}

export default BacklogPrdDiff;
