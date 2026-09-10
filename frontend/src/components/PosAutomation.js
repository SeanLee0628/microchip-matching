import React, { useState, useRef } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API_URL = process.env.REACT_APP_API_URL || "";

function Drop({ label, hint, file, onSelect, color, required }) {
  const ref = useRef();
  const [over, setOver] = useState(false);
  return (
    <div
      onClick={() => ref.current.click()}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files?.[0]) onSelect(e.dataTransfer.files[0]); }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setOver(false); }}
      style={{
        border: `2px dashed ${over || file ? color : "#cbd5e1"}`, borderRadius: 8,
        padding: 16, background: over ? `${color}15` : file ? `${color}08` : "#fafbfc",
        minHeight: 96, cursor: "pointer", transition: "all .15s",
      }}
    >
      <input ref={ref} type="file" accept=".xlsx,.xlsm" style={{ display: "none" }}
        onChange={(e) => { if (e.target.files[0]) onSelect(e.target.files[0]); e.target.value = ""; }} />
      <div style={{ fontWeight: 700, color, fontSize: 14 }}>
        {label}{!required && <span style={{ fontSize: 11, color: "#94a3b8", fontWeight: 500 }}> (선택)</span>}
      </div>
      <div style={{ fontSize: 11.5, color: "#64748b", marginTop: 4, lineHeight: 1.4 }}>{hint}</div>
      {file && (
        <div style={{ marginTop: 10, padding: "5px 9px", background: "white", border: "1px solid #e2e8f0",
          borderRadius: 4, fontSize: 11.5, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>📄 {file.name}</span>
          <button onClick={(e) => { e.stopPropagation(); onSelect(null); }}
            style={{ background: "transparent", border: 0, color: "#dc2626", cursor: "pointer", fontSize: 15 }}>×</button>
        </div>
      )}
    </div>
  );
}

