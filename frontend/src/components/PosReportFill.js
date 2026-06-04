import React, { useState, useRef } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API_URL = process.env.REACT_APP_API_URL || "";

function DropZone({ label, hint, file, onSelect, color }) {
  const inputRef = useRef();
  const [isOver, setIsOver] = useState(false);

  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      onSelect(e.dataTransfer.files[0]);
    }
  };

  return (
    <div
      onClick={() => inputRef.current.click()}
      onDrop={onDrop}
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsOver(true); }}
      onDragEnter={(e) => { e.preventDefault(); setIsOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setIsOver(false); }}
      style={{
        border: `2px dashed ${isOver ? color : file ? color : "#cbd5e1"}`,
        borderRadius: 8,
        padding: 20,
        background: isOver ? `${color}15` : file ? `${color}08` : "#fafbfc",
        minHeight: 120,
        cursor: "pointer",
        transition: "all 0.15s",
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls"
        style={{ display: "none" }}
        onChange={(e) => { if (e.target.files[0]) onSelect(e.target.files[0]); e.target.value = ""; }}
      />
      <div style={{ textAlign: "center" }}>
        <div style={{ fontWeight: 700, color, fontSize: 15 }}>{label}</div>
        <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>{hint}</div>
      </div>
      {file && (
        <div style={{ marginTop: 12, padding: "6px 10px", background: "white", border: "1px solid #e2e8f0", borderRadius: 4, fontSize: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>📄 {file.name}</span>
          <button
            onClick={(e) => { e.stopPropagation(); onSelect(null); }}
            style={{ background: "transparent", border: 0, color: "#dc2626", cursor: "pointer", fontSize: 16, padding: "0 4px", lineHeight: 1 }}
            title="제거"
          >×</button>
        </div>
      )}
    </div>
  );
}

function PosReportFill() {
  const [rawFile, setRawFile] = useState(null);
  const [templateFile, setTemplateFile] = useState(null);
  const [codeMappingFile, setCodeMappingFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const handleFill = async () => {
    if (!rawFile || !templateFile) {
      setError("RAW DATA와 템플릿 파일 모두 필요합니다.");
      return;
    }
    setLoading(true);
    setError(null);
    setSuccess(null);
    const fd = new FormData();
    fd.append("raw_data", rawFile);
    fd.append("template", templateFile);
    if (codeMappingFile) fd.append("code_mapping", codeMappingFile);
    try {
      const res = await axios.post(`${API_URL}/api/pos-report/fill`, fd, { responseType: "blob" });
      // JSON 에러를 200으로 반환한 경우 — Blob 안의 JSON 감지
      const ctype = res.headers?.["content-type"] || "";
      if (ctype.includes("json") || res.data.size < 1024) {
        try {
          const txt = await res.data.text();
          const obj = JSON.parse(txt);
          if (obj.error) {
            setError("실패: " + obj.error);
            return;
          }
        } catch (_) { /* JSON 아니면 정상 파일 */ }
      }
      const filled = res.headers?.["x-filled-rows"] || "?";
      const fname = `POS_Report_filled_${new Date().toISOString().slice(0, 10)}.xlsx`;
      saveAs(res.data, fname);
      setSuccess(`자동완성 완료. ${filled}개 행 처리 → 다운로드 시작.`);
    } catch (e) {
      let msg = e.message;
      if (e.response?.data instanceof Blob) {
        try {
          const txt = await e.response.data.text();
          const obj = JSON.parse(txt);
          msg = obj.error || obj.detail || txt;
        } catch (_) { /* ignore */ }
      } else if (e.response?.data?.error) {
        msg = e.response.data.error;
      }
      setError("실패: " + msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>출고내역 자동완성 (5실)</h1>
        <p className="subtitle">RAW DATA + 템플릿 파일 → 출고내역 시트 E~M열 자동 채움</p>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {success && (
        <div style={{ padding: 12, background: "#dcfce7", color: "#166534", borderRadius: 6, marginBottom: 16, fontSize: 13 }}>
          ✅ {success}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
        <DropZone
          label="① RAW DATA"
          hint="단가1·2(QTN)·3(ASD) 시트가 포함된 원본 (필수)"
          file={rawFile}
          onSelect={setRawFile}
          color="#3b82f6"
        />
        <DropZone
          label="② 템플릿 (POS Report)"
          hint="출고내역 시트의 A~D열만 입력된 양식"
          file={templateFile}
          onSelect={setTemplateFile}
          color="#10b981"
        />
        <DropZone
          label="③ 업체코드 매핑 (선택)"
          hint="별도 파일 (예: RAWDATA.xlsx 5220개 전체 매핑) — 미지정 시 ① 사용"
          file={codeMappingFile}
          onSelect={setCodeMappingFile}
          color="#f59e0b"
        />
      </div>

      <div style={{ marginBottom: 20, display: "flex", gap: 12, alignItems: "center" }}>
        <button
          onClick={handleFill}
          disabled={loading || !rawFile || !templateFile}
          style={{
            padding: "12px 24px",
            background: !rawFile || !templateFile ? "#94a3b8" : "#0ea5e9",
            color: "white", border: 0, borderRadius: 6,
            cursor: !rawFile || !templateFile ? "not-allowed" : "pointer",
            fontWeight: 600,
          }}
        >
          {loading ? "처리 중..." : "🔄 자동완성 실행 → 다운로드"}
        </button>
      </div>

    </div>
  );
}

export default PosReportFill;
