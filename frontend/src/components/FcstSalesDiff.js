import React, { useState, useRef, useMemo } from "react";
import axios from "axios";
import FcstWeekDrift from "./FcstWeekDrift";

const API_URL = process.env.REACT_APP_API_URL || "";

// ─────────────────────────────────────────────────────────────────────────────
//  Design tokens — Unitrontech 브랜드 시그니처 (#232d37 charcoal + #c43a3a red)
// ─────────────────────────────────────────────────────────────────────────────
const T = {
  // text
  text:    "#0a0a0a",
  text2:   "#555555",
  text3:   "#999999",
  // surface
  bg:      "#f7f7f8",
  bgAlt:   "#fafafa",
  surface: "#ffffff",
  border:  "#e4e4e7",
  border2: "#d4d4d8",
  // brand
  brand:   "#232d37",   // Unitrontech charcoal — secondary buttons, headers
  accent:  "#c43a3a",   // Unitrontech red    — primary CTA, emphasis
  accentH: "#a92e2e",   // hover
  // status (desaturated, sober)
  pos:     "#1f7a4c",
  neg:     "#c43a3a",
  warn:    "#a37111",
  info:    "#2a567a",
  // soft tints
  posBg:   "#eef5f1",
  negBg:   "#fcecec",
  warnBg:  "#faf2e0",
  infoBg:  "#eaf0f5",
  // type
  fontStack: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Segoe UI', system-ui, sans-serif",
};

const STATUS_STYLE = {
  "달성":     { dot: T.pos,    color: T.pos,    bg: T.posBg,    ring: "#cfe4d8" },
  "근접":     { dot: T.warn,   color: T.warn,   bg: T.warnBg,   ring: "#ead9b3" },
  "미달":     { dot: T.neg,    color: T.neg,    bg: T.negBg,    ring: "#ecc5c5" },
  "초과":     { dot: T.info,   color: T.info,   bg: T.infoBg,   ring: "#c8d6e2" },
  "매칭누락": { dot: "#8a3a52", color: "#8a3a52", bg: "#f5ecf0", ring: "#e0c8d2" },
  "미실현":   { dot: "#6b7280", color: "#6b7280", bg: "#f3f4f6", ring: "#d4d4d8" },
  "FCST=0":   { dot: "#3f3f6b", color: "#3f3f6b", bg: "#ecedf5", ring: "#cccfe0" },
  "—":        { dot: "#a1a1aa", color: "#a1a1aa", bg: "#f4f4f5", ring: "#e4e4e7" },
};

const STATUS_FILTERS = ["all", "달성", "근접", "미달", "초과", "매칭누락", "미실현", "FCST=0"];

// ─────────────────────────────────────────────────────────────────────────────
//  Formatters
// ─────────────────────────────────────────────────────────────────────────────
const fmtUSD0  = (v) => v == null ? "—" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const fmtUSD2  = (v) => v == null ? "—" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtNum0  = (v) => v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
const fmtPct   = (v) => v == null ? "—" : `${Number(v).toFixed(1)}%`;
const fmtSign  = (v) => { if (v == null) return "—"; const s = v >= 0 ? "+" : "−"; return `${s}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`; };
const fmtBytes = (n) => n < 1024 ? `${n} B` : n < 1024*1024 ? `${(n/1024).toFixed(1)} KB` : `${(n/1024/1024).toFixed(2)} MB`;
const nowStamp = () => new Date().toLocaleString("ko-KR", { hour12: false });

// ─────────────────────────────────────────────────────────────────────────────
//  Wrapper — 모드 토글 (실적 비교 / 주간 변동)
// ─────────────────────────────────────────────────────────────────────────────
function FcstSalesDiff() {
  const [mode, setMode] = useState("sales");
  // ── 공유 상태: "최신 FCST" 파일은 양 모드에서 같은 객체로 본다
  //    (실적비교의 FCST  ≡  주간변동의 금주 FCST)
  const [sharedFcst, setSharedFcst] = useState(null);
  return (
    <div style={{ fontFamily: T.fontStack, color: T.text }}>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
        <ModeToggle mode={mode} onChange={setMode} />
      </div>
      <div style={{ display: mode === "sales" ? "block" : "none" }}>
        <SalesCompareView fcstFile={sharedFcst} setFcstFile={setSharedFcst} />
      </div>
      <div style={{ display: mode === "drift" ? "block" : "none" }}>
        <FcstWeekDrift currFile={sharedFcst} setCurrFile={setSharedFcst} />
      </div>
    </div>
  );
}

