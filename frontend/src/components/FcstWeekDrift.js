import React, { useState, useRef, useMemo } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

// Design tokens — Unitrontech 브랜드 (#232d37 + #c43a3a)
const T = {
  text: "#0a0a0a", text2: "#555555", text3: "#999999",
  bg: "#f7f7f8", bgAlt: "#fafafa", surface: "#ffffff",
  border: "#e4e4e7", border2: "#d4d4d8",
  brand: "#232d37", accent: "#c43a3a", accentH: "#a92e2e",
  pos: "#1f7a4c", neg: "#c43a3a", warn: "#a37111", info: "#2a567a",
  posBg: "#eef5f1", negBg: "#fcecec", warnBg: "#faf2e0", infoBg: "#eaf0f5",
  fontStack: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Segoe UI', system-ui, sans-serif",
};

const STATUS_STYLE = {
  "증가":   { dot: T.pos,    color: T.pos,    bg: T.posBg,  ring: "#cfe4d8" },
  "감소":   { dot: T.neg,    color: T.neg,    bg: T.negBg,  ring: "#ecc5c5" },
  "신규":   { dot: T.info,   color: T.info,   bg: T.infoBg, ring: "#c8d6e2" },
  "제거":   { dot: "#8a3a52", color: "#8a3a52", bg: "#f5ecf0", ring: "#e0c8d2" },
  "무변동": { dot: "#6b7280", color: "#6b7280", bg: "#f3f4f6", ring: "#d4d4d8" },
};

const STATUS_FILTERS = ["all", "증가", "감소", "신규", "제거", "무변동"];

const fmtUSD0 = (v) => v == null ? "—" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const fmtUSD2 = (v) => v == null ? "—" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtSign = (v) => { if (v == null) return "—"; const s = v >= 0 ? "+" : "−"; return `${s}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`; };
const fmtSignPct = (v) => { if (v == null) return "—"; const s = v >= 0 ? "+" : "−"; return `${s}${Math.abs(v).toFixed(1)}%`; };
const fmtBytes = (n) => n < 1024 ? `${n} B` : n < 1024*1024 ? `${(n/1024).toFixed(1)} KB` : `${(n/1024/1024).toFixed(2)} MB`;
const nowStamp = () => new Date().toLocaleString("ko-KR", { hour12: false });

