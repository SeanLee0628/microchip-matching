import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function MicrochipAiMatch() {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [maxAiRows, setMaxAiRows] = useState(20);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef();

  const handleFiles = (fl) => {
    if (!fl || !fl.length) return;
    const f = fl[0];
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) {
      setError("xlsx 파일만 가능"); return;
    }
    setFile(f); setResult(null); setError(null);
  };

  const submit = async () => {
    setError(null); setResult(null);
    if (!file) return setError("파일 선택 필요");
    const fd = new FormData();
    fd.append("file", file);
    setLoading(true);
    try {
      const res = await axios.post(
        `${API_URL}/api/microchip-match/ai-suggest?max_ai_rows=${maxAiRows}`,
        fd, { timeout: 600000 }
      );
      if (res.data.error) setError(res.data.error + (res.data.trace ? "\n" + res.data.trace : ""));
      else setResult(res.data);
    } catch (e) {
      setError("실패: " + (e.response?.data?.detail || e.message));
    } finally { setLoading(false); }
  };

  return (
    <div>
      <div className="page-header">
        <h1>Microchip AI Agent</h1>
        <p className="subtitle">
          1단계: 정확 매칭(결정론) → 2단계: rapidfuzz 후보 좁힘 → 3단계: Claude AI Agent가 도구 호출하며 최종 판단
        </p>
      </div>

      {error && <div className="error-banner" style={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 12 }}>{error}</div>}

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
            borderRadius: 8, padding: 18, cursor: "pointer", textAlign: "center",
            background: dragOver ? "#dbeafe" : (file ? "#f0fdf4" : "#fafbfc"), fontSize: 13,
          }}>
          {file ? `📄 ${file.name}` : "마이크로칩 매칭 엑셀 (출고내역 + 백록 시트 포함) 드래그/클릭"}
        </div>
        <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center" }}>
          <label style={{ fontSize: 12, color: "#475569" }}>
            AI 분석 최대 행 수:
            <input type="number" value={maxAiRows} onChange={(e) => setMaxAiRows(Number(e.target.value))}
              min="1" max="100" style={{ marginLeft: 6, width: 60, padding: "3px 6px", border: "1px solid #cbd5e1", borderRadius: 3 }} />
            <span style={{ color: "#94a3b8", marginLeft: 4 }}>(많을수록 비용↑/시간↑)</span>
          </label>
          <div style={{ flex: 1 }} />
          <button onClick={submit} disabled={loading || !file}
            style={{
              padding: "10px 22px",
              background: loading || !file ? "#94a3b8" : "#1d4ed8",
              color: "white", border: 0, borderRadius: 6,
              cursor: loading || !file ? "not-allowed" : "pointer", fontWeight: 700, fontSize: 14,
            }}>
            {loading ? "🤖 AI Agent 분석 중..." : "🤖 하이브리드 매칭 실행"}
          </button>
        </div>
      </div>

      {result && (
        <div style={{ background: "white", padding: 18, borderRadius: 8, border: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
            <Stat label="출고내역 행" v={result.stats.shipment_total} c="#64748b" />
            <Stat label="백록 행" v={result.stats.backlog_total} c="#64748b" />
            <Stat label="✅ 정확 매칭" v={result.stats.exact_matched} c="#10b981" />
            <Stat label="❌ 매칭 실패 (출고)" v={result.stats.unmatched_ship} c="#dc2626" />
            <Stat label="🤖 AI 분석" v={result.stats.ai_processed} c="#3b82f6" />
            <Stat label="⏭️ AI 스킵" v={result.stats.ai_skipped} c="#94a3b8" />
          </div>

          <div style={{ background: "#f1f5f9", padding: 10, borderRadius: 6, marginBottom: 14, fontSize: 12 }}>
            ⏱️ 파싱 {result.timings.parse}s · 정확매칭 {result.timings.exact}s · AI Agent {result.timings.ai_agent}s
          </div>

          {result.ai_results?.length > 0 && (
            <div>
              <h3 style={{ fontSize: 15, marginBottom: 10 }}>🤖 AI Agent 분석 결과 ({result.ai_results.length}건)</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {result.ai_results.map((r, i) => <AiResultCard key={i} r={r} />)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AiResultCard({ r }) {
  const conf = r.ai.confidence ?? 0;
  const matched = !!r.ai.matched_mix;
  const color = !matched ? "#dc2626" : conf >= 95 ? "#10b981" : conf >= 80 ? "#3b82f6" : "#f59e0b";
  const bg = !matched ? "#fef2f2" : conf >= 95 ? "#f0fdf4" : conf >= 80 ? "#eff6ff" : "#fffbeb";
  const label = !matched ? "❌ 매칭 없음" : conf >= 95 ? "✅ 자동 적용 가능" : conf >= 80 ? "🔵 사람 확인 권장" : "⚠️ 약한 매칭";
  return (
    <div style={{
      border: `1px solid ${color}40`, borderLeft: `4px solid ${color}`,
      borderRadius: 6, padding: 12, background: bg,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div style={{ fontSize: 13 }}>
          <b style={{ color }}>{label}</b>
          <span style={{ marginLeft: 8, fontSize: 11, color: "#64748b" }}>신뢰도 {conf}</span>
        </div>
      </div>
      <div style={{ fontSize: 12, color: "#475569", marginBottom: 4 }}>
        <b>출고내역:</b> {r.ship_row.mix} · {r.ship_row.customer} · {r.ship_row.part}
        {r.ship_row.end && <> · END {r.ship_row.end}</>}
      </div>
      {matched && (
        <div style={{ fontSize: 12, color: "#10b981" }}>
          <b>→ 백록 매칭:</b> <code style={{ background: "#fff", padding: "1px 6px", borderRadius: 3 }}>{r.ai.matched_mix}</code>
        </div>
      )}
      <div style={{ fontSize: 12, color: "#64748b", marginTop: 6, fontStyle: "italic" }}>
        💬 {r.ai.reason}
      </div>
      {r.agent_log?.length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary style={{ cursor: "pointer", fontSize: 11, color: "#94a3b8" }}>
            🔧 Agent 도구 호출 로그 ({r.agent_log.length}회)
          </summary>
          <pre style={{ fontSize: 10, background: "#0f172a", color: "#e2e8f0", padding: 8, borderRadius: 4, overflow: "auto", maxHeight: 200, marginTop: 4 }}>
            {JSON.stringify(r.agent_log, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function Stat({ label, v, c }) {
  return (
    <div style={{ flex: 1, minWidth: 110, padding: 8, background: "#f8fafc", borderRadius: 4, borderLeft: `3px solid ${c}` }}>
      <div style={{ fontSize: 10, color: "#64748b" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: c }}>{v}</div>
    </div>
  );
}

export default MicrochipAiMatch;