function ModeToggle({ mode, onChange }) {
  const tabs = [
    { key: "sales", title: "실적 비교", sub: "FCST → 실제 매출" },
    { key: "drift", title: "주간 변동", sub: "전주 → 금주 FCST" },
  ];
  return (
    <div style={{
      display: "inline-flex", border: `1px solid ${T.border2}`, borderRadius: 4,
      background: T.surface, overflow: "hidden",
    }}>
      {tabs.map((t, i) => {
        const active = mode === t.key;
        return (
          <button key={t.key} onClick={() => onChange(t.key)}
            style={{
              padding: "10px 18px", border: 0,
              borderLeft: i > 0 ? `1px solid ${T.border2}` : 0,
              background: active ? T.brand : T.surface,
              color: active ? "#ffffff" : T.text2,
              cursor: "pointer",
              fontWeight: 700, fontSize: 12,
              letterSpacing: "0.3px",
              textAlign: "left", minWidth: 160,
              transition: "background 0.12s, color 0.12s",
            }}>
            <div style={{ lineHeight: 1.2 }}>{t.title}</div>
            <div style={{
              fontSize: 10, fontWeight: 500, marginTop: 3,
              color: active ? "rgba(255,255,255,0.65)" : T.text3,
              letterSpacing: "0.2px",
            }}>{t.sub}</div>
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  Sales Compare View — FCST vs 실제 매출
// ─────────────────────────────────────────────────────────────────────────────
function SalesCompareView({ fcstFile, setFcstFile }) {
  // fcstFile 은 wrapper 에서 lift-up 된 공유 상태 (주간변동의 금주 FCST 와 동일 객체)
  const [actualFile, setActualFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const [generatedAt, setGeneratedAt] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [dragF, setDragF] = useState(false);
  const [dragA, setDragA] = useState(false);
  const fcstRef = useRef();
  const actualRef = useRef();

  const reset = () => {
    setFcstFile(null); setActualFile(null);
    setError(null); setData(null); setGeneratedAt(null);
    setStatusFilter("all"); setOwnerFilter("all"); setQuery("");
    if (fcstRef.current) fcstRef.current.value = "";
    if (actualRef.current) actualRef.current.value = "";
  };

  const acceptDrop = (e, setter, setDrag) => {
    e.preventDefault(); setDrag(false);
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) { setError(".xlsx 또는 .xlsm 파일만 지원합니다."); return; }
    setter(f); setData(null); setError(null);
  };

  const runCompare = async () => {
    if (!fcstFile || !actualFile) { setError("FCST 파일과 실적 파일 두 개 모두 선택하세요."); return; }
    setLoading(true); setError(null); setData(null);
    try {
      const fd = new FormData();
      fd.append("fcst", fcstFile);
      fd.append("actual", actualFile);
      const res = await axios.post(`${API_URL}/api/fcst-sales/preview`, fd);
      if (res.data.error) { setError(res.data.error); return; }
      setData(res.data); setGeneratedAt(nowStamp());
    } catch (e) {
      setError(`요청 실패: ${e.response?.data?.detail || e.message}`);
    } finally { setLoading(false); }
  };

  const runExport = async () => {
    if (!fcstFile || !actualFile) return;
    setExporting(true); setError(null);
    try {
      const fd = new FormData();
      fd.append("fcst", fcstFile); fd.append("actual", actualFile);
      const res = await axios.post(`${API_URL}/api/fcst-sales/export`, fd, { responseType: "blob" });
      if (res.data.type?.includes("json")) {
        const text = await res.data.text();
        try { setError(JSON.parse(text).error || "내보내기 실패"); } catch { setError("내보내기 실패"); }
        return;
      }
      let fname = "영업FCST_매출비교.xlsx";
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
  const maxOwnerFcst = useMemo(() => Math.max(1, ...owners.map(o => o.fcst)), [owners]);
  const maxCustGap = useMemo(() => Math.max(1, ...(data?.top_customers || []).map(c => Math.abs(c.GAP))), [data]);

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

  // ───────────────────────────────────────────────────────────────────────────
  return (
    <div style={{ fontFamily: T.fontStack, color: T.text }}>
      {/* PAGE HEADER */}
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1 style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.5px", margin: 0 }}>
            영업FCST <span style={{ color: T.text3, fontWeight: 600 }}>vs</span> 실제 매출
          </h1>
          <p style={{ fontSize: 13, color: T.text2, marginTop: 6, marginBottom: 0 }}>
            영업 3개월 FCST(M / M+1 / M+2)와 실적을 <b>업체명 + 파트명</b> 기준 매칭 · 달성률 추적
          </p>
        </div>
        {data && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Pill icon="🗓" label={`기준 월 ${data.target_month}`} tone="info" />
            <Pill icon="🕒" label={generatedAt} tone="muted" />
          </div>
        )}
      </div>

      <Divider />
      {error && <ErrorBanner>{error}</ErrorBanner>}

      {/* INPUT — file slots + actions */}
      <Card style={{ marginBottom: 20 }}>
        <CardLabel>입력 파일</CardLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 24px 1fr", gap: 12, alignItems: "stretch", marginBottom: 14 }}>
          <FileSlot
            label="FCST" accent={T.info} hint="Sales Revenue FCST_영업5실_*.xlsx · 시트 'Sales Revenue' · 3개월 블록"
            file={fcstFile} setter={setFcstFile} inputRef={fcstRef}
            drag={dragF} setDrag={setDragF} onDrop={acceptDrop} clearPreview={() => setData(null)}
          />
          <div style={{ alignSelf: "center", color: T.text3, fontSize: 22, textAlign: "center" }}>↔</div>
          <FileSlot
            label="실적" accent={T.pos} hint="월 실적 raw 엑셀 · 헤더 Month/Customer/MPN/QTY/매출가(RS)"
            file={actualFile} setter={setActualFile} inputRef={actualRef}
            drag={dragA} setDrag={setDragA} onDrop={acceptDrop} clearPreview={() => setData(null)}
          />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <PrimaryBtn onClick={runCompare} disabled={loading || !fcstFile || !actualFile} loading={loading}>
            {loading ? "분석 중…" : "비교 실행"}
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
          {/* KPI ROW */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
            <KpiCard
              label={`FCST 총액 · ${data.target_month}`}
              value={fmtUSD0(data.kpi.total_fcst)} valueSub="USD"
              accent={T.info}
              hint={`${data.kpi.matched + data.kpi.only_fcst}개 (Customer, MPN) 라인`}
            />
            <KpiCard
              label="실제 매출 (영업실적)"
              value={fmtUSD0(data.kpi.total_actual)} valueSub="USD"
              accent={T.pos}
              hint={`실적 Month · ${data.actuals_month_code ?? "—"}`}
            />
            <KpiCard
              label="GAP (실제 − FCST)"
              value={fmtSign(data.kpi.total_gap)}
              accent={data.kpi.total_gap >= 0 ? T.pos : T.neg}
              hint={data.kpi.total_gap >= 0 ? "초과 달성" : "미달"}
            />
            <AchievementKpi
              ach={data.kpi["달성률"]}
              hint={`매칭 ${data.kpi.matched} · 실적만 ${data.kpi.only_actual} · FCST만 ${data.kpi.only_fcst}`}
            />
          </div>

          {/* STATUS DISTRIBUTION */}
          <Card style={{ marginBottom: 16, padding: "12px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: T.text2, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                상태 분포
              </span>
              {Object.entries(data.kpi.counts).filter(([_, n]) => n > 0).map(([k, n]) => {
                const st = STATUS_STYLE[k] || STATUS_STYLE["—"];
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

          {/* DASHBOARDS GRID */}
          <div style={{ display: "grid", gridTemplateColumns: "1.15fr 1fr", gap: 16, marginBottom: 18 }}>
            <Card padding={0}>
              <CardHeader title="담당자별 달성률" subtitle="FCST 규모 큰 순" />
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ background: T.bg, color: T.text2, fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    <Th>담당자</Th>
                    <Th right>FCST</Th>
                    <Th right>실제</Th>
                    <Th right>GAP</Th>
                    <Th>달성률</Th>
                    <Th right>건수</Th>
                  </tr>
                </thead>
                <tbody>
                  {owners.map((o) => (
                    <tr key={o.owner} style={{ borderTop: `1px solid ${T.border}` }}>
                      <Td><strong>{o.owner}</strong></Td>
                      <Td right num>{fmtUSD0(o.fcst)}</Td>
                      <Td right num>{fmtUSD0(o.actual)}</Td>
                      <Td right num color={o.GAP >= 0 ? T.pos : T.neg}>{fmtSign(o.GAP)}</Td>
                      <Td><ProgressBar ach={o["달성률"]} /></Td>
                      <Td right num>
                        {o.items}
                        {o.miss > 0 && <span style={{ marginLeft: 6, color: T.neg, fontSize: 10.5 }}>· 미달 {o.miss}</span>}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card padding={0}>
              <CardHeader title="Customer Top 10" subtitle="절대 GAP 큰 순" />
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ background: T.bg, color: T.text2, fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    <Th>Customer</Th>
                    <Th right>GAP</Th>
                    <Th>분포</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_customers.map((c) => (
                    <tr key={c.customer} style={{ borderTop: `1px solid ${T.border}` }}>
                      <Td title={c.customer}>
                        <div style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          <strong>{c.customer}</strong>
                        </div>
                        <div style={{ color: T.text3, fontSize: 10.5, marginTop: 2 }}>
                          FCST {fmtUSD0(c.fcst)} · 실제 {fmtUSD0(c.actual)} · {fmtPct(c["달성률"])}
                        </div>
                      </Td>
                      <Td right num color={c.GAP >= 0 ? T.pos : T.neg}>{fmtSign(c.GAP)}</Td>
                      <Td><DivergingBar value={c.GAP} max={maxCustGap} /></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>

          {/* DETAIL TABLE */}
          <Card padding={0}>
            <CardHeader
              title="Detail · (Customer, MPN) 매칭 라인"
              subtitle={`${filtered.length.toLocaleString()} / ${data.rows.length.toLocaleString()} 행 표시`}
              right={
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
                </div>
              }
            />
            <div style={{ display: "flex", gap: 10, padding: "10px 16px", borderBottom: `1px solid ${T.border}`, background: T.bg, alignItems: "center" }}>
              <select
                value={ownerFilter} onChange={(e) => setOwnerFilter(e.target.value)}
                style={{
                  padding: "6px 10px", fontSize: 12, border: `1px solid ${T.border2}`,
                  borderRadius: 6, background: T.surface, color: T.text, cursor: "pointer",
                }}
              >
                {ownerOptions.map((o) => <option key={o} value={o}>{o === "all" ? "담당자 전체" : o}</option>)}
              </select>
              <div style={{ position: "relative", flex: 1, maxWidth: 320 }}>
                <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: T.text3, fontSize: 13 }}>🔎</span>
                <input
                  type="text" placeholder="담당자 · Customer · MPN 검색"
                  value={query} onChange={(e) => setQuery(e.target.value)}
                  style={{
                    width: "100%", padding: "6px 10px 6px 30px", fontSize: 12,
                    border: `1px solid ${T.border2}`, borderRadius: 6, background: T.surface, color: T.text,
                  }}
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
                    <ThH right>FCST Qty</ThH>
                    <ThH right>FCST RS</ThH>
                    <ThH right>실제 Qty</ThH>
                    <ThH right>실제 RS</ThH>
                    <ThH right>달성률</ThH>
                    <ThH right>GAP</ThH>
                    <ThH center>상태</ThH>
                    {data.fcst_months.filter(m => m !== data.target_month).map((m) => (
                      <React.Fragment key={m}>
                        <ThH right>{m} Qty</ThH>
                        <ThH right>{m} RS</ThH>
                      </React.Fragment>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr>
                      <td colSpan={10 + 2 * (data.fcst_months.length - 1)}
                        style={{ padding: 40, textAlign: "center", color: T.text3, fontSize: 13 }}>
                        조건에 맞는 라인이 없습니다.
                      </td>
                    </tr>
                  ) : filtered.map((r, i) => {
                    const st = STATUS_STYLE[r["상태"]] || STATUS_STYLE["—"];
                    const zebra = i % 2 === 0 ? T.surface : "#fafbfc";
                    return (
                      <tr key={i} style={{ background: zebra, borderBottom: `1px solid ${T.border}` }}>
                        <Td>{r["담당자"] || "—"}</Td>
                        <Td title={r["Customer"]}>{r["Customer"]}</Td>
                        <Td title={r["MPN"]} mono>{r["MPN"]}</Td>
                        <Td right num>{fmtNum0(r["FCST_Qty"])}</Td>
                        <Td right num>{fmtUSD2(r["FCST_RS_AMT"])}</Td>
                        <Td right num>{fmtNum0(r["실제_Qty"])}</Td>
                        <Td right num>{fmtUSD2(r["실제_RS_AMT"])}</Td>
                        <Td right num color={
                          r["달성률"] == null ? T.text3 :
                          r["달성률"] >= 100 ? T.pos : r["달성률"] >= 80 ? T.warn : T.neg
                        }>{fmtPct(r["달성률"])}</Td>
                        <Td right num color={r["GAP"] >= 0 ? T.pos : T.neg}>{fmtSign(r["GAP"])}</Td>
                        <Td center>
                          <span style={{
                            display: "inline-flex", alignItems: "center", gap: 5,
                            padding: "2px 9px", borderRadius: 999,
                            background: st.bg, color: st.color,
                            border: `1px solid ${st.ring}`,
                            fontWeight: 700, fontSize: 10.5, letterSpacing: "-0.1px",
                          }}>
                            <span style={{ width: 5, height: 5, borderRadius: 999, background: st.dot }} />
                            {r["상태"]}
                          </span>
                        </Td>
                        {data.fcst_months.filter(m => m !== data.target_month).map((m) => (
                          <React.Fragment key={m}>
                            <Td right num color={T.text2}>{fmtNum0(r[`FCST_${m}_Qty`])}</Td>
                            <Td right num color={T.text2}>{fmtUSD2(r[`FCST_${m}_RS_AMT`])}</Td>
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

// ─────────────────────────────────────────────────────────────────────────────
//  Sub-components
// ─────────────────────────────────────────────────────────────────────────────
function Divider() { return <div style={{ height: 1, background: T.border, margin: "16px 0 18px" }} />; }

function Card({ children, style, padding = 16 }) {
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 4,
      padding, ...style,
    }}>
      {children}
    </div>
  );
}

function CardLabel({ children }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 700, color: T.text2,
      textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 10 }}>
      {children}
    </div>
  );
}

function CardHeader({ title, subtitle, right }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "12px 16px", borderBottom: `1px solid ${T.border}`,
    }}>
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: T.text }}>{title}</div>
        {subtitle && <div style={{ fontSize: 11, color: T.text3, marginTop: 2 }}>{subtitle}</div>}
      </div>
      {right}
    </div>
  );
}

function KpiCard({ label, value, valueSub, accent, hint }) {
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 4, padding: "14px 16px", position: "relative", overflow: "hidden",
    }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: accent }} />
      <div style={{ fontSize: 10.5, color: T.text2, fontWeight: 700, marginBottom: 10, letterSpacing: "0.5px", textTransform: "uppercase" }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 6 }}>
        <div style={{ fontSize: 26, fontWeight: 800, color: T.text, lineHeight: 1, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.5px" }}>
          {value}
        </div>
        {valueSub && <div style={{ fontSize: 11, color: T.text3, fontWeight: 600 }}>{valueSub}</div>}
      </div>
      <div style={{ fontSize: 11, color: T.text3, marginTop: 4 }}>{hint}</div>
    </div>
  );
}

function AchievementKpi({ ach, hint }) {
  const v = ach == null ? null : Number(ach);
  const accent = v == null ? T.text3 : v >= 100 ? T.pos : v >= 80 ? T.warn : T.neg;
  const pct = v == null ? 0 : Math.min(100, Math.max(0, v));
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderRadius: 4, padding: "14px 16px", position: "relative", overflow: "hidden",
    }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: accent }} />
      <div style={{ fontSize: 10.5, color: T.text2, fontWeight: 700, marginBottom: 10, letterSpacing: "0.5px", textTransform: "uppercase" }}>달성률</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 26, fontWeight: 800, color: accent, lineHeight: 1, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.5px" }}>
          {v == null ? "—" : v.toFixed(1)}
        </div>
        <div style={{ fontSize: 13, color: accent, fontWeight: 700 }}>%</div>
      </div>
      <div style={{
        position: "relative", height: 6, borderRadius: 999, background: T.border, overflow: "hidden", marginBottom: 6,
      }}>
        <div style={{ position: "absolute", inset: 0, width: `${pct}%`, background: accent, transition: "width 0.4s" }} />
        {/* 100% marker */}
        <div style={{ position: "absolute", left: "100%", top: -3, bottom: -3, width: 1, background: T.text3, transform: "translateX(-0.5px)" }} />
      </div>
      <div style={{ fontSize: 11, color: T.text3 }}>{hint}</div>
    </div>
  );
}

function ProgressBar({ ach }) {
  const v = ach == null ? null : Number(ach);
  const color = v == null ? T.text3 : v >= 100 ? T.pos : v >= 80 ? T.warn : T.neg;
  const pct = v == null ? 0 : Math.min(120, Math.max(0, v));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 140 }}>
      <div style={{
        position: "relative", flex: 1, height: 6, borderRadius: 999,
        background: T.border, overflow: "hidden",
      }}>
        <div style={{
          position: "absolute", inset: 0, width: `${(pct / 120) * 100}%`,
          background: color, transition: "width 0.4s",
        }} />
        <div style={{
          position: "absolute", left: `${(100 / 120) * 100}%`, top: 0, bottom: 0, width: 1,
          background: T.text3, opacity: 0.4,
        }} />
      </div>
      <div style={{
        minWidth: 46, textAlign: "right", fontVariantNumeric: "tabular-nums",
        fontSize: 11.5, fontWeight: 700, color,
      }}>
        {v == null ? "—" : `${v.toFixed(1)}%`}
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
        padding: "10px 20px",
        background: disabled ? "#d4d4d8" : T.accent,
        color: "white", border: 0, borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        fontWeight: 700, fontSize: 12.5, letterSpacing: "0.3px",
        display: "inline-flex", alignItems: "center", gap: 6,
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = T.accentH; }}
      onMouseLeave={(e) => { if (!disabled) e.currentTarget.style.background = T.accent; }}>
      {loading && <Spinner />}
      {children}
    </button>
  );
}

function SuccessBtn({ children, onClick, disabled, loading }) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        padding: "10px 18px",
        background: disabled ? "#d4d4d8" : T.brand,
        color: "white", border: 0, borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        fontWeight: 700, fontSize: 12.5, letterSpacing: "0.3px",
        display: "inline-flex", alignItems: "center", gap: 6,
      }}>
      {loading && <Spinner />}
      {children}
    </button>
  );
}