function FcstWeekDrift({ currFile, setCurrFile }) {
  // currFile 은 부모(FcstSalesDiff)에서 lift-up — 실적비교의 FCST 와 동일 객체
  const [prevFile, setPrevFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const [generatedAt, setGeneratedAt] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [dragP, setDragP] = useState(false);
  const [dragC, setDragC] = useState(false);
  const prevRef = useRef();
  const currRef = useRef();

  const reset = () => {
    setPrevFile(null); setCurrFile(null);
    setError(null); setData(null); setGeneratedAt(null);
    setStatusFilter("all"); setOwnerFilter("all"); setQuery("");
    if (prevRef.current) prevRef.current.value = "";
    if (currRef.current) currRef.current.value = "";
  };

  const onDrop = (e, setter, setDrag) => {
    e.preventDefault(); setDrag(false);
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) { setError(".xlsx 또는 .xlsm 만 지원합니다."); return; }
    setter(f); setData(null); setError(null);
  };

  const runCompare = async () => {
    if (!prevFile || !currFile) { setError("전주 FCST 와 금주 FCST 두 파일 모두 선택하세요."); return; }
    setLoading(true); setError(null); setData(null);
    try {
      const fd = new FormData();
      fd.append("prev", prevFile);
      fd.append("curr", currFile);
      const res = await axios.post(`${API_URL}/api/fcst-drift/preview`, fd);
      if (res.data.error) { setError(res.data.error); return; }
      setData(res.data); setGeneratedAt(nowStamp());
    } catch (e) {
      setError(`요청 실패: ${e.response?.data?.detail || e.message}`);
    } finally { setLoading(false); }
  };

  const runExport = async () => {
    if (!prevFile || !currFile) return;
    setExporting(true); setError(null);
    try {
      const fd = new FormData();
      fd.append("prev", prevFile); fd.append("curr", currFile);
      const res = await axios.post(`${API_URL}/api/fcst-drift/export`, fd, { responseType: "blob" });
      if (res.data.type?.includes("json")) {
        const text = await res.data.text();
        try { setError(JSON.parse(text).error || "내보내기 실패"); } catch { setError("내보내기 실패"); }
        return;
      }
      let fname = "FCST_주간변동.xlsx";
      const cd = res.headers["content-disposition"];
      if (cd) { const m = cd.match(/filename\*=UTF-8''([^;]+)/i); if (m) { try { fname = decodeURIComponent(m[1]); } catch {} } }
      const blob = new Blob([res.data], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = fname;
      document.body.appendChild(a); a.click(); a.remove(); window.URL.revokeObjectURL(url);
    } catch (e) {
      if (e.response?.data instanceof Blob) {
        try { setError(JSON.parse(await e.response.data.text()).error || `내보내기 실패: ${e.message}`); return; } catch {}
      }
      setError(`내보내기 실패: ${e.response?.data?.detail || e.message}`);
    } finally { setExporting(false); }
  };

  const owners = data?.owners || [];
  const ownerOptions = useMemo(() => ["all", ...owners.map(o => o.owner)], [owners]);
  const maxOwnerDelta = useMemo(() => Math.max(1, ...owners.map(o => Math.abs(o.delta))), [owners]);
  const maxMoverDelta = useMemo(() => Math.max(1, ...(data?.top_movers || []).map(r => Math.abs(r["△"]))), [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.rows.filter(r => {
      if (statusFilter !== "all" && r["상태"] !== statusFilter) return false;
      if (ownerFilter !== "all" && (r["담당자"] || "(미지정)") !== ownerFilter) return false;
      if (!q) return true;
      return `${r["담당자"]||""} ${r["Customer"]||""} ${r["MPN"]||""}`.toLowerCase().includes(q);
    });
  }, [data, statusFilter, ownerFilter, query]);

  return (
    <div style={{ fontFamily: T.fontStack, color: T.text }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1 style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.5px", margin: 0 }}>
            FCST 주간 변동 <span style={{ color: T.text3, fontWeight: 600, fontSize: 18 }}>· 전주 vs 금주</span>
          </h1>
          <p style={{ fontSize: 13, color: T.text2, marginTop: 6, marginBottom: 0 }}>
            동일한 FCST 엑셀 두 스냅샷을 <b>업체명 + 파트명</b> 기준 비교 · 월별 변동·증감·신규·제거 추적
          </p>
        </div>
        {data && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Pill icon="📅" label={`월 ${data.months.join(" / ")}`} tone="info" />
            <Pill icon="🕒" label={generatedAt} tone="muted" />
          </div>
        )}
      </div>

      <Divider />
      {error && <ErrorBanner>{error}</ErrorBanner>}

      <Card style={{ marginBottom: 20 }}>
        <CardLabel>입력 파일</CardLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 24px 1fr", gap: 12, alignItems: "stretch", marginBottom: 14 }}>
          <FileSlot
            label="전주 FCST" accent="#64748b" hint="지난주 스냅샷 — Sales Revenue FCST_*.xlsx"
            file={prevFile} setter={setPrevFile} inputRef={prevRef}
            drag={dragP} setDrag={setDragP} onDrop={onDrop} clearPreview={() => setData(null)}
          />
          <div style={{ alignSelf: "center", color: T.text3, fontSize: 22, textAlign: "center" }}>→</div>
          <FileSlot
            label="금주 FCST" accent={T.info} hint="이번주 스냅샷 — Sales Revenue FCST_*.xlsx"
            file={currFile} setter={setCurrFile} inputRef={currRef}
            drag={dragC} setDrag={setDragC} onDrop={onDrop} clearPreview={() => setData(null)}
          />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <PrimaryBtn onClick={runCompare} disabled={loading || !prevFile || !currFile} loading={loading}>
            {loading ? "분석 중…" : "변동 분석"}
          </PrimaryBtn>
          <SuccessBtn onClick={runExport} disabled={exporting || !data} loading={exporting}>
            {exporting ? "내보내는 중…" : "엑셀 내보내기"}
          </SuccessBtn>
          <GhostBtn onClick={reset} disabled={loading || exporting}>초기화</GhostBtn>
        </div>
      </Card>

      {!data && !loading && <EmptyState />}

      {data && (
        <>
          {/* 월별 △ KPI + Total (RS AMT) */}
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${data.kpi.monthly.length + 1}, 1fr)`, gap: 12, marginBottom: 12 }}>
            {data.kpi.monthly.map((mk) => (
              <DeltaKpi key={mk.month} title={`${mk.month} RS AMT 변동`} prev={mk.prev} curr={mk.curr} delta={mk.delta} deltaPct={mk.delta_pct} />
            ))}
            <DeltaKpi title="3개월 RS AMT 합계" prev={data.kpi.total_prev} curr={data.kpi.total_curr} delta={data.kpi.total_delta} deltaPct={data.kpi.total_delta_pct} emphasize />
          </div>
          {/* 월별 △ KPI + Total (GP) */}
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${data.kpi.monthly.length + 1}, 1fr)`, gap: 12, marginBottom: 16 }}>
            {data.kpi.monthly.map((mk) => (
              <DeltaKpi key={mk.month} title={`${mk.month} GP 변동`} prev={mk.gp_prev} curr={mk.gp_curr} delta={mk.gp_delta}
                deltaPct={mk.gp_prev ? (mk.gp_delta / mk.gp_prev * 100) : null} />
            ))}
            <DeltaKpi title="3개월 GP 합계" prev={data.kpi.gp_total_prev} curr={data.kpi.gp_total_curr} delta={data.kpi.gp_total_delta} deltaPct={data.kpi.gp_total_delta_pct} emphasize />
          </div>

          {/* 상태 분포 */}
          <Card style={{ marginBottom: 16, padding: "12px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: T.text2, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                변동 분포
              </span>
              {Object.entries(data.kpi.counts).filter(([_, n]) => n > 0).map(([k, n]) => {
                const st = STATUS_STYLE[k];
                const isActive = statusFilter === k;
                return (
                  <button key={k}
                    onClick={() => setStatusFilter(isActive ? "all" : k)}
                    title={`'${k}' 로 detail 필터링`}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      padding: "3px 10px 3px 8px",
                      background: isActive ? st.bg : "transparent",
                      color: st.color,
                      border: `1px solid ${isActive ? st.ring : T.border}`,
                      borderRadius: 999, cursor: "pointer", fontSize: 12, fontWeight: 600,
                      transition: "all 0.15s",
                    }}>
                    <span style={{ width: 6, height: 6, borderRadius: 999, background: st.dot, flexShrink: 0 }} />
                    <span style={{ fontVariantNumeric: "tabular-nums" }}>
                      {k} <span style={{ fontWeight: 700, marginLeft: 2 }}>{n}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </Card>

          {/* 담당자별 + Top movers */}
          <div style={{ display: "grid", gridTemplateColumns: "1.15fr 1fr", gap: 16, marginBottom: 18 }}>
            <Card padding={0}>
              <CardHeader title="담당자별 변동" subtitle="절대 △ 큰 순" />
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ background: T.bg, color: T.text2, fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    <Th>담당자</Th>
                    <Th right>전주</Th>
                    <Th right>금주</Th>
                    <Th right>△</Th>
                    <Th right>△ %</Th>
                    <Th right>GP △</Th>
                    <Th>분포</Th>
                  </tr>
                </thead>
                <tbody>
                  {owners.map((o) => (
                    <tr key={o.owner} style={{ borderTop: `1px solid ${T.border}` }}>
                      <Td>
                        <strong>{o.owner}</strong>
                        <div style={{ fontSize: 10.5, color: T.text3, marginTop: 2 }}>
                          {o.items}건 · 증{o.increased} 감{o.decreased} 신규{o.new} 제거{o.removed}
                        </div>
                      </Td>
                      <Td right num>{fmtUSD0(o.prev)}</Td>
                      <Td right num>{fmtUSD0(o.curr)}</Td>
                      <Td right num color={o.delta >= 0 ? T.pos : T.neg}>{fmtSign(o.delta)}</Td>
                      <Td right num color={o.delta >= 0 ? T.pos : T.neg}>{fmtSignPct(o.delta_pct)}</Td>
                      <Td right num color={o.gp_delta >= 0 ? T.pos : T.neg}>{fmtSign(o.gp_delta)}</Td>
                      <Td><DivergingBar value={o.delta} max={maxOwnerDelta} /></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card padding={0}>
              <CardHeader title="Top Movers (라인 단위)" subtitle="절대 △ Top 10" />
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ background: T.bg, color: T.text2, fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    <Th>Customer · MPN</Th>
                    <Th right>△</Th>
                    <Th>분포</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_movers.map((r, i) => {
                    const st = STATUS_STYLE[r["상태"]];
                    return (
                      <tr key={i} style={{ borderTop: `1px solid ${T.border}` }}>
                        <Td>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, maxWidth: 240 }}>
                            <span style={{ width: 6, height: 6, borderRadius: 999, background: st.dot, flexShrink: 0 }} />
                            <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {r["Customer"]}
                            </strong>
                          </div>
                          <div style={{ fontSize: 10.5, color: T.text3, marginTop: 2, fontFamily: "ui-monospace, monospace" }}>
                            {r["MPN"]} · {r["담당자"] || "—"}
                          </div>
                        </Td>
                        <Td right num color={r["△"] >= 0 ? T.pos : T.neg}>{fmtSign(r["△"])}</Td>
                        <Td><DivergingBar value={r["△"]} max={maxMoverDelta} /></Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>
          </div>

          {/* Detail */}
          <Card padding={0}>
            <CardHeader
              title="Detail · (Customer, MPN) 라인별 변동"
              subtitle={`${filtered.length.toLocaleString()} / ${data.rows.length.toLocaleString()} 행 표시`}
              right={
                <Segmented
                  options={STATUS_FILTERS.map((f) => ({
                    key: f,
                    label: f === "all" ? `전체 ${data.rows.length}` : `${f} ${data.kpi.counts[f] || 0}`,
                    color: f === "all" ? undefined : STATUS_STYLE[f]?.color,
                    bg: f === "all" ? undefined : STATUS_STYLE[f]?.bg,
                  }))}
                  value={statusFilter}
                  onChange={setStatusFilter}
                />
              }
            />
            <div style={{ display: "flex", gap: 10, padding: "10px 16px", borderBottom: `1px solid ${T.border}`, background: T.bg, alignItems: "center" }}>
              <select
                value={ownerFilter} onChange={(e) => setOwnerFilter(e.target.value)}
                style={{ padding: "6px 10px", fontSize: 12, border: `1px solid ${T.border2}`, borderRadius: 6, background: T.surface, color: T.text, cursor: "pointer" }}
              >
                {ownerOptions.map((o) => <option key={o} value={o}>{o === "all" ? "담당자 전체" : o}</option>)}
              </select>
              <div style={{ position: "relative", flex: 1, maxWidth: 320 }}>
                <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: T.text3, fontSize: 13 }}>🔎</span>
                <input
                  type="text" placeholder="담당자 · Customer · MPN 검색"
                  value={query} onChange={(e) => setQuery(e.target.value)}
                  style={{ width: "100%", padding: "6px 10px 6px 30px", fontSize: 12, border: `1px solid ${T.border2}`, borderRadius: 6, background: T.surface, color: T.text }}
                />
              </div>
              {(statusFilter !== "all" || ownerFilter !== "all" || query) && (
                <button
                  onClick={() => { setStatusFilter("all"); setOwnerFilter("all"); setQuery(""); }}
                  style={{ border: 0, background: "transparent", color: T.info, cursor: "pointer", fontSize: 12, fontWeight: 600 }}>
                  필터 초기화
                </button>
              )}
            </div>

            <div style={{ overflow: "auto", maxHeight: 620 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, whiteSpace: "nowrap" }}>
                <thead style={{ position: "sticky", top: 0, zIndex: 1 }}>
                  <tr style={{ background: T.brand, color: "white" }}>
                    <ThH>담당자</ThH>
                    <ThH>Customer</ThH>
                    <ThH>MPN</ThH>
                    <ThH right>전주합계</ThH>
                    <ThH right>금주합계</ThH>
                    <ThH right>△</ThH>
                    <ThH right>△%</ThH>
                    <ThH right>GP 전주</ThH>
                    <ThH right>GP 금주</ThH>
                    <ThH right>GP △</ThH>
                    <ThH center>상태</ThH>
                    {data.months.map((m) => (
                      <React.Fragment key={m}>
                        <ThH right>{m} 전주</ThH>
                        <ThH right>{m} 금주</ThH>
                        <ThH right>{m} △</ThH>
                        <ThH right>{m} GP△</ThH>
                      </React.Fragment>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr>
                      <td colSpan={11 + 4 * data.months.length}
                        style={{ padding: 40, textAlign: "center", color: T.text3, fontSize: 13 }}>
                        조건에 맞는 라인이 없습니다.
                      </td>
                    </tr>
                  ) : filtered.map((r, i) => {
                    const st = STATUS_STYLE[r["상태"]];
                    const zebra = i % 2 === 0 ? T.surface : "#fafbfc";
                    return (
                      <tr key={i} style={{ background: zebra, borderBottom: `1px solid ${T.border}` }}>
                        <Td>{r["담당자"] || "—"}</Td>
                        <Td title={r["Customer"]}>{r["Customer"]}</Td>
                        <Td title={r["MPN"]} mono>{r["MPN"]}</Td>
                        <Td right num>{fmtUSD2(r["전주합계"])}</Td>
                        <Td right num>{fmtUSD2(r["금주합계"])}</Td>
                        <Td right num color={r["△"] >= 0 ? T.pos : T.neg}>{fmtSign(r["△"])}</Td>
                        <Td right num color={
                          r["△%"] == null ? T.text3 : r["△%"] >= 0 ? T.pos : T.neg
                        }>{fmtSignPct(r["△%"])}</Td>
                        <Td right num>{fmtUSD2(r["GP전주"])}</Td>
                        <Td right num>{fmtUSD2(r["GP금주"])}</Td>
                        <Td right num color={r["GP△"] >= 0 ? T.pos : T.neg}>{fmtSign(r["GP△"])}</Td>
                        <Td center>
                          <span style={{
                            display: "inline-flex", alignItems: "center", gap: 5,
                            padding: "2px 9px", borderRadius: 999,
                            background: st.bg, color: st.color, border: `1px solid ${st.ring}`,
                            fontWeight: 700, fontSize: 10.5,
                          }}>
                            <span style={{ width: 5, height: 5, borderRadius: 999, background: st.dot }} />
                            {r["상태"]}
                          </span>
                        </Td>
                        {data.months.map((m) => (
                          <React.Fragment key={m}>
                            <Td right num color={T.text2}>{fmtUSD2(r[`${m}_prev`])}</Td>
                            <Td right num color={T.text2}>{fmtUSD2(r[`${m}_curr`])}</Td>
                            <Td right num color={r[`${m}_△`] === 0 ? T.text3 : r[`${m}_△`] > 0 ? T.pos : T.neg}>
                              {r[`${m}_△`] === 0 ? "—" : fmtSign(r[`${m}_△`])}
                            </Td>
                            <Td right num color={r[`${m}_gp_△`] === 0 ? T.text3 : r[`${m}_gp_△`] > 0 ? T.pos : T.neg}>
                              {r[`${m}_gp_△`] === 0 ? "—" : fmtSign(r[`${m}_gp_△`])}
                            </Td>
                          </React.Fragment>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// ── Sub-components (FcstSalesDiff 와 동일 디자인 토큰)
function Divider() { return <div style={{ height: 1, background: T.border, margin: "16px 0 18px" }} />; }
function Card({ children, style, padding = 16 }) {
  return <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 4, padding, ...style }}>{children}</div>;
}
function CardLabel({ children }) {
  return <div style={{ fontSize: 11, fontWeight: 700, color: T.text2, textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 10 }}>{children}</div>;
}
function CardHeader({ title, subtitle, right }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px", borderBottom: `1px solid ${T.border}` }}>
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: T.text }}>{title}</div>
        {subtitle && <div style={{ fontSize: 11, color: T.text3, marginTop: 2 }}>{subtitle}</div>}
      </div>
      {right}
    </div>
  );
}

function DeltaKpi({ title, prev, curr, delta, deltaPct, emphasize }) {
  const accent = delta == null ? T.text3 : delta >= 0 ? T.pos : T.neg;
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 4,
      padding: "14px 16px", position: "relative", overflow: "hidden",
      boxShadow: emphasize ? `inset 0 0 0 1px ${accent}` : "none",
    }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: accent }} />
      <div style={{ fontSize: 10.5, color: T.text2, fontWeight: 700, marginBottom: 10, letterSpacing: "0.5px", textTransform: "uppercase" }}>{title}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 6 }}>
        <div style={{ fontSize: 22, fontWeight: 800, color: accent, lineHeight: 1, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.5px" }}>
          {fmtSign(delta)}
        </div>
        <div style={{ fontSize: 12, color: accent, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
          {fmtSignPct(deltaPct)}
        </div>
      </div>
      <div style={{ fontSize: 11, color: T.text3, fontVariantNumeric: "tabular-nums" }}>
        {fmtUSD0(prev)} <span style={{ margin: "0 4px" }}>→</span> {fmtUSD0(curr)}
      </div>
    </div>
  );
}

function DivergingBar({ value, max }) {
  const pct = Math.min(100, Math.abs(value) / max * 100);
  const color = value >= 0 ? T.pos : T.neg;
  return (
    <div style={{ position: "relative", height: 8, width: 140, minWidth: 140 }}>
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: T.border2 }} />
      <div style={{
        position: "absolute", top: 1, bottom: 1,
        left: value >= 0 ? "50%" : `${50 - pct / 2}%`,
        width: `${pct / 2}%`,
        background: color, borderRadius: 2,
      }} />
    </div>
  );
}

function FileSlot({ label, accent, hint, file, setter, inputRef, drag, setDrag, onDrop, clearPreview }) {
  return (
    <div
      style={{
        border: `1px dashed ${drag ? accent : file ? accent : T.border2}`,
        background: drag ? T.infoBg : file ? T.bgAlt : T.surface,
        borderRadius: 4, padding: 14, cursor: "pointer",
        transition: "all 0.15s",
        display: "flex", flexDirection: "column", justifyContent: "center", minHeight: 92,
      }}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragEnter={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={(e) => { e.preventDefault(); setDrag(false); }}
      onDrop={(e) => onDrop(e, setter, setDrag)}
    >
      <input ref={inputRef} type="file" accept=".xlsx,.xlsm" style={{ display: "none" }}
        onChange={(e) => { setter(e.target.files[0] || null); clearPreview(); }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 800, color: accent, letterSpacing: "0.3px" }}>{label}</span>
        {file && <span style={{ fontSize: 10, color: T.text3, fontVariantNumeric: "tabular-nums" }}>{fmtBytes(file.size)}</span>}
      </div>
      <div style={{ fontSize: 10.5, color: T.text3, marginBottom: 6 }}>{hint}</div>
      {file ? (
        <div style={{ fontSize: 12, color: T.text, wordBreak: "break-all", lineHeight: 1.3 }}>
          <span style={{ marginRight: 4 }}>📄</span>{file.name}
        </div>
      ) : (
        <div style={{ fontSize: 11, color: drag ? accent : T.text3 }}>
          {drag ? "여기에 놓으세요" : "클릭하거나 .xlsx 파일을 끌어다 놓으세요"}
        </div>
      )}
    </div>
  );
}

function PrimaryBtn({ children, onClick, disabled, loading }) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        padding: "10px 20px", background: disabled ? "#d4d4d8" : T.accent,
        color: "white", border: 0, borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        fontWeight: 700, fontSize: 12.5, letterSpacing: "0.3px",
        display: "inline-flex", alignItems: "center", gap: 6,
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = T.accentH; }}
      onMouseLeave={(e) => { if (!disabled) e.currentTarget.style.background = T.accent; }}>
      {loading && <Spinner />}{children}
    </button>
  );
}
function SuccessBtn({ children, onClick, disabled, loading }) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        padding: "10px 18px", background: disabled ? "#d4d4d8" : T.brand,
        color: "white", border: 0, borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        fontWeight: 700, fontSize: 12.5, letterSpacing: "0.3px",
        display: "inline-flex", alignItems: "center", gap: 6,
      }}>
      {loading && <Spinner />}{children}
    </button>
  );
}
function GhostBtn({ children, onClick, disabled }) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        padding: "10px 16px", background: T.surface, color: T.text2,
        border: `1px solid ${T.border2}`, borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        fontWeight: 600, fontSize: 12.5, letterSpacing: "0.2px",
      }}>{children}</button>
  );
}
function Spinner() {
  return (
    <span style={{
      display: "inline-block", width: 12, height: 12, borderRadius: 999,
      border: "2px solid rgba(255,255,255,0.4)", borderTopColor: "white",
      animation: "fcst-drift-spin 0.8s linear infinite",
    }}>
      <style>{`@keyframes fcst-drift-spin { to { transform: rotate(360deg); } }`}</style>
    </span>
  );
}
function Pill({ icon, label, tone = "muted" }) {
  const styles = { info: { bg: T.infoBg, color: T.info, border: "#bfdbfe" }, muted: { bg: T.bg, color: T.text2, border: T.border } }[tone];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "3px 9px", borderRadius: 999,
      background: styles.bg, color: styles.color, border: `1px solid ${styles.border}`,
      fontSize: 11.5, fontWeight: 600,
    }}>
      {icon && <span>{icon}</span>}{label}
    </span>
  );
}
function Segmented({ options, value, onChange }) {
  return (
    <div style={{ display: "inline-flex", border: `1px solid ${T.border2}`, borderRadius: 4, overflow: "hidden", background: T.surface }}>
      {options.map((o, i) => {
        const active = value === o.key;
        return (
          <button key={o.key} onClick={() => onChange(o.key)}
            style={{
              padding: "6px 11px", border: 0,
              background: active ? (o.bg || T.brand) : "transparent",
              color: active ? (o.color || "white") : T.text2,
              fontWeight: active ? 700 : 600, fontSize: 11,
              letterSpacing: "0.2px",
              cursor: "pointer",
              borderRight: i < options.length - 1 ? `1px solid ${T.border2}` : 0,
              fontVariantNumeric: "tabular-nums",
            }}>{o.label}</button>
        );
      })}
    </div>
  );
}
function Th({ children, right, center }) {
  return <th style={{ padding: "9px 12px", textAlign: right ? "right" : center ? "center" : "left", fontWeight: 700, fontSize: 10.5 }}>{children}</th>;
}
function ThH({ children, right, center }) {
  return (
    <th style={{
      padding: "9px 11px", fontWeight: 700, fontSize: 10.5,
      letterSpacing: "0.3px", textTransform: "uppercase",
      textAlign: right ? "right" : center ? "center" : "left",
    }}>{children}</th>
  );
}
function Td({ children, right, center, color, num, mono, title }) {
  return (
    <td title={title} style={{
      padding: "8px 11px", textAlign: right ? "right" : center ? "center" : "left",
      color: color || "inherit",
      fontVariantNumeric: num ? "tabular-nums" : "normal",
      fontFamily: mono ? "ui-monospace, 'Cascadia Code', 'SF Mono', Consolas, monospace" : "inherit",
      fontSize: mono ? 11.5 : "inherit",
      maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    }}>{children}</td>
  );
}
function ErrorBanner({ children }) {
  return (
    <div style={{
      background: T.negBg, border: `1px solid #fecaca`, color: T.neg,
      padding: "10px 14px", borderRadius: 4, fontSize: 13, marginBottom: 14,
      display: "flex", alignItems: "center", gap: 8,
    }}><span style={{ fontSize: 16 }}>⚠</span>{children}</div>
  );
}
function EmptyState() {
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "32px 28px", textAlign: "center", background: T.bg, borderBottom: `1px solid ${T.border}` }}>
        <div style={{ fontSize: 32, marginBottom: 8 }}>📈</div>
        <div style={{ fontSize: 15, fontWeight: 700, color: T.text, marginBottom: 4 }}>FCST 주간 변동 대시보드</div>
        <div style={{ fontSize: 12.5, color: T.text2 }}>
          지난주와 이번주 FCST 엑셀을 업로드하면 라인 단위 증감과 신규/제거를 자동 추적합니다.
        </div>
      </div>
      <div style={{ padding: "20px 28px", display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
        <Step n="1" title="전주 FCST 업로드" body="지난주에 SharePoint에 올렸던 동일 양식의 FCST 엑셀 (Sales Revenue 시트)." />
        <Step n="2" title="금주 FCST 업로드" body="이번주 갱신된 FCST 엑셀. 둘 다 동일 구조여야 합니다." />
        <Step n="3" title="변동 분석" body="(Customer + MPN) 기준 매칭 · 월별 RS AMT + GP 변동 + 신규/제거 자동 분류." />
      </div>
      <div style={{ padding: "14px 28px", background: T.bg, borderTop: `1px solid ${T.border}`, fontSize: 11.5, color: T.text2, lineHeight: 1.7 }}>
        <b>상태 분류</b>: 증가(전·금주 모두 존재 + 금주 ↑) · 감소(금주 ↓) · 신규(금주만 존재) · 제거(전주만 존재) · 무변동
      </div>
    </Card>
  );
}
function Step({ n, title, body }) {
  return (
    <div>
      <div style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 22, height: 22, borderRadius: 999, background: T.brand, color: "white",
        fontSize: 11, fontWeight: 800, marginBottom: 8,
      }}>{n}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 11.5, color: T.text2, lineHeight: 1.5 }}>{body}</div>
    </div>
  );
}

export default FcstWeekDrift;