function firstDay(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`; }
function lastDay(d) {
  const e = new Date(d.getFullYear(), d.getMonth() + 1, 0);
  return `${e.getFullYear()}-${String(e.getMonth() + 1).padStart(2, "0")}-${String(e.getDate()).padStart(2, "0")}`;
}

function PosAutomation() {
  const prev = new Date(); prev.setMonth(prev.getMonth() - 1);
  const [raw, setRaw] = useState(null);
  const [crm, setCrm] = useState(null);
  const [addr, setAddr] = useState(null);
  const [start, setStart] = useState(firstDay(prev));
  const [end, setEnd] = useState(lastDay(prev));
  const [mode, setMode] = useState("draft");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const runIt = async () => {
    if (!raw) { setError("RAWDATA 파일을 선택하세요."); return; }
    if (!start || !end) { setError("대상 기간을 지정하세요."); return; }
    setLoading(true); setError(null); setResult(null);
    const fd = new FormData();
    fd.append("rawdata", raw);
    if (crm) fd.append("crm", crm);
    if (addr) fd.append("address_master", addr);
    fd.append("start_date", start);
    fd.append("end_date", end);
    fd.append("mode", mode);
    try {
      const res = await axios.post(`${API_URL}/api/pos-auto/run`, fd, { responseType: "blob" });
      const ctype = res.headers?.["content-type"] || "";
      if (ctype.includes("json") || res.data.size < 2048) {
        try {
          const obj = JSON.parse(await res.data.text());
          if (obj.error) { setError(obj.error); return; }
        } catch (_) { /* 정상 파일 */ }
      }
      let meta = null;
      const b64 = res.headers?.["x-result-json-b64"];
      if (b64) {
        try { meta = JSON.parse(decodeURIComponent(escape(atob(b64)))); } catch (_) { meta = null; }
      }
      const fname = `Microchip_POS_Result_${start.slice(0, 7).replace("-", "")}.xlsx`;
      saveAs(res.data, fname);
      setResult(meta || { summary: [], counts: {} });
    } catch (e) {
      let msg = e.message;
      if (e.response?.data instanceof Blob) {
        try { msg = JSON.parse(await e.response.data.text()).error || msg; } catch (_) {}
      }
      setError("실패: " + msg);
    } finally { setLoading(false); }
  };

  const val = (k) => result?.summary?.find((r) => r["항목"] === k)?.["값"];
  const submitOk = val("최종 제출 가능 여부") === "YES";

  return (
    <div>
      <div className="page-header">
        <h1>POS Report 자동 생성 (5실)</h1>
        <p style={{ color: "#64748b", fontSize: 13, marginTop: 6 }}>
          RAWDATA에서 QTN·ASD 유효성을 검사해 적용 단가를 정하고, 마이크로칩 제출용 27열과
          검증·예외·QTN 사용원장을 한 파일로 만듭니다. <b>원본 파일은 수정하지 않습니다.</b>
        </p>
      </div>

      {error && (
        <div style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c",
          padding: "10px 14px", borderRadius: 6, fontSize: 13, marginBottom: 14 }}>⚠ {error}</div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
        <Drop label="RAWDATA" required color="#0ea5e9" file={raw} onSelect={setRaw}
          hint="출고이력 · 단가2(QTN) · 단가3(ASD) · 업체코드 매칭 시트가 있는 파일" />
        <Drop label="CRM 고객사" color="#8b5cf6" file={crm} onSelect={setCrm}
          hint="우편번호·주소 원천. 이름 정확일치로만 연결합니다" />
        <Drop label="주소 마스터" color="#10b981" file={addr} onSelect={setAddr}
          hint="지난 실행의 Address_Master_Needed 시트를 채워 올리면 반영됩니다" />
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 18 }}>
        <label style={{ fontSize: 12, color: "#475569" }}>
          대상 시작일<br />
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
            style={{ padding: "7px 10px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: 13 }} />
        </label>
        <label style={{ fontSize: 12, color: "#475569" }}>
          대상 종료일<br />
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
            style={{ padding: "7px 10px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: 13 }} />
        </label>
        <label style={{ fontSize: 12, color: "#475569" }}>
          모드<br />
          <select value={mode} onChange={(e) => setMode(e.target.value)}
            style={{ padding: "7px 10px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: 13 }}>
            <option value="draft">검토용 (draft)</option>
            <option value="final">제출본 (final) — 예외 0건일 때만</option>
          </select>
        </label>
        <button onClick={runIt} disabled={loading || !raw}
          style={{ padding: "9px 22px", background: loading || !raw ? "#cbd5e1" : "#c43a3a",
            color: "white", border: 0, borderRadius: 6, fontWeight: 700, fontSize: 13,
            cursor: loading || !raw ? "not-allowed" : "pointer" }}>
          {loading ? "생성 중…" : "POS 생성"}
        </button>
      </div>

      {result && (
        <>
          <div style={{
            padding: "12px 16px", borderRadius: 6, marginBottom: 14, fontSize: 13.5, fontWeight: 700,
            background: submitOk ? "#ecfdf5" : "#fffbeb",
            border: `1px solid ${submitOk ? "#a7f3d0" : "#fde68a"}`,
            color: submitOk ? "#065f46" : "#92400e",
          }}>
            {submitOk ? "✅ 제출 가능 — 모든 검증 통과" : "⚠ 제출 불가 — 아래 예외를 해소한 뒤 final 로 다시 실행하세요"}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
            {[["POS 생성 행", val("POS 생성 행 수")], ["정상 처리", val("정상 처리 행 수")],
              ["예외", val("예외 행 수")], ["원본 출고", val("원본 출고 행 수")]].map(([k, v]) => (
              <div key={k} style={{ background: "white", border: "1px solid #e2e8f0", borderRadius: 6, padding: "10px 14px" }}>
                <div style={{ fontSize: 11, color: "#64748b", fontWeight: 600 }}>{k}</div>
                <div style={{ fontSize: 22, fontWeight: 800, marginTop: 4 }}>{v ?? "—"}</div>
              </div>
            ))}
          </div>

          <div style={{ background: "white", border: "1px solid #e2e8f0", borderRadius: 6, overflow: "hidden" }}>
            <div style={{ padding: "10px 14px", borderBottom: "1px solid #e2e8f0", fontWeight: 700, fontSize: 13 }}>
              검증 요약 <span style={{ fontWeight: 400, color: "#64748b", fontSize: 12 }}>· 파일은 이미 다운로드되었습니다</span>
            </div>
            <div style={{ maxHeight: 420, overflow: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <tbody>
                  {(result.summary || []).map((r, i) => (
                    <tr key={i} style={{ borderTop: "1px solid #f1f5f9",
                      background: String(r["항목"]).startsWith("[예외코드]") ? "#fffdf5" : "white" }}>
                      <td style={{ padding: "6px 14px", color: "#475569" }}>{r["항목"]}</td>
                      <td style={{ padding: "6px 14px", textAlign: "right", fontWeight: 600,
                        fontVariantNumeric: "tabular-nums" }}>{String(r["값"])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ marginTop: 12, fontSize: 12, color: "#64748b", lineHeight: 1.7 }}>
            내려받은 파일의 시트: <b>POS_Report</b>(제출용 27열) · <b>Processing_Result</b>(행별 계산 근거) ·
            <b> Exceptions</b> · <b>Customer_Match_Review</b> · <b>Validation_Summary</b> ·
            <b> QTN_Usage_Ledger</b> · <b>POS_Field_Mapping</b> · <b>Address_Master_Needed</b> · <b>Run_Log</b>
          </div>
        </>
      )}
    </div>
  );
}

export default PosAutomation;
