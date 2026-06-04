import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function MicrochipBuildSheet() {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef();

  const handleFiles = (fl) => {
    if (!fl || !fl.length) return;
    const f = fl[0];
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) {
      setError("xlsx 파일만 가능"); return;
    }
    setFile(f); setInfo(null); setError(null);
  };

  const submit = async () => {
    setError(null); setInfo(null);
    if (!file) return setError("파일 선택 필요");
    const fd = new FormData();
    fd.append("file", file);
    setLoading(true);
    try {
      const res = await axios.post(`${API_URL}/api/microchip-match/build-sheet`, fd, {
        responseType: "blob", timeout: 300000,
      });
      const ctype = res.headers["content-type"] || "";
      if (ctype.includes("json")) {
        const text = await res.data.text();
        setError(JSON.parse(text).error || "실패");
        return;
      }
      const cd = res.headers["content-disposition"] || "";
      let fname = "result_매칭완료.xlsx";
      const m = cd.match(/filename\*=UTF-8''([^;]+)/i);
      if (m) { try { fname = decodeURIComponent(m[1]); } catch {} }
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url; a.download = fname; a.click();
      window.URL.revokeObjectURL(url);
      setInfo({ count: res.headers["x-records-count"], filename: fname });
    } catch (e) {
      let msg = e.message;
      if (e.response?.data instanceof Blob) { try { msg = await e.response.data.text(); } catch {} }
      setError("실패: " + msg);
    } finally { setLoading(false); }
  };

  return (
    <div>
      <div className="page-header">
        <h1>📥 Microchip 출고기준 시트 생성</h1>
        <p className="subtitle">
          백록 + 출고내역 + FAB2 시트가 있는 엑셀을 업로드 → <code>출고기준(백록매칭)</code> 시트가 추가된 파일 다운로드.
          원본 시트는 그대로 보존.
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ background: "white", padding: 18, borderRadius: 8, border: "1px solid #e2e8f0", marginBottom: 16 }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xlsm" style={{ display: "none" }}
          onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }} />
        <div
          onClick={() => fileRef.current.click()}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          style={{
            border: `2px dashed ${dragOver ? "#3b82f6" : (file ? "#10b981" : "#cbd5e1")}`,
            borderRadius: 8, padding: 22, cursor: "pointer", textAlign: "center",
            background: dragOver ? "#dbeafe" : (file ? "#f0fdf4" : "#fafbfc"), fontSize: 13,
          }}>
          {file ? <><b style={{ color: "#059669" }}>📄 {file.name}</b><div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>다른 파일은 클릭/드래그</div></>
                : "엑셀 파일 드래그/클릭 (.xlsx)"}
        </div>
        <div style={{ marginTop: 14, textAlign: "right" }}>
          <button onClick={submit} disabled={loading || !file}
            style={{
              padding: "10px 22px",
              background: loading || !file ? "#94a3b8" : "#10b981",
              color: "white", border: 0, borderRadius: 6,
              cursor: loading || !file ? "not-allowed" : "pointer", fontWeight: 700, fontSize: 14,
            }}>
            {loading ? "🔧 시트 생성 중..." : "🔧 시트 생성 + 다운로드"}
          </button>
        </div>
      </div>

      {info && (
        <div style={{ background: "#f0fdf4", border: "1px solid #86efac", padding: 14, borderRadius: 8, fontSize: 13 }}>
          ✅ <b>{info.filename}</b> 다운로드 완료<br />
          <span style={{ color: "#64748b" }}>총 {info.count}건이 출고기준(백록매칭) 시트에 기록됨</span>
        </div>
      )}

      <div style={{ marginTop: 16, fontSize: 12, color: "#64748b", padding: 10, background: "#f8fafc", borderRadius: 4 }}>
        💡 입력 파일에 필요한 시트:<br />
        - <code>백록260324</code> (또는 백록으로 시작하는 시트, 피벗 제외)<br />
        - <code>출고내역</code><br />
        - <code>FAB2</code> (선택, 있으면 PCN 정보 포함)
      </div>
    </div>
  );
}

export default MicrochipBuildSheet;
