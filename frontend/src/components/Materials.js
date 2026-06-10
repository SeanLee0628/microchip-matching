import React, { useState } from "react";
import axios from "axios";

// 출고 자동등록(xlwings)은 "지금 PC에서 열려 있는 Excel"을 제어하는 로컬 전용 기능이라
// 항상 사용자 PC에서 도는 로컬 백엔드(main.py, 포트 8001)로 요청해야 한다.
// 클라우드(main_aws.py)엔 /api/jaejae 라우트가 없어서 SPA 폴백(GET) 때문에 POST가 405로 깨진다.
// REACT_APP_JAEJAE_API 로 포트/호스트 덮어쓰기 가능.
const API_URL = process.env.REACT_APP_JAEJAE_API || "http://localhost:8001";

function Materials() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [autoSave, setAutoSave] = useState(false);
  const [undoneRows, setUndoneRows] = useState(new Set());
  const [undoBusy, setUndoBusy] = useState(false);

  const undoOne = async (smRow, snapshot, bookName) => {
    if (!snapshot) return;
    if (!window.confirm(`SM 행 ${smRow}의 처리를 되돌립니까?\n(가장 최근 처리부터 순서대로 되돌리는 게 안전합니다)`)) return;
    setUndoBusy(true);
    try {
      const res = await axios.post(`${API_URL}/api/jaejae/undo-row`, { snapshot, book: bookName });
      if (res.data.error) {
        alert("되돌리기 실패: " + res.data.error);
      } else {
        setUndoneRows((prev) => new Set([...prev, smRow]));
        alert(`✅ 되돌리기 완료: ${res.data.summary || "OK"}`);
      }
    } catch (e) {
      const hint = !e.response
        ? "로컬 백엔드(main.py, 포트 8001)에 연결할 수 없습니다. 이 PC에서 서버를 실행했는지 확인하세요."
        : e.message;
      alert("되돌리기 오류: " + hint);
    } finally { setUndoBusy(false); }
  };

  const directProcess = async () => {
    setError(null); setResult(null); setUndoneRows(new Set()); setLoading(true);
    try {
      const res = await axios.post(`${API_URL}/api/jaejae/process-direct`, null, {
        params: { save: autoSave },
        timeout: 600000,
      });
      if (res.data.error) {
        setError(res.data.error + (res.data.trace ? "\n\n" + res.data.trace : ""));
      } else {
        setResult({ ...res.data, mode: "direct" });
      }
    } catch (e) {
      const isConn = !e.response; // 응답 자체가 없음 = 로컬 백엔드 미실행/연결 거부
      const hint = isConn
        ? "로컬 백엔드(main.py, 포트 8001)에 연결할 수 없습니다. 이 PC에서 '자재_AI자동등록' 서버(main.py)를 먼저 실행하고, 처리할 Excel을 열어둔 상태인지 확인하세요."
        : (e.response?.data?.detail || e.message);
      setError("실패: " + hint);
    } finally { setLoading(false); }
  };

  return (
    <div>
      <div className="page-header">
        <h1>자재 출고 자동등록 (AI Agent + xlwings)</h1>
        <p className="subtitle">
          현재 PC에서 열려 있는 Excel을 직접 수정합니다 (xlwings).
          shipping management의 가장 최근 날짜 미처리 행만 처리.
        </p>
      </div>

      {error && (
        <div className="error-banner" style={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 12 }}>
          {error}
        </div>
      )}

      <div style={{ background: "#dbeafe", border: "1px solid #93c5fd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h3 style={{ marginTop: 0, marginBottom: 8, fontSize: 15, color: "#1e40af" }}>🥇 직접 수정 모드 (권장)</h3>
        <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, color: "#1e40af" }}>
          <li><b>Excel</b>에서 작업 파일을 열어둡니다 (예: APR_xxx.xlsx)</li>
          <li><code>shipping management</code> 시트에 신규 출고건을 입력 (저장 안 해도 됨)</li>
          <li>아래 [🤖 활성 Excel 직접 수정] 버튼 클릭</li>
          <li>Excel 화면에 셀 입력이 즉시 반영됨 + 콘솔에 처리 요약</li>
          <li>Excel에서 <kbd>Ctrl+S</kbd> 로 저장 (또는 자동 저장 옵션 체크)</li>
        </ol>
        <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
          <label style={{ fontSize: 13, color: "#1e40af" }}>
            <input type="checkbox" checked={autoSave} onChange={(e) => setAutoSave(e.target.checked)} />
            {" "}처리 후 자동 저장
          </label>
          <div style={{ flex: 1 }} />
          <button onClick={directProcess} disabled={loading}
            style={{
              padding: "12px 24px",
              background: loading ? "#94a3b8" : "#1d4ed8",
              color: "white", border: 0, borderRadius: 6,
              cursor: loading ? "wait" : "pointer", fontWeight: 700, fontSize: 14,
            }}>
            {loading ? "🤖 처리 중..." : "🤖 활성 Excel 직접 수정"}
          </button>
        </div>
      </div>


      {result && (
        <div style={{ background: "white", padding: 20, borderRadius: 8, border: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
            <Stat label={result.mode === "direct" ? "워크북" : "미처리"}
                  value={result.mode === "direct" ? result.book : result.pending_count} color="#64748b" />
            <Stat label="처리 완료" value={result.processed_count} color="#10b981" />
            <Stat label="스킵/실패" value={result.skipped_count} color="#dc2626" />
            {result.elapsed != null && <Stat label="소요 (초)" value={result.elapsed} color="#3b82f6" />}
          </div>

          <div style={{ background: "#f1f5f9", padding: 12, borderRadius: 6, marginBottom: 12, fontSize: 13, whiteSpace: "pre-wrap" }}>
            {result.summary}
          </div>

          {result.backup_path && !String(result.backup_path).startsWith("FAIL") && (
            <div style={{ background: "#ecfeff", border: "1px solid #67e8f9", padding: 10, borderRadius: 6, marginBottom: 12, fontSize: 12, color: "#0e7490" }}>
              💾 백업 파일: <code style={{ fontFamily: "monospace" }}>{result.backup_path}</code>
              <span style={{ color: "#64748b", marginLeft: 8 }}>(전체 원복 시 이 파일로 교체)</span>
            </div>
          )}

          {(result.results?.length > 0 || result.row_results?.length > 0) && (
            <div>
              <h4 style={{ fontSize: 14, marginBottom: 8 }}>처리 내역 (가장 최근 처리부터 ↩️ 되돌리는 게 안전)</h4>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {(result.results || result.row_results).slice().reverse().map((r, i) => (
                  <RowCard
                    key={i}
                    r={r}
                    mode={result.mode}
                    undone={undoneRows.has(r.sm_row)}
                    bookName={result.book}
                    onUndo={undoOne}
                    busy={undoBusy}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RowCard({ r, mode, undone, bookName, onUndo, busy }) {
  const isOk = r.status === "ok" || r.status === "processed";
  const isAlready = r.status === "already_processed";
  const color = undone ? "#94a3b8" : (isOk ? "#10b981" : (isAlready ? "#3b82f6" : "#dc2626"));
  const bg = undone ? "#f1f5f9" : (isOk ? "#f0fdf4" : (isAlready ? "#eff6ff" : "#fef2f2"));
  const icon = undone ? "↩️" : (isOk ? "✅" : (isAlready ? "ℹ️" : "❌"));
  const canUndo = isOk && mode === "direct" && r.snapshot && !undone;
  return (
    <div style={{
      border: `1px solid ${color}40`, borderLeft: `4px solid ${color}`,
      borderRadius: 4, padding: 8, fontSize: 13, background: bg,
      opacity: undone ? 0.6 : 1,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ flex: 1 }}>
          <b style={{ color }}>{icon}</b>{" "}
          <span style={{ fontWeight: 600, textDecoration: undone ? "line-through" : "none" }}>{r.part}</span>{" "}
          × <b>{Number(r.qty).toLocaleString()}</b>
          {r.team && <> · <b>{r.team}</b></>}
          {r.customer && <> · {r.customer}</>}
          <span style={{ color: "#94a3b8", marginLeft: 8 }}>(SM 행 {r.sm_row})</span>
          {undone && <span style={{ marginLeft: 8, color: "#64748b", fontSize: 11 }}>(되돌려짐)</span>}
          {isAlready && <span style={{ marginLeft: 8, color: "#3b82f6", fontSize: 11 }}>(이전 처리됨)</span>}
        </div>
        {canUndo && (
          <button
            onClick={() => onUndo(r.sm_row, r.snapshot, bookName)}
            disabled={busy}
            style={{
              padding: "4px 10px", background: "white", color: "#dc2626",
              border: "1px solid #fca5a5", borderRadius: 4,
              cursor: busy ? "wait" : "pointer", fontSize: 11, fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            ↩️ 이 행 되돌리기
          </button>
        )}
      </div>
      {isOk && r.snapshot && !undone && <ConciseSummary snapshot={r.snapshot} />}
      {r.error && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 4 }}>사유: {r.error}</div>}
    </div>
  );
}

function ConciseSummary({ snapshot }) {
  // SM 마커는 표시 제외
  const writes = (snapshot.writes || []).filter((w) => w.sheet !== "shipping management");
  // 시트별 그룹화 (같은 시트의 같은 행은 묶음)
  const groups = {};
  writes.forEach((w) => {
    const k = `${w.sheet}|${w.row}`;
    if (!groups[k]) groups[k] = { sheet: w.sheet, row: w.row, cells: [] };
    groups[k].cells.push(w);
  });
  const groupList = Object.values(groups);

  return (
    <div style={{ marginTop: 6, paddingLeft: 4 }}>
      {groupList.map((g, i) => {
        const isDC = g.sheet.includes("DATECODE");
        const isApr = g.sheet.toLowerCase().includes("inventory");
        // DATECODE: 출고 컬럼(10~16) 위주로 보여주기, 입고 복사(1~9)는 "신규 행" 표시로 압축
        if (isDC) {
          const outs = g.cells.filter((c) => c.col >= 10);
          const inputs = g.cells.filter((c) => c.col < 10);
          return (
            <div key={i} style={{ fontSize: 12, color: "#475569", marginBottom: 3 }}>
              📋 <b>{g.sheet}</b> {g.row}행
              {inputs.length > 0 && <span style={{ color: "#94a3b8" }}> (신규 행 + 입고정보 복사)</span>}
              <div style={{ marginLeft: 18, fontSize: 11, color: "#64748b", marginTop: 2 }}>
                {outs.map((c, j) => (
                  <span key={j} style={{ marginRight: 10 }}>
                    <code style={{ color: "#3b82f6" }}>{c.cell}</code>={fmtCell(c.after)}
                  </span>
                ))}
              </div>
            </div>
          );
        }
        if (isApr) {
          const c = g.cells[0];
          return (
            <div key={i} style={{ fontSize: 12, color: "#475569", marginBottom: 3 }}>
              📦 <b>{g.sheet}</b> <code style={{ color: "#3b82f6" }}>{c.cell}</code>:
              {" "}{fmtCell(c.before)} → <b style={{ color: "#10b981" }}>{fmtCell(c.after)}</b>
              {c.note && <span style={{ color: "#94a3b8" }}> ({c.note})</span>}
            </div>
          );
        }
        // 그 외 시트
        return (
          <div key={i} style={{ fontSize: 12, color: "#475569", marginBottom: 3 }}>
            📄 <b>{g.sheet}</b> {g.row}행: {g.cells.length}셀 변경
          </div>
        );
      })}
      {snapshot.writes?.length > 0 && (
        <details style={{ marginTop: 4 }}>
          <summary style={{ cursor: "pointer", fontSize: 10, color: "#94a3b8" }}>
            전체 셀 보기 ({snapshot.writes.filter((w) => w.sheet !== "shipping management").length}셀)
          </summary>
          <div style={{ overflowX: "auto", marginTop: 4 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
              <thead>
                <tr style={{ background: "#f1f5f9" }}>
                  <th style={{ padding: 3, textAlign: "left" }}>시트</th>
                  <th style={{ padding: 3, textAlign: "left" }}>셀</th>
                  <th style={{ padding: 3, textAlign: "left" }}>이전</th>
                  <th style={{ padding: 3, textAlign: "left" }}>입력</th>
                  <th style={{ padding: 3, textAlign: "left" }}>설명</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.writes
                  .filter((w) => w.sheet !== "shipping management")
                  .map((w, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #f8fafc" }}>
                      <td style={{ padding: 2 }}>{w.sheet}</td>
                      <td style={{ padding: 2, color: "#3b82f6" }}>{w.cell}</td>
                      <td style={{ padding: 2, color: "#94a3b8" }}>{fmtCell(w.before)}</td>
                      <td style={{ padding: 2, color: "#10b981" }}>{fmtCell(w.after)}</td>
                      <td style={{ padding: 2, color: "#64748b" }}>{w.note || ""}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

function fmtCell(v) {
  if (v == null) return "-";
  if (typeof v === "object") {
    if (v.__dt__) return String(v.__dt__).slice(0, 19).replace("T", " ");
    if (v.__d__) return v.__d__;
    return JSON.stringify(v);
  }
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}

function Stat({ label, value, color }) {
  return (
    <div style={{ flex: 1, minWidth: 120, padding: 10, background: "#f8fafc", borderRadius: 6, borderLeft: `4px solid ${color}` }}>
      <div style={{ fontSize: 11, color: "#64748b" }}>{label}</div>
      <div style={{ fontSize: value && String(value).length > 12 ? 14 : 22, fontWeight: 700, color, wordBreak: "break-all" }}>{value}</div>
    </div>
  );
}

export default Materials;
