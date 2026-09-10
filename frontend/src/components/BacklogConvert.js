import React, { useState, useRef, useMemo } from "react";
import axios from "axios";

const API = process.env.REACT_APP_API_URL || "";

// 마이크론 담당자 메일로 오는 원본 → Backlog Shipment Report 재배열.
// 원본은 회차마다 열 순서·개수가 달라서 헤더 이름으로 찾는다(서버 backlog_convert.py).
// DBC 는 SO# → MPN, FSE·CUST 는 SO# → PO# 순서로 이전 백록에서 끌어온다.
// 후보가 여러 개면 채우지 않고 '확인 필요'로 뽑는다 — 같은 PO# 에 담당자가 둘인 경우가 있다.

const RED = "#c43a3a";
const SRC_COLOR = { "SO#": "#16a34a", MPN: "#2563eb", "PO#": "#d97706" };
const FILL_FIELDS = ["DBC", "FSE", "CUST"];
const fmt = (v) => {
  if (v == null || v === "") return "";
  if (typeof v === "number") return Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 });
  const s = String(v);
  const m = /^(\d{4}-\d{2}-\d{2})T/.exec(s);   // 날짜는 시간 부분을 잘라 보여준다
  return m ? m[1] : s;
};

function BacklogConvert() {
  const [srcFile, setSrcFile] = useState(null);
  const [prevFile, setPrevFile] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dl, setDl] = useState(false);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("rows");     // rows | review
  const srcRef = useRef();
  const prevRef = useRef();

  const run = async () => {
    if (!srcFile) return;
    setLoading(true); setError(null); setData(null);
    const fd = new FormData();
    fd.append("file", srcFile);
    if (prevFile) fd.append("prev", prevFile);
    try {
      const res = await axios.post(`${API}/api/backlog-convert/preview`, fd);
      if (res.data.error) setError(res.data.error);
      else { setData(res.data); setTab(res.data.review?.length ? "review" : "rows"); }
    } catch (e) { setError("변환 실패: " + (e.response?.data?.detail || e.message)); }
    setLoading(false);
  };

  const download = async () => {
    if (!srcFile) return;
    setDl(true); setError(null);
    const fd = new FormData();
    fd.append("file", srcFile);
    if (prevFile) fd.append("prev", prevFile);
    try {
      const res = await axios.post(`${API}/api/backlog-convert/export`, fd, { responseType: "blob" });
      const cd = res.headers["content-disposition"] || "";
      const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(res.data);
      a.download = m ? decodeURIComponent(m[1]) : "Backlog Shipment Report.xlsx";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { setError("다운로드 실패: " + e.message); }
    setDl(false);
  };

  const s = data?.summary;
  const counts = useMemo(() => {
    if (!s) return [];
    return FILL_FIELDS.map((f) => {
      const c = s.fill_counts[f] || {};
      return { field: f, so: c["SO#"] || 0, mpn: c.MPN || 0, po: c["PO#"] || 0, blank: c[""] || 0 };
    });
  }, [s]);

  return (
    <div>
      <div className="page-header">
        <h1>Backlog 원본 변환</h1>
        <p className="subtitle">
          마이크론 원본 → Backlog Shipment Report 순서로 재배열 · OPEN COST 계산 ·
          DBC·FSE·CUST 를 이전 백록에서 채움
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "flex", gap: 14, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 8 }}>
        <Pick label="1) 마이크론 원본" required file={srcFile} inputRef={srcRef} onPick={setSrcFile} />
        <Pick label="2) 이전 백록 (선택)" file={prevFile} inputRef={prevRef} onPick={setPrevFile}
              hint="DBC·FSE·CUST 를 여기서 끌어옵니다" />
        <button onClick={run} disabled={!srcFile || loading}
                style={{ ...btn, background: srcFile ? "#0a0e12" : "#cbd5e1" }}>
          {loading ? "변환 중…" : "변환"}
        </button>
        <button onClick={download} disabled={!srcFile || dl}
                style={{ ...btn, background: RED, marginLeft: "auto", opacity: srcFile ? 1 : 0.5 }}>
          {dl ? "만드는 중…" : "⬇ 엑셀 다운로드"}
        </button>
      </div>
      <p style={{ fontSize: 12, color: "#64748b", margin: "0 0 18px" }}>
        이전 백록을 올리지 않으면 DBC·FSE·CUST 는 빈칸으로 나갑니다. 열 순서와 OPEN COST 는 그대로 만들어집니다.
      </p>

      {s && (<>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
          <Stat label="행" value={s.row_count} big />
          <Stat label="출력 열" value={s.output_columns} sub={`원본 ${s.source_columns}열`} />
          <Stat label="확인 필요" value={s.review_count} color={s.review_count ? RED : "#16a34a"} />
        </div>

        {s.dropped_optional?.length > 0 && (
          <Note>
            원본에 없어서 뺀 열: <b>{s.dropped_optional.join(", ")}</b> — 순서는 그대로 유지됩니다.
          </Note>
        )}
        {!s.has_prev && <Note warn>이전 백록을 올리지 않아 DBC·FSE·CUST 가 모두 빈칸입니다.</Note>}

        {s.has_prev && (
          <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                        padding: "12px 14px", marginBottom: 14 }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 8 }}>자동 채움 결과</div>
            <table style={{ fontSize: 12.5, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ color: "#64748b" }}>
                  {["", "SO# 로", "MPN 으로", "PO# 로", "빈칸"].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: "4px 14px 4px 0", fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {counts.map((c) => (
                  <tr key={c.field}>
                    <td style={{ padding: "3px 14px 3px 0", fontWeight: 700 }}>{c.field}</td>
                    <td style={{ padding: "3px 14px 3px 0", color: SRC_COLOR["SO#"] }}>{c.so.toLocaleString()}</td>
                    <td style={{ padding: "3px 14px 3px 0", color: SRC_COLOR.MPN }}>
                      {c.field === "DBC" ? c.mpn.toLocaleString() : "—"}
                    </td>
                    <td style={{ padding: "3px 14px 3px 0", color: SRC_COLOR["PO#"] }}>
                      {c.field === "DBC" ? "—" : c.po.toLocaleString()}
                    </td>
                    <td style={{ padding: "3px 14px 3px 0", color: c.blank ? RED : "#94a3b8", fontWeight: c.blank ? 700 : 400 }}>
                      {c.blank.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ display: "flex", gap: 4, borderBottom: "1px solid #e2e8f0", marginBottom: 12 }}>
          <Tab on={tab === "rows"} onClick={() => setTab("rows")}>
            결과 미리보기 ({Math.min(data.rows.length, s.row_count).toLocaleString()} / {s.row_count.toLocaleString()}행)
          </Tab>
          <Tab on={tab === "review"} onClick={() => setTab("review")} alert={s.review_count > 0}>
            확인 필요 {s.review_count}
          </Tab>
        </div>

        {tab === "review" && (s.review_count === 0
          ? <Empty>확인할 게 없습니다. DBC·FSE·CUST 가 전부 채워졌습니다.</Empty>
          : <Review rows={data.review} />)}

        {tab === "rows" && (<>
          {data.rows.length < s.row_count && (
            <div style={{ fontSize: 12, color: "#64748b", marginBottom: 8 }}>
              화면에는 앞 {data.rows.length.toLocaleString()}행만 보여줍니다. 엑셀 다운로드는 전체 {s.row_count.toLocaleString()}행입니다.
            </div>
          )}
          <Rows columns={data.columns} rows={data.rows} fills={data.fills} />
        </>)}
      </>)}
    </div>
  );
}

