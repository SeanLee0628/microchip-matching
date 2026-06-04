import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function SubulFilter() {
  const [invFile, setInvFile] = useState(null);
  const [subFile, setSubFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const invRef = useRef();
  const subRef = useRef();

  const run = async () => {
    if (!invFile || !subFile) {
      setError("품목 리스트와 수불부 파일을 모두 선택하세요.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    const fd = new FormData();
    fd.append("inventory", invFile);
    fd.append("subul", subFile);
    try {
      const res = await axios.post(`${API_URL}/api/subul-filter/process`, fd, {
        responseType: "blob",
      });

      // 서버가 에러를 JSON(blob)으로 돌려준 경우 처리
      const ctype = res.headers["content-type"] || "";
      if (ctype.includes("application/json")) {
        const txt = await res.data.text();
        const j = JSON.parse(txt);
        setError(j.error || "처리 실패");
        return;
      }

      // 결과 메타데이터 (유지/삭제 건수 등)
      const b64 = res.headers["x-result-json-b64"];
      if (b64) {
        try {
          setResult(JSON.parse(decodeURIComponent(escape(atob(b64)))));
        } catch (_) {
          /* 메타 파싱 실패는 무시하고 다운로드는 진행 */
        }
      }

      // 파일 다운로드
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      const base = (subFile.name || "수불부.xlsx").replace(/\.[^.]+$/, "");
      a.download = `${base}_filtered.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      // blob 응답에서 에러 메시지 추출 시도
      let msg = e.message;
      const d = e.response?.data;
      if (d && typeof d.text === "function") {
        try {
          const j = JSON.parse(await d.text());
          msg = j.error || j.detail || msg;
        } catch (_) {
          /* noop */
        }
      }
      setError("실패: " + msg);
    } finally {
      setLoading(false);
    }
  };

  const pickerBtn = (label, file, ref, onPick) => (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <button
        onClick={() => ref.current.click()}
        disabled={loading}
        style={{
          padding: "10px 18px",
          background: "#fff",
          color: "#334155",
          border: "1px solid #cbd5e1",
          borderRadius: 6,
          cursor: "pointer",
          fontWeight: 600,
          minWidth: 200,
          textAlign: "left",
        }}
      >
        {file ? `📄 ${file.name}` : label}
      </button>
      <input
        ref={ref}
        type="file"
        accept=".xlsx,.xls"
        style={{ display: "none" }}
        onChange={(e) => onPick(e.target.files[0] || null)}
      />
    </div>
  );

  return (
    <div>
      <div className="page-header">
        <h1>수불부 품목 필터 (4실)</h1>
        <p className="subtitle">
          InventoryRecipt 품목 리스트에 <b>있는</b> 품목 행만 남기고, KEC 수불부에서
          리스트에 <b>없는</b> 품목 행을 삭제합니다. 원본 서식·헤더·합계행은 유지하며
          품목코드 완전일치로 매칭합니다.
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 14,
          marginBottom: 20,
          maxWidth: 640,
        }}
      >
        <div>
          <div style={{ fontSize: 13, color: "#64748b", marginBottom: 6 }}>
            ① 품목 리스트 (InventoryRecipt_excel, .xls/.xlsx)
          </div>
          {pickerBtn("📥 품목 리스트 선택", invFile, invRef, setInvFile)}
        </div>

        <div>
          <div style={{ fontSize: 13, color: "#64748b", marginBottom: 6 }}>
            ② KEC 수불부 (.xlsx)
          </div>
          {pickerBtn("📥 수불부 선택", subFile, subRef, setSubFile)}
        </div>

        <div>
          <button
            onClick={run}
            disabled={loading || !invFile || !subFile}
            style={{
              padding: "12px 24px",
              background: loading || !invFile || !subFile ? "#94a3b8" : "#3b82f6",
              color: "white",
              border: 0,
              borderRadius: 6,
              cursor: loading || !invFile || !subFile ? "default" : "pointer",
              fontWeight: 600,
            }}
          >
            {loading ? "처리 중..." : "🔎 필터 실행 → 다운로드"}
          </button>
        </div>
      </div>

      {result && (
        <div
          style={{
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            background: "white",
            padding: 18,
            maxWidth: 640,
          }}
        >
          <div style={{ fontSize: 14, marginBottom: 10, color: "#334155" }}>
            ✅ 필터링 완료 — 결과 파일이 다운로드되었습니다.
          </div>
          <div style={{ display: "flex", gap: 18, fontSize: 13, color: "#334155", flexWrap: "wrap" }}>
            <span>품목 리스트 <b>{result.inventory_items?.toLocaleString()}</b>종</span>
            <span style={{ color: "#10b981" }}>유지 <b>{result.kept?.toLocaleString()}</b>행</span>
            <span style={{ color: "#dc2626" }}>삭제 <b>{result.deleted?.toLocaleString()}</b>행</span>
            <span style={{ color: "#94a3b8" }}>
              품목열 {result.descrip_col} · 데이터 시작 {result.start_row}행
            </span>
          </div>
          {result.deleted_items?.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary style={{ cursor: "pointer", fontSize: 13, color: "#64748b" }}>
                삭제된 품목 보기 (최대 50종)
              </summary>
              <div
                style={{
                  marginTop: 8,
                  fontSize: 12,
                  color: "#475569",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 6,
                }}
              >
                {result.deleted_items.map((x, i) => (
                  <span
                    key={i}
                    style={{
                      background: "#f1f5f9",
                      borderRadius: 4,
                      padding: "2px 8px",
                      fontFamily: "monospace",
                    }}
                  >
                    {x}
                  </span>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default SubulFilter;
