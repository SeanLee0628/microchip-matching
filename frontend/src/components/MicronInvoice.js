import React, { useState, useEffect, useRef } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API_URL = process.env.REACT_APP_API_URL || "";

function MicronInvoice() {
  const [rate, setRate] = useState(1400);
  const [rateSource, setRateSource] = useState("");
  const [rateLabel, setRateLabel] = useState("");
  const [customer, setCustomer] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [issueDate, setIssueDate] = useState(new Date().toISOString().slice(0, 10));
  const [person, setPerson] = useState("");
  const [items, setItems] = useState([{ part: "", qty: "", price: "", rate: "" }]);
  const [loading, setLoading] = useState(false);
  // 업로드로 만든 거래명세서 (문서번호별 한 장)
  const [uploaded, setUploaded] = useState([]);
  const [upLoading, setUpLoading] = useState(false);
  const [upError, setUpError] = useState(null);
  const fileRef = useRef();

  useEffect(() => {
    axios.get(`${API_URL}/api/exchange-rate`).then((r) => {
      setRate(r.data.rate);
      setRateSource(r.data.source);
      setRateLabel(r.data.label || "");
    }).catch(() => {});
  }, []);

  const updateItem = (idx, field, value) => {
    const updated = [...items];
    updated[idx][field] = value;
    setItems(updated);
  };

  const addRow = () => setItems([...items, { part: "", qty: "", price: "", rate: "" }]);
  const removeRow = (idx) => setItems(items.filter((_, i) => i !== idx));

  const totalUsd = items.reduce((sum, it) => sum + (Number(it.qty) || 0) * (Number(it.price) || 0), 0);
  const totalKrw = Math.round(items.reduce((sum, it) => {
    const amtUsd = (Number(it.qty) || 0) * (Number(it.price) || 0);
    const itemRate = Number(it.rate) || rate;
    return sum + amtUsd * itemRate;
  }, 0));

  const handleRateChange = (e) => {
    setRate(Number(e.target.value) || 0);
    setRateSource("manual");
    setRateLabel("");
  };

  const generate = async (format) => {
    const validItems = items.filter(it => it.part && it.qty && it.price);
    if (!validItems.length) { alert("품목을 입력하세요"); return; }
    if (!customer) { alert("공급받는자(고객사)를 입력하세요"); return; }
    setLoading(true);
    try {
      const endpoint = format === "pdf" ? "/api/invoice/generate-pdf" : "/api/invoice/generate";
      const res = await axios.post(`${API_URL}${endpoint}`,
        { items: validItems, customer, date, rate, issue_date: issueDate, person_in_charge: person },
        { responseType: "blob" }
      );
      saveAs(res.data, `거래명세서_${customer}_${date}.${format}`);
    } catch (err) {
      alert("생성 실패: " + err.message);
    }
    setLoading(false);
  };

  // 엑셀 서식 코드 → 소수점 자릿수. 백엔드 decimals_from_fmt 와 같은 규칙이라
  // 미리보기에 보이는 자릿수가 받는 파일과 어긋나지 않는다.
  const decimalsOf = (fmt) => {
    if (!fmt) return null;
    const first = String(fmt).split(";")[0]
      .replace(/"[^"]*"/g, "").replace(/\[[^\]]*\]/g, "").replace(/\\./g, "");
    const m = first.match(/\.([0#?]+)/);
    if (m) return m[1].length;
    return /[0#?]/.test(first) ? 0 : null;
  };
  const showNum = (v, sym, fmt, def) => {
    if (v == null) return "-";
    const d = decimalsOf(fmt) ?? def;
    return sym + Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  const safeName = (s) => String(s || "").replace(/[\\/:*?"<>|]/g, "_");

  const handleUpload = async (file) => {
    if (!file) return;
    setUpLoading(true);
    setUpError(null);
    setUploaded([]);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/invoice/upload-preview`, fd, { timeout: 180000 });
      if (res.data.error) setUpError(res.data.error);
      else setUploaded(res.data.invoices || []);
    } catch (e) {
      setUpError("업로드 실패: " + (e.response?.data?.detail || e.message));
    }
    setUpLoading(false);
  };

  const downloadOne = async (inv, format) => {
    setUpError(null);
    try {
      const endpoint = format === "pdf" ? "/api/invoice/generate-pdf" : "/api/invoice/generate";
      const res = await axios.post(`${API_URL}${endpoint}`, inv, { responseType: "blob" });
      saveAs(res.data, `거래명세서_${safeName(inv.customer)}_${inv.doc_no || inv.date || ""}.${format}`);
    } catch (e) {
      setUpError("다운로드 실패: " + e.message);
    }
  };

  const downloadZip = async () => {
    setUpError(null);
    try {
      const res = await axios.post(`${API_URL}/api/invoice/upload-zip`,
        { invoices: uploaded }, { responseType: "blob" });
      saveAs(res.data, `거래명세서_${new Date().toISOString().slice(0, 10)}.zip`);
    } catch (e) {
      setUpError("ZIP 다운로드 실패: " + e.message);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>거래명세서 (1실)</h1>
        <p className="subtitle">METACOM 형식 · 발행일/담당자 포함</p>
      </div>

      {/* ── 업로드: 파일 내용 그대로 거래명세서로 ── */}
      <div style={{ background: "#fff", borderRadius: 8, padding: 20, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.xls" style={{ display: "none" }}
            onChange={(e) => { handleUpload(e.target.files[0]); e.target.value = ""; }} />
          <button onClick={() => fileRef.current.click()} disabled={upLoading}
            style={{ padding: "10px 20px", background: "#3b82f6", color: "#fff", border: 0, borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
            {upLoading ? "처리 중..." : "📤 거래명세서 데이터 업로드"}
          </button>
          <span style={{ fontSize: 12, color: "#64748b" }}>
            문서번호별로 한 장씩 생성 · 파일에 적힌 값과 소수점 표기를 <b>그대로</b> 사용 (환산 안 함)
          </span>
          {uploaded.length > 0 && (
            <button onClick={downloadZip}
              style={{ marginLeft: "auto", padding: "10px 20px", background: "#0f172a", color: "#fff", border: 0, borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
              🗜️ 전체 {uploaded.length}건 ZIP 다운로드
            </button>
          )}
        </div>
        {upError && (
          <div style={{ marginTop: 12, padding: "10px 12px", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 6, color: "#b91c1c", fontSize: 13 }}>
            {upError}
          </div>
        )}
      </div>

      {uploaded.map((inv, idx) => (
        <div key={idx} style={{ background: "#fff", borderRadius: 8, padding: 20, marginBottom: 16, border: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "2px solid #f1f5f9", paddingBottom: 10, marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 17 }}>{inv.customer || "(고객사 없음)"}</h3>
              <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
                문서번호 <b style={{ color: "#0f172a" }}>{inv.doc_no || "-"}</b>
                {" · "}발행일 {inv.issue_date || "-"}
                {" · "}담당자 {inv.person_in_charge || "-"}
                {" · "}품목 {inv.item_count}건
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => downloadOne(inv, "xlsx")}
                style={{ background: "#10b981", color: "#fff", border: "none", padding: "8px 18px", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
                📥 엑셀
              </button>
              <button onClick={() => downloadOne(inv, "pdf")}
                style={{ background: "#dc2626", color: "#fff", border: "none", padding: "8px 18px", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
                📄 PDF
              </button>
            </div>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, tableLayout: "fixed" }}>
            <thead>
              <tr style={{ background: "#1a1a2e", color: "#fff" }}>
                <th style={{ padding: 6, width: 36 }}>No.</th>
                <th style={{ padding: 6 }}>Part #</th>
                <th style={{ padding: 6, width: 70 }}>QTY</th>
                <th style={{ padding: 6, width: 90 }}>U/PRICE ($)</th>
                <th style={{ padding: 6, width: 100 }}>Amount ($)</th>
                <th style={{ padding: 6, width: 80 }}>RATE</th>
                <th style={{ padding: 6, width: 120 }}>U/PRICE (₩)</th>
                <th style={{ padding: 6, width: 130 }}>AMOUNT (₩)</th>
              </tr>
            </thead>
            <tbody>
              {inv.items.map((it, i) => {
                const f = it.fmt || {};
                return (
                  <tr key={i} style={{ borderBottom: "1px solid #f0f0f0" }}>
                    <td style={{ padding: 4, textAlign: "center", color: "#999" }}>{i + 1}</td>
                    <td style={{ padding: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.part}</td>
                    <td style={{ padding: 4, textAlign: "right" }}>{Number(it.qty).toLocaleString()}</td>
                    <td style={{ padding: 4, textAlign: "right" }}>{showNum(it.price, "$", f.price_usd, 2)}</td>
                    <td style={{ padding: 4, textAlign: "right", fontWeight: 600 }}>{showNum(it.amount_usd, "$", f.amount_usd, 2)}</td>
                    <td style={{ padding: 4, textAlign: "center" }}>{showNum(it.rate, "", f.rate, 2)}</td>
                    <td style={{ padding: 4, textAlign: "right" }}>{showNum(it.price_krw, "₩", f.price_krw, 2)}</td>
                    <td style={{ padding: 4, textAlign: "right", fontWeight: 600 }}>{showNum(it.amount_krw, "₩", f.amount_krw, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr style={{ background: "#f9f9f9", fontWeight: 700 }}>
                <td colSpan={4} style={{ padding: 6, textAlign: "right" }}>소계</td>
                <td style={{ padding: 6, textAlign: "right" }}>{showNum(inv.totals.sub_usd, "$", null, 2)}</td>
                <td />
                <td style={{ padding: 6, textAlign: "right" }}>소계</td>
                <td style={{ padding: 6, textAlign: "right" }}>{showNum(inv.totals.sub_krw, "₩", null, 0)}</td>
              </tr>
              <tr style={{ background: "#fff3cd", fontWeight: 700, fontSize: 12 }}>
                <td colSpan={4} style={{ padding: 6, textAlign: "right" }}>합계 (VAT 포함)</td>
                <td style={{ padding: 6, textAlign: "right" }}>{showNum(inv.totals.total_usd, "$", null, 2)}</td>
                <td />
                <td style={{ padding: 6, textAlign: "right" }}>합계</td>
                <td style={{ padding: 6, textAlign: "right" }}>{showNum(inv.totals.total_krw, "₩", null, 0)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      ))}

      <div style={{ background: "#fff", borderRadius: 8, padding: 20, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>공급받는자 (고객사)</label>
            <input style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="METACOM" />
          </div>
          <div style={{ width: 150 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>출고일자</label>
            <input type="date" style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div style={{ width: 150 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>발행일</label>
            <input type="date" style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={issueDate} onChange={(e) => setIssueDate(e.target.value)} />
          </div>
          <div style={{ width: 200 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>담당자</label>
            <input style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={person} onChange={(e) => setPerson(e.target.value)} placeholder="email@unitrontech.com" />
          </div>
          <div style={{ width: 150 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>
              환율 (USD/KRW) <span style={{ fontSize: 10, color: rateSource === "koreaexim" ? "#059669" : rateSource === "frankfurter" ? "#3b82f6" : "#e67e22" }}>
                {rateLabel || (rateSource === "exchangerate-api" ? "실시간" : rateSource === "manual" ? "수동" : "수동")}
              </span>
            </label>
            <input type="number" step="0.01" min="0" style={{ width: "100%", padding: "8px 10px", border: "1px solid #f59e0b", borderRadius: 4, fontSize: 13, background: "#fffbeb" }}
              value={rate} onChange={handleRateChange} title="직접 수정 가능" />
          </div>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, tableLayout: "fixed" }}>
          <thead>
            <tr style={{ background: "#1a1a2e", color: "#fff", fontSize: 11 }}>
              <th style={{ padding: 6, width: 36 }}>No.</th>
              <th style={{ padding: 6 }}>Part #</th>
              <th style={{ padding: 6, width: 70 }}>QTY</th>
              <th style={{ padding: 6, width: 90 }}>U/PRICE ($)</th>
              <th style={{ padding: 6, width: 100 }}>Amount ($)</th>
              <th style={{ padding: 6, width: 80 }}>RATE</th>
              <th style={{ padding: 6, width: 110 }}>U/PRICE (₩)</th>
              <th style={{ padding: 6, width: 130 }}>AMOUNT (₩)</th>
              <th style={{ padding: 6, width: 32 }}></th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => {
              const qty = Number(it.qty) || 0;
              const price = Number(it.price) || 0;
              const amtUsd = qty * price;
              const itemRate = Number(it.rate) || rate;
              const priceKrw = price * itemRate;
              const amtKrw = amtUsd * itemRate;
              return (
                <tr key={i} style={{ borderBottom: "1px solid #f0f0f0" }}>
                  <td style={{ padding: 4, textAlign: "center", color: "#999" }}>{i + 1}</td>
                  <td style={{ padding: 4 }}>
                    <input style={{ width: "100%", padding: "4px 6px", border: "1px solid #eee", borderRadius: 3, fontSize: 11, boxSizing: "border-box" }}
                      value={it.part} onChange={(e) => updateItem(i, "part", e.target.value)} placeholder="MT25QU512ABB8ESF-0AAT" />
                  </td>
                  <td style={{ padding: 4 }}>
                    <input type="number" style={{ width: "100%", padding: "4px 6px", border: "1px solid #eee", borderRadius: 3, fontSize: 11, textAlign: "right", boxSizing: "border-box" }}
                      value={it.qty} onChange={(e) => updateItem(i, "qty", e.target.value)} placeholder="0" />
                  </td>
                  <td style={{ padding: 4 }}>
                    <input type="number" step="0.01" style={{ width: "100%", padding: "4px 6px", border: "1px solid #eee", borderRadius: 3, fontSize: 11, textAlign: "right", boxSizing: "border-box" }}
                      value={it.price} onChange={(e) => updateItem(i, "price", e.target.value)} placeholder="0.00" />
                  </td>
                  <td style={{ padding: 4, textAlign: "right", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{amtUsd ? amtUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "-"}</td>
                  <td style={{ padding: 4 }}>
                    <input type="number" step="0.01" style={{ width: "100%", padding: "4px 6px", border: "1px solid #fde68a", background: "#fffbeb", borderRadius: 3, fontSize: 11, textAlign: "center", boxSizing: "border-box" }}
                      value={it.rate} onChange={(e) => updateItem(i, "rate", e.target.value)} placeholder={String(rate)} title="비워두면 상단 환율 사용" />
                  </td>
                  <td style={{ padding: 4, textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{priceKrw ? priceKrw.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "-"}</td>
                  <td style={{ padding: 4, textAlign: "right", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{amtKrw ? Math.round(amtKrw).toLocaleString() : "-"}</td>
                  <td style={{ padding: 4 }}>
                    {items.length > 1 && <button onClick={() => removeRow(i)} style={{ background: "none", border: "none", color: "#ccc", cursor: "pointer", fontSize: 14 }}>×</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr style={{ background: "#f9f9f9", fontWeight: 700, fontSize: 11 }}>
              <td colSpan={4} style={{ padding: 6, textAlign: "right" }}>소계</td>
              <td style={{ padding: 6, textAlign: "right" }}>${totalUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
              <td></td>
              <td style={{ padding: 6, textAlign: "right" }}>소계</td>
              <td style={{ padding: 6, textAlign: "right" }}>₩{totalKrw.toLocaleString()}</td>
              <td></td>
            </tr>
            <tr style={{ background: "#fff3cd", fontWeight: 700, fontSize: 12 }}>
              <td colSpan={4} style={{ padding: 6, textAlign: "right" }}>합계 (VAT 포함)</td>
              <td style={{ padding: 6, textAlign: "right" }}>${(totalUsd * 1.1).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
              <td></td>
              <td style={{ padding: 6, textAlign: "right" }}>합계</td>
              <td style={{ padding: 6, textAlign: "right" }}>₩{Math.round(totalKrw * 1.1).toLocaleString()}</td>
              <td></td>
            </tr>
          </tfoot>
        </table>

        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 16 }}>
          <button onClick={addRow} style={{ background: "#f5f5f5", border: "1px solid #ddd", padding: "6px 16px", borderRadius: 4, fontSize: 13, cursor: "pointer" }}>+ 행 추가</button>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => generate("xlsx")} disabled={loading}
              style={{ background: "#10b981", color: "#fff", border: "none", padding: "8px 24px", borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
              {loading ? "생성 중..." : "📥 엑셀 다운로드"}
            </button>
            <button onClick={() => generate("pdf")} disabled={loading}
              style={{ background: "#dc2626", color: "#fff", border: "none", padding: "8px 24px", borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
              📄 PDF 다운로드
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

export default MicronInvoice;