function GhostBtn({ children, onClick, disabled }) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        padding: "10px 16px",
        background: T.surface, color: T.text2,
        border: `1px solid ${T.border2}`, borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        fontWeight: 600, fontSize: 12.5, letterSpacing: "0.2px",
      }}>
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <span style={{
      display: "inline-block", width: 12, height: 12, borderRadius: 999,
      border: "2px solid rgba(255,255,255,0.4)", borderTopColor: "white",
      animation: "fcst-spin 0.8s linear infinite",
    }}>
      <style>{`@keyframes fcst-spin { to { transform: rotate(360deg); } }`}</style>
    </span>
  );
}

function Pill({ icon, label, tone = "muted" }) {
  const styles = {
    info:   { bg: T.infoBg, color: T.info,  border: "#c8d6e2" },
    muted:  { bg: T.bg,     color: T.text2, border: T.border },
  }[tone];
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
    <div style={{
      display: "inline-flex", border: `1px solid ${T.border2}`, borderRadius: 4, overflow: "hidden",
      background: T.surface,
    }}>
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
            }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function Th({ children, right, center }) {
  return (
    <th style={{
      padding: "9px 12px",
      textAlign: right ? "right" : center ? "center" : "left",
      fontWeight: 700, fontSize: 10.5,
    }}>
      {children}
    </th>
  );
}

