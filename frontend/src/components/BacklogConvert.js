import React, { useState, useRef, useMemo } from "react";
import axios from "axios";

const API = process.env.REACT_APP_API_URL || "";

// 마이크론 담당자 메일로 오는 원본 → Backlog Shipment Report 재배열.
// 원본은 회차마다 열 순서·개수가 달라서 헤더 이름으로 찾는다(서버 backlog_convert.py).
// DBC 는 SO# → MPN, FSE·CUST 는 SO# → PO# 순서로 이전 백록에서 끌어온다.
// 후보가 여러 개면 채우지 않고 '확인 필요'로 뽑는다 — 같은 PO# 에 담당자가 둘인 경우가 있다.

const FILL_FIELDS = ["DBC", "FSE", "CUST"];
const CALC_COLS = ["OPEN_ORDER_VALUE", "OPEN COST"];
const NUM_COLS = new Set(["QTY", "OPEN_ORDER_VALUE", "OPEN COST", "DBC", "SAP_NO", "CHANNEL_CODE"]);
const SRC_CLASS = { "SO#": "src-so", MPN: "src-mpn", "PO#": "src-po" };

// 열마다 소수 자리를 정해 준다 — 안 그러면 OPEN COST 가 14.04 / 10.779861 처럼 들쭉날쭉해진다
const DECIMALS = { "OPEN COST": 4, DBC: 2 };

const fmt = (v, col) => {
  if (v == null || v === "") return "";
  if (typeof v === "number") {
    const d = DECIMALS[col];
    return Number(v).toLocaleString(undefined, {
      maximumFractionDigits: d == null ? 6 : d,
      minimumFractionDigits: d == null ? 0 : Math.min(d, 2),
    });
  }
  const m = /^(\d{4}-\d{2}-\d{2})T/.exec(String(v));   // 날짜는 시간 부분을 자른다
  return m ? m[1] : String(v);
};

