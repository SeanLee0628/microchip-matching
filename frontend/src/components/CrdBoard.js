import React, { useState, useRef } from "react";
import axios from "axios";

const API = process.env.REACT_APP_API_URL || "";

const RISK = {
  red:     { label: "위험",   dot: "🔴", color: "#dc2626", bg: "#fef2f2", border: "#fecaca" },
  yellow:  { label: "임박",   dot: "🟡", color: "#d97706", bg: "#fffbeb", border: "#fde68a" },
  green:   { label: "여유",   dot: "🟢", color: "#16a34a", bg: "#f0fdf4", border: "#bbf7d0" },
  unknown: { label: "CRD미상", dot: "⚪", color: "#64748b", bg: "#f8fafc", border: "#e2e8f0" },
};
const ORDER = ["red", "yellow", "green", "unknown"];
const TYPE_LABEL = { OR: "양산", FD: "샘플" };
const fmtQty = (q) => (q == null ? "" : Number(q).toLocaleString());

function CrdBoard() {
  const [mode, setMode] = useState("now");   // "now" 현황 / "change" 변화

  // ── 현황 보기 상태 ──
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [fileObj, setFileObj] = useState(null);
  const [buffer, setBuffer] = useState(7);
  const [riskFilter, setRiskFilter] = useState(null);
  const [typeFilter, setTypeFilter] = useState(null);
  const [fseFilter, setFseFilter] = useState("");
  const [custFilter, setCustFilter] = useState("");
  const fileRef = useRef();

  // ── 변화 보기 상태 ──
  const [prevFile, setPrevFile] = useState(null);
  const [curFile, setCurFile] = useState(null);
  const [cmp, setCmp] = useState(null);
  const [cmpLoading, setCmpLoading] = useState(false);
  const [cmpError, setCmpError] = useState(null);
  const [indefOnly, setIndefOnly] = useState(false);
  const prevRef = useRef();
  const curRef = useRef();

  const doUpload = async (file, buf) => {
    if (!file) return;
    setLoading(true); setError(null);
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await axios.post(`${API}/api/crd-board?buffer_days=${buf}`, fd);
      if (res.data.error) { setError(res.data.error); setData(null); }
      else setData(res.data);
    } catch (err) { setError("업로드 실패: " + err.message); }
    setLoading(false);
  };
  const onPick = (file) => { setFileObj(file); doUpload(file, buffer); };

  const doCompare = async () => {
    if (!prevFile || !curFile) return;
    setCmpLoading(true); setCmpError(null);
    const fd = new FormData();
    fd.append("prev", prevFile); fd.append("current", curFile);
    try {
      const res = await axios.post(`${API}/api/crd-board/compare`, fd);
      if (res.data.error) { setCmpError(res.data.error); setCmp(null); }
      else setCmp(res.data);
    } catch (err) { setCmpError("비교 실패: " + err.message); }
    setCmpLoading(false);
  };

  const fseOptions = [...new Set((data?.board || []).map(c => c.fse).filter(Boolean))].sort();
  const cards = (data?.board || []).filter(c =>
    (!riskFilter || c.risk === riskFilter) &&
    (!typeFilter || c.order_type === typeFilter) &&
    (!fseFilter || c.fse === fseFilter) &&
    (!custFilter || (c.customer || "").toLowerCase().includes(custFilter.toLowerCase()))
  );
  const slipped = (cmp?.slipped || []).filter(c => !indefOnly || c.indefinite);

  const tab = (key, label) => (
    <button onClick={() => setMode(key)}
            style={{ padding: "8px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer",
                     border: "none", borderBottom: `2px solid ${mode === key ? "#0a0e12" : "transparent"}`,
                     background: "none", color: mode === key ? "#0a0e12" : "#94a3b8" }}>
      {label}
    </button>
  );

  return (
    <div style={{ background: "#f7f7f8", minHeight: "100vh", padding: "24px 28px", boxSizing: "border-box" }}>
      <div style={{ maxWidth: 1400, margin: "0 auto" }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, color: "#0a0e12" }}>영업1실 · CRD 신호등 보드</h1>

        <div style={{ display: "flex", gap: 4, borderBottom: "1px solid #e2e8f0", margin: "14px 0 18px" }}>
          {tab("now", "현황 보기")}
          {tab("change", "변화 보기 (MAD 밀림)")}
        </div>

        {/* ───────── 현황 보기 ───────── */}
        {mode === "now" && (<>
          <p style={{ fontSize: 13, color: "#64748b", marginTop: 0 }}>
            Backlog Shipment Report를 올리면 미출하 주문을 <b>자재 가용일(MAD)</b> vs <b>요청일(CRD)</b>로 비교해
            납기 위험을 정렬합니다.
          </p>
          <div style={{ margin: "16px 0", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
                   onChange={(e) => onPick(e.target.files[0])} />
            <button onClick={() => fileRef.current?.click()} disabled={loading}
                    style={{ padding: "9px 18px", background: "#0a0e12", color: "#fff", border: "none",
                             borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
              {loading ? "분석 중…" : "Backlog 리포트 업로드"}
            </button>
            <span style={{ fontSize: 12.5, color: "#475569" }}>
              위험 기준:{" "}
              <input type="number" min={0} value={buffer} onChange={(e) => setBuffer(Number(e.target.value))}
                     style={{ width: 54, padding: "5px 8px", borderRadius: 6, border: "1px solid #e2e8f0", fontSize: 13 }} />
              일 초과
            </span>
            <button onClick={() => doUpload(fileObj, buffer)} disabled={!fileObj || loading}
                    style={{ padding: "7px 12px", background: "#fff", color: "#0a0e12", border: "1px solid #cbd5e1",
                             borderRadius: 7, fontSize: 12.5, cursor: fileObj ? "pointer" : "default" }}>적용</button>
            {data && (
              <span style={{ fontSize: 12, color: "#64748b" }}>
                기준일 {data.today} · 오픈 {data.open_count}건
                {data.shipped_skipped ? ` (출하완료 ${data.shipped_skipped} 제외)` : ""}
                {data.order_types && ` · 양산 ${data.order_types.OR || 0}/샘플 ${data.order_types.FD || 0}`}
              </span>
            )}
          </div>

          {error && <Banner>{error}</Banner>}

          {data?.part_summary?.filter(p => p.red > 0).length > 0 && (
            <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "14px 16px", marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#0a0e12", marginBottom: 8 }}>🔴 위험 집중 부품 Top</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {data.part_summary.filter(p => p.red > 0).map((p, i) => (
                  <div key={i} style={{ fontSize: 12, background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 6, padding: "6px 10px" }}>
                    <b>{p.did}/{p.mpn}</b> · 위험 {p.red}건 · {Number(p.red_qty).toLocaleString()}ea
                  </div>
                ))}
              </div>
            </div>
          )}

          {data && (
            <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap", alignItems: "center" }}>
              {ORDER.map(r => (
                <button key={r} onClick={() => setRiskFilter(riskFilter === r ? null : r)}
                        style={{ padding: "8px 14px", borderRadius: 999, fontSize: 13, fontWeight: 600, cursor: "pointer",
                                 background: RISK[r].bg, color: RISK[r].color,
                                 border: `1.5px solid ${riskFilter === r ? RISK[r].color : RISK[r].border}` }}>
                  {RISK[r].dot} {RISK[r].label} {data.summary?.[r] ?? 0}
                </button>
              ))}
              {["OR", "FD"].map(t => (
                <button key={t} onClick={() => setTypeFilter(typeFilter === t ? null : t)}
                        style={{ padding: "8px 12px", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer",
                                 background: typeFilter === t ? "#0a0e12" : "#fff",
                                 color: typeFilter === t ? "#fff" : "#64748b", border: "1px solid #e2e8f0" }}>
                  {TYPE_LABEL[t]}
                </button>
              ))}
              {fseOptions.length > 0 && (
                <select value={fseFilter} onChange={(e) => setFseFilter(e.target.value)}
                        style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 13 }}>
                  <option value="">담당(FSE) 전체</option>
                  {fseOptions.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <input value={custFilter} onChange={(e) => setCustFilter(e.target.value)} placeholder="고객 필터"
                     style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 13, marginLeft: "auto" }} />
            </div>
          )}

          {data && cards.length === 0 && (
            <Empty>{data.open_count === 0 ? "오픈 주문이 없습니다 (미출하 건 없음)." : "조건에 맞는 카드가 없습니다."}</Empty>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {cards.map((c, i) => {
              const rk = RISK[c.risk] || RISK.unknown;
              return (
                <Card key={i} rk={rk}>
                  <Head left={<>
                    {rk.dot} {c.customer || "(고객 미상)"}
                    <Badge>{TYPE_LABEL[c.order_type] || c.order_type || "-"}</Badge>
                    {c.fse && <span style={{ marginLeft: 6, fontSize: 11, color: "#475569" }}>· {c.fse}</span>}
                    {c.overdue && <Badge bg="#fee2e2" color="#b91c1c">납기 경과</Badge>}
                  </>} right={<span style={{ color: rk.color, fontWeight: 600 }}>{rk.label}</span>} />
                  <div style={{ fontSize: 13, color: "#334155", marginTop: 3 }}>
                    {c.did}/{c.mpn} · {fmtQty(c.qty)}ea · 요청(CRD) {c.crd || "—"} · 가용(MAD) <b>{c.mad || "—"}</b>
                  </div>
                  <div style={{ fontSize: 12.5, color: rk.color, marginTop: 4 }}>{c.reason}</div>
                </Card>
              );
            })}
          </div>
        </>)}

        {/* ───────── 변화 보기 ───────── */}
        {mode === "change" && (<>
          <p style={{ fontSize: 13, color: "#64748b", marginTop: 0 }}>
            이전·현재 두 Backlog 리포트를 올리면 <b>자재 가용일(MAD)이 밀린 주문</b>(선적 일정 악화)을 SO 기준으로 찾아
            많이 밀린 순으로 보여줍니다. 절대 위험과 별개로 <b>"곧 위험해질 것"</b>을 잡습니다.
          </p>
          <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", margin: "16px 0" }}>
            <FileBtn label="이전 백록" file={prevFile} inputRef={prevRef} onPick={setPrevFile} />
            <span style={{ color: "#94a3b8" }}>→</span>
            <FileBtn label="현재 백록" file={curFile} inputRef={curRef} onPick={setCurFile} />
            <button onClick={doCompare} disabled={!prevFile || !curFile || cmpLoading}
                    style={{ padding: "9px 18px", background: (prevFile && curFile) ? "#0a0e12" : "#cbd5e1",
                             color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600,
                             cursor: (prevFile && curFile) ? "pointer" : "default" }}>
              {cmpLoading ? "비교 중…" : "비교"}
            </button>
            {cmp && <span style={{ fontSize: 12, color: "#64748b" }}>기준일 {cmp.today}</span>}
          </div>

          {cmpError && <Banner>{cmpError}</Banner>}

          {cmp && (
            <>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
                <Stat label="🔺 밀림" value={cmp.summary.slipped} color="#dc2626" big />
                <Stat label="⛔ 무기한" value={cmp.summary.indefinite} color="#7c2d12" />
                <Stat label="🔻 개선" value={cmp.summary.improved} color="#16a34a" />
                <Stat label="＋ 신규" value={cmp.summary.new} color="#2563eb" />
                <Stat label="✔ 소진" value={cmp.summary.gone} color="#64748b" />
                <Stat label="▬ 동일" value={cmp.summary.same} color="#94a3b8" />
              </div>

              <label style={{ fontSize: 12.5, color: "#475569", display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 12 }}>
                <input type="checkbox" checked={indefOnly} onChange={(e) => setIndefOnly(e.target.checked)} />
                무기한 연기된 것만 보기
              </label>

              {slipped.length === 0 && <Empty>MAD가 밀린 주문이 없습니다.</Empty>}

              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {slipped.map((c, i) => {
                  const rk = c.indefinite ? { color: "#7c2d12", bg: "#fff7ed", border: "#fed7aa" }
                                          : { color: "#dc2626", bg: "#fef2f2", border: "#fecaca" };
                  return (
                    <Card key={i} rk={rk}>
                      <Head left={<>
                        🔺 {c.customer || "(고객 미상)"}
                        <Badge>{TYPE_LABEL[c.order_type] || c.order_type || "-"}</Badge>
                        {c.fse && <span style={{ marginLeft: 6, fontSize: 11, color: "#475569" }}>· {c.fse}</span>}
                        {c.indefinite && <Badge bg="#ffedd5" color="#7c2d12">무기한 연기</Badge>}
                      </>} right={<span style={{ color: rk.color, fontWeight: 700 }}>+{Number(c.slip_days).toLocaleString()}일 밀림</span>} />
                      <div style={{ fontSize: 13, color: "#334155", marginTop: 3 }}>
                        {c.did}/{c.mpn} · {fmtQty(c.qty)}ea · 요청(CRD) {c.crd || "—"}
                      </div>
                      <div style={{ fontSize: 12.5, color: rk.color, marginTop: 4 }}>
                        자재 가용일 <b>{c.prev_mad}</b> → <b>{c.mad}</b> 로 밀림
                      </div>
                    </Card>
                  );
                })}
              </div>
            </>
          )}
        </>)}
      </div>
    </div>
  );
}