function ThH({ children, right, center }) {
  return (
    <th style={{
      padding: "9px 11px", fontWeight: 700, fontSize: 10.5,
      letterSpacing: "0.3px", textTransform: "uppercase",
      textAlign: right ? "right" : center ? "center" : "left",
    }}>
      {children}
    </th>
  );
}

function Td({ children, right, center, color, num, mono, title }) {
  return (
    <td title={title} style={{
      padding: "8px 11px",
      textAlign: right ? "right" : center ? "center" : "left",
      color: color || "inherit",
      fontVariantNumeric: num ? "tabular-nums" : "normal",
      fontFamily: mono ? "ui-monospace, 'Cascadia Code', 'SF Mono', Consolas, monospace" : "inherit",
      fontSize: mono ? 11.5 : "inherit",
      maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    }}>
      {children}
    </td>
  );
}

function ErrorBanner({ children }) {
  return (
    <div style={{
      background: T.negBg, border: `1px solid #fecaca`, color: T.neg,
      padding: "10px 14px", borderRadius: 8, fontSize: 13, marginBottom: 14,
      display: "flex", alignItems: "center", gap: 8,
    }}>
      <span style={{ fontSize: 16 }}>⚠</span>
      {children}
    </div>
  );
}

function EmptyState() {
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "32px 28px", textAlign: "center", background: T.bg, borderBottom: `1px solid ${T.border}` }}>
        <div style={{ fontSize: 32, marginBottom: 8 }}>📊</div>
        <div style={{ fontSize: 15, fontWeight: 700, color: T.text, marginBottom: 4 }}>
          영업 FCST · 매출 비교 대시보드
        </div>
        <div style={{ fontSize: 12.5, color: T.text2 }}>
          FCST 엑셀과 실적 엑셀을 업로드하면 자동으로 매칭 분석 + Summary 가 생성됩니다.
        </div>
      </div>
      <div style={{ padding: "20px 28px", display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
        <Step n="1" title="입력 파일 업로드"
          body="FCST 파일(Sales Revenue 시트 · 3개월 블록)과 월 실적 파일(Customer/MPN/QTY/매출가)을 위 슬롯에 드래그하세요." />
        <Step n="2" title="비교 실행"
          body="(Customer + MPN) 키로 자동 매칭. 업체명 정규화로 '(주)·주식회사' 표기 차이는 자동 흡수됩니다." />
        <Step n="3" title="결과 확인 & 내보내기"
          body="KPI · 담당자별 · Customer Top 10 · Detail 4단 대시보드. 엑셀(Summary + Detail) 다운로드 가능." />
      </div>
      <div style={{ padding: "14px 28px", background: T.bg, borderTop: `1px solid ${T.border}`, fontSize: 11.5, color: T.text2, lineHeight: 1.7 }}>
        <b>상태 분류 기준</b>: 달성(100–120%) · 근접(80–100%) · 미달(&lt;80%) · 초과(&gt;120%) · 매칭누락(실적만 있고 FCST 없음) · 미실현(FCST 있고 실적 없음)
      </div>
    </Card>
  );
}

function Step({ n, title, body }) {
  return (
    <div>
      <div style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 22, height: 22, borderRadius: 999,
        background: T.brand, color: "white", fontSize: 11, fontWeight: 800, marginBottom: 8,
      }}>{n}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 11.5, color: T.text2, lineHeight: 1.5 }}>{body}</div>
    </div>
  );
}

export default FcstSalesDiff;