function BacklogConvert() {
  const [srcFile, setSrcFile] = useState(null);
  const [prevFile, setPrevFile] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dl, setDl] = useState(false);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("rows");
  const srcRef = useRef();
  const prevRef = useRef();

  const send = (fd) => {
    fd.append("file", srcFile);
    if (prevFile) fd.append("prev", prevFile);
    return fd;
  };

  const run = async () => {
    if (!srcFile) return;
    setLoading(true); setError(null); setData(null);
    try {
      const res = await axios.post(`${API}/api/backlog-convert/preview`, send(new FormData()));
      if (res.data.error) setError(res.data.error);
      else { setData(res.data); setTab(res.data.review?.length ? "review" : "rows"); }
    } catch (e) {
      setError("변환 실패: " + (e.response?.data?.detail || e.message));
    }
    setLoading(false);
  };

  const download = async () => {
    if (!srcFile) return;
    setDl(true); setError(null);
    try {
      const res = await axios.post(`${API}/api/backlog-convert/export`, send(new FormData()),
                                   { responseType: "blob" });
      const cd = res.headers["content-disposition"] || "";
      const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(res.data);
      a.download = m ? decodeURIComponent(m[1]) : "Backlog Shipment Report.xlsx";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
    setDl(false);
  };

  const s = data?.summary;
  const fillRows = useMemo(() => {
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
          마이크론 원본을 Backlog Shipment Report 열 순서로 재배열하고, OPEN COST 를 계산해
          DBC·FSE·CUST 를 이전 백록에서 채웁니다.
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="upload-panel">
        <div className="file-slots">
          <Slot label="마이크론 원본" required file={srcFile} inputRef={srcRef} onPick={setSrcFile} />
          <Slot label="이전 백록" file={prevFile} inputRef={prevRef} onPick={setPrevFile}
                hint="DBC·FSE·CUST 를 여기서 끌어옵니다" />
          <div className="slot-actions">
            <button className="primary-btn" onClick={run} disabled={!srcFile || loading}>
              {loading ? "변환 중…" : "변환"}
            </button>
          </div>
          <div className="slot-actions" style={{ marginLeft: "auto" }}>
            <button className="accent-btn" onClick={download} disabled={!srcFile || dl}>
              {dl ? "만드는 중…" : "엑셀 다운로드"}
            </button>
          </div>
        </div>
        <div className="file-slot-hint" style={{ marginTop: 12 }}>
          원본 열 순서·개수가 달라도 헤더 이름으로 찾습니다. 이전 백록을 올리지 않으면
          DBC·FSE·CUST 는 빈칸으로 나가고, 열 순서와 OPEN COST 는 그대로 만들어집니다.
        </div>
      </div>

      {s && (<>
        <div className="sales-summary compact">
          <Card label="행" value={s.row_count} />
          <Card label="출력 열" value={s.output_columns} sub={`원본 ${s.source_columns}열`} />
          <Card label="자동 채움" value={autoFilled(s)} sub="DBC·FSE·CUST 합계" ok />
          <Card label="확인 필요" value={s.review_count} warn={s.review_count > 0} />
        </div>

        {!s.has_prev && (
          <div className="warn-note">
            이전 백록을 올리지 않아 <b>DBC·FSE·CUST 가 모두 빈칸</b>입니다. 열 순서와 OPEN COST 는 정상입니다.
          </div>
        )}
        {s.dropped_optional?.length > 0 && (
          <div className="muted-note">
            원본에 없어서 뺀 열 — <b>{s.dropped_optional.join(", ")}</b>. 나머지 열 순서는 그대로입니다.
          </div>
        )}

        {s.has_prev && (
          <div className="panel">
            <div className="panel-title">자동 채움 결과</div>
            <table className="mini-table">
              <thead>
                <tr>
                  <th> </th>
                  <th>SO# 로</th>
                  <th>MPN 으로</th>
                  <th>PO# 로</th>
                  <th>빈칸</th>
                </tr>
              </thead>
              <tbody>
                {fillRows.map((r) => (
                  <tr key={r.field}>
                    <td>{r.field}</td>
                    <td><b>{r.so.toLocaleString()}</b></td>
                    <td>{r.field === "DBC" ? r.mpn.toLocaleString() : "—"}</td>
                    <td>{r.field === "DBC" ? "—" : r.po.toLocaleString()}</td>
                    <td className={r.blank ? "cell-blank" : undefined}>{r.blank.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="file-slot-hint" style={{ marginTop: 10 }}>
              SO# 로 채운 건 이전 백록에서 맞춰두신 값입니다. MPN·PO# 는 신규 SO 에 쓴 대체 경로이고,
              후보가 둘 이상이면 채우지 않고 확인 필요로 넘깁니다.
            </div>
          </div>
        )}

        <div className="tabs">
          <button className={`tab ${tab === "rows" ? "active" : ""}`} onClick={() => setTab("rows")}>
            결과 미리보기
            <span className="tab-count">
              {Math.min(data.rows.length, s.row_count).toLocaleString()} / {s.row_count.toLocaleString()}
            </span>
          </button>
          <button className={`tab ${tab === "review" ? "active" : ""}`} onClick={() => setTab("review")}>
            확인 필요
            <span className="tab-count">{s.review_count.toLocaleString()}</span>
          </button>
        </div>

        {tab === "review" && (s.review_count === 0
          ? <div className="empty-state ok">확인할 게 없습니다. DBC·FSE·CUST 가 전부 채워졌습니다.</div>
          : <ReviewTable rows={data.review} />)}

        {tab === "rows" && (
          <>
            <div className="table-header">
              <div className="table-info">
                화면에는 앞 <strong>{data.rows.length.toLocaleString()}</strong>행,
                엑셀 다운로드는 전체 <strong>{s.row_count.toLocaleString()}</strong>행입니다.
                {" · "}머리 밑줄 <Legend color="#eab308" /> 계산 열,
                <Legend color="#c43a3a" /> 자동 채움 열
              </div>
            </div>
            <RowTable columns={data.columns} rows={data.rows} fills={data.fills} />
          </>
        )}
      </>)}
    </div>
  );
}

const autoFilled = (s) =>
  FILL_FIELDS.reduce((n, f) => {
    const c = s.fill_counts[f] || {};
    return n + (c["SO#"] || 0) + (c.MPN || 0) + (c["PO#"] || 0);
  }, 0);

const Legend = ({ color }) => (
  <span style={{ display: "inline-block", width: 14, height: 3, background: color,
                 borderRadius: 2, margin: "0 4px 0 6px", verticalAlign: "middle" }} />
);

const RowTable = ({ columns, rows, fills }) => (
  <div className="table-container">
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c}
                className={CALC_COLS.includes(c) ? "th-calc"
                           : FILL_FIELDS.includes(c) ? "th-derived" : undefined}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {columns.map((c) => {
              const isFill = FILL_FIELDS.includes(c);
              const blank = isFill && (r[c] == null || r[c] === "");
              // 배지는 예외에만 — SO# 로 채운 건 기본 경로라 표시하지 않는다
              const src = isFill ? fills?.[i]?.[c] : null;
              const badge = src && src !== "SO#" ? src : null;
              return (
                <td key={c} className={[
                  NUM_COLS.has(c) ? "cell-number" : "",
                  blank ? "cell-blank" : "",
                ].filter(Boolean).join(" ") || undefined}>
                  {blank ? "확인 필요" : fmt(r[c], c)}
                  {badge && <span className={`src-badge ${SRC_CLASS[badge]}`}>{badge}</span>}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const REVIEW_COLS = ["행", "항목", "사유", "후보", "SO", "PURCH_ORDER_NO", "MPN", "DID",
                     "END_CUSTOMER_NAME"];

const ReviewTable = ({ rows }) => (
  <div className="table-container">
    <table className="data-table">
      <thead>
        <tr>{REVIEW_COLS.map((h) => <th key={h}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td className="cell-number">{r.row}</td>
            <td className="cell-blank">{r.field}</td>
            <td>{r.reason}</td>
            <td>{(r.candidates || []).join(", ") || "—"}</td>
            <td>{fmt(r.SO)}</td>
            <td>{fmt(r.PURCH_ORDER_NO)}</td>
            <td>{fmt(r.MPN)}</td>
            <td>{fmt(r.DID)}</td>
            <td>{fmt(r.END_CUSTOMER_NAME)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const Slot = ({ label, file, inputRef, onPick, required, hint }) => (
  <div>
    <div className="file-slot-label">
      {label}{required && <span className="req"> *</span>}
    </div>
    <input ref={inputRef} type="file" accept=".xlsx,.xls" style={{ display: "none" }}
           onChange={(e) => onPick(e.target.files[0])} />
    <button className={`file-btn ${file ? "filled" : ""}`} onClick={() => inputRef.current?.click()}>
      <span>{file ? "✓" : "＋"}</span>
      <span className="fname">{file ? file.name : "파일 선택"}</span>
    </button>
    {/* 힌트가 없어도 자리를 차지해 슬롯 높이를 맞춘다 */}
    <div className="file-slot-hint">{hint && !file ? hint : " "}</div>
  </div>
);

const Card = ({ label, value, sub, warn, ok }) => (
  <div className={`summary-card${ok && value ? " card-gp" : ""}`}>
    <div className="summary-label">{label}</div>
    <div className="summary-value" style={warn && value ? { color: "#c43a3a" } : undefined}>
      {Number(value).toLocaleString()}
    </div>
    {sub && <div className="file-slot-hint" style={{ marginTop: 2 }}>{sub}</div>}
  </div>
);

export default BacklogConvert;