// ── 작은 표현용 컴포넌트들 ──
const Banner = ({ children }) => (
  <div style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#dc2626",
                padding: "12px 16px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>{children}</div>
);
const Empty = ({ children }) => (
  <div style={{ padding: 48, textAlign: "center", color: "#94a3b8", fontSize: 14 }}>{children}</div>
);
const Card = ({ rk, children }) => (
  <div style={{ background: rk.bg, border: `1px solid ${rk.border}`, borderLeft: `5px solid ${rk.color}`,
                borderRadius: 8, padding: "12px 16px" }}>{children}</div>
);
const Head = ({ left, right }) => (
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
    <div style={{ fontSize: 14, fontWeight: 700, color: "#0a0e12" }}>{left}</div>
    <div style={{ fontSize: 12 }}>{right}</div>
  </div>
);
const Badge = ({ children, bg = "#eef2f6", color = "#475569" }) => (
  <span style={{ marginLeft: 6, fontSize: 11, color, background: bg, padding: "1px 6px", borderRadius: 4 }}>{children}</span>
);
const Stat = ({ label, value, color, big }) => (
  <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "10px 16px", minWidth: 92 }}>
    <div style={{ fontSize: 11.5, color: "#64748b" }}>{label}</div>
    <div style={{ fontSize: big ? 22 : 18, fontWeight: 700, color }}>{Number(value).toLocaleString()}</div>
  </div>
);
const FileBtn = ({ label, file, inputRef, onPick }) => (
  <div>
    <input ref={inputRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
           onChange={(e) => onPick(e.target.files[0])} />
    <button onClick={() => inputRef.current?.click()}
            style={{ padding: "8px 14px", background: "#fff", color: "#0a0e12", border: "1px solid #cbd5e1",
                     borderRadius: 8, fontSize: 13, cursor: "pointer" }}>
      📄 {label}{file ? " ✓" : ""}
    </button>
    {file && <div style={{ fontSize: 11, color: "#64748b", marginTop: 3, maxWidth: 180, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{file.name}</div>}
  </div>
);

export default CrdBoard;