const Rows = ({ columns, rows, fills }) => (
  <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                overflowX: "auto", maxHeight: "68vh", overflowY: "auto" }}>
    <table style={{ borderCollapse: "separate", borderSpacing: 0, fontSize: 11.5, width: "100%" }}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c} style={{ position: "sticky", top: 0, zIndex: 1,
                                 background: ["OPEN_ORDER_VALUE", "OPEN COST"].includes(c) ? "#8a6d00" : "#0a0e12",
                                 color: "#fff", fontSize: 10.5, fontWeight: 600, whiteSpace: "nowrap",
                                 padding: "8px 9px", textAlign: "left" }}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} style={{ background: i % 2 ? "#fafafa" : "#fff" }}>
            {columns.map((c) => {
              const src = FILL_FIELDS.includes(c) ? fills?.[i]?.[c] : null;
              const blank = FILL_FIELDS.includes(c) && (r[c] == null || r[c] === "");
              return (
                <td key={c} style={{ padding: "6px 9px", borderTop: "1px solid #f1f5f9",
                                     whiteSpace: "nowrap", color: blank ? RED : "#334155" }}>
                  {blank ? "확인 필요" : fmt(r[c])}
                  {src && <sup style={{ marginLeft: 3, fontSize: 8.5, color: SRC_COLOR[src] }}>{src}</sup>}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const Review = ({ rows }) => (
  <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                overflowX: "auto", maxHeight: "68vh", overflowY: "auto" }}>
    <table style={{ borderCollapse: "separate", borderSpacing: 0, fontSize: 12, width: "100%" }}>
      <thead>
        <tr>
          {["행", "항목", "사유", "후보", "SO", "PURCH_ORDER_NO", "MPN", "DID", "END_CUSTOMER_NAME"].map((h) => (
            <th key={h} style={{ position: "sticky", top: 0, background: "#8a6d00", color: "#fff",
                                 fontSize: 10.5, fontWeight: 600, padding: "8px 10px",
                                 textAlign: "left", whiteSpace: "nowrap" }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} style={{ background: i % 2 ? "#fafafa" : "#fff" }}>
            <td style={td}>{r.row}</td>
            <td style={{ ...td, fontWeight: 700, color: RED }}>{r.field}</td>
            <td style={td}>{r.reason}</td>
            <td style={td}>{(r.candidates || []).join(", ") || "—"}</td>
            <td style={td}>{fmt(r.SO)}</td>
            <td style={td}>{fmt(r.PURCH_ORDER_NO)}</td>
            <td style={td}>{fmt(r.MPN)}</td>
            <td style={td}>{fmt(r.DID)}</td>
            <td style={td}>{fmt(r.END_CUSTOMER_NAME)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const td = { padding: "6px 10px", borderTop: "1px solid #f1f5f9", whiteSpace: "nowrap", color: "#334155" };
const btn = { padding: "9px 18px", color: "#fff", border: "none", borderRadius: 8,
              fontSize: 13, fontWeight: 600, cursor: "pointer" };

const Pick = ({ label, file, inputRef, onPick, required, hint }) => (
  <div>
    <div style={{ fontSize: 11.5, color: required ? "#0a0e12" : "#64748b", fontWeight: 600, marginBottom: 4 }}>
      {label}
    </div>
    <input ref={inputRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
           onChange={(e) => onPick(e.target.files[0])} />
    <button onClick={() => inputRef.current?.click()}
            style={{ padding: "8px 14px", background: "#fff", color: "#0a0e12",
                     border: `1px solid ${file ? "#16a34a" : "#cbd5e1"}`, borderRadius: 8,
                     fontSize: 13, cursor: "pointer" }}>
      📄 {file ? "✓ " + (file.name.length > 26 ? file.name.slice(0, 26) + "…" : file.name) : "파일 선택"}
    </button>
    {hint && !file && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 3 }}>{hint}</div>}
  </div>
);

const Tab = ({ on, onClick, children, alert }) => (
  <button onClick={onClick}
          style={{ padding: "8px 14px", fontSize: 13, fontWeight: 600, cursor: "pointer", border: "none",
                   borderBottom: `2px solid ${on ? "#0a0e12" : "transparent"}`, background: "none",
                   color: on ? "#0a0e12" : alert ? RED : "#94a3b8" }}>
    {children}
  </button>
);

const Stat = ({ label, value, color, sub, big }) => (
  <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "10px 16px", minWidth: 96 }}>
    <div style={{ fontSize: 11.5, color: "#64748b" }}>{label}</div>
    <div style={{ fontSize: big ? 22 : 18, fontWeight: 700, color: color || "#0a0e12" }}>
      {Number(value).toLocaleString()}
    </div>
    {sub && <div style={{ fontSize: 11, color: "#94a3b8" }}>{sub}</div>}
  </div>
);

const Note = ({ children, warn }) => (
  <div style={{ background: warn ? "#fffbeb" : "#f8fafc", border: `1px solid ${warn ? "#fde68a" : "#e2e8f0"}`,
                color: warn ? "#92400e" : "#475569", padding: "10px 14px", borderRadius: 8,
                fontSize: 12.5, marginBottom: 12 }}>{children}</div>
);

const Empty = ({ children }) => (
  <div style={{ padding: 40, textAlign: "center", color: "#16a34a", fontSize: 14,
                background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10 }}>{children}</div>
);

export default BacklogConvert;
