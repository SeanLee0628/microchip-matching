import React, { useState, useEffect } from "react";
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

  return (
    <>
      <div className="page-header">
        <h1>거래명세서 (1실)</h1>
        <p className="subtitle">METACOM 형식 · 발행일/담당자 포함</p>
      </div>

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
