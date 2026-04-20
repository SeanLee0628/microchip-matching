import React, { useState, useEffect } from "react";
import axios from "axios";
import { saveAs } from "file-saver";

const API_URL = process.env.REACT_APP_API_URL || "";

function Invoice() {
  const [rate, setRate] = useState(1400);
  const [rateSource, setRateSource] = useState("");
  const [customer, setCustomer] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [items, setItems] = useState([{ part: "", qty: "", price: "" }]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    axios.get(`${API_URL}/api/exchange-rate`).then((r) => {
      setRate(r.data.rate);
      setRateSource(r.data.source);
    }).catch(() => {});
  }, []);

  const updateItem = (idx, field, value) => {
    const updated = [...items];
    updated[idx][field] = value;
    setItems(updated);
  };

  const addRow = () => setItems([...items, { part: "", qty: "", price: "" }]);
  const removeRow = (idx) => setItems(items.filter((_, i) => i !== idx));

  const totalUsd = items.reduce((sum, it) => sum + (Number(it.qty) || 0) * (Number(it.price) || 0), 0);
  const totalKrw = Math.round(totalUsd * rate);

  const generate = async () => {
    const validItems = items.filter(it => it.part && it.qty && it.price);
    if (!validItems.length) { alert("품목을 입력하세요"); return; }
    setLoading(true);
    try {
      const res = await axios.post(`${API_URL}/api/invoice/generate`,
        { items: validItems, customer, date, rate },
        { responseType: "blob" }
      );
      saveAs(res.data, `거래명세서_${date}.xlsx`);
    } catch (err) {
      alert("생성 실패");
    }
    setLoading(false);
  };

  return (
    <>
      <div className="page-header">
        <h1>거래명세서</h1>
        <p className="subtitle">파트명/수량/외화단가 입력 → 환율 자동 적용 → 엑셀 생성</p>
      </div>

      <div style={{ background: "#fff", borderRadius: 8, padding: 20, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>공급받는자 (고객사)</label>
            <input style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="(주)글로시스" />
          </div>
          <div style={{ width: 150 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>날짜</label>
            <input type="date" style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div style={{ width: 150 }}>
            <label style={{ fontSize: 12, color: "#888", display: "block", marginBottom: 4 }}>
              환율 (USD/KRW) <span style={{ fontSize: 10, color: rateSource === "exchangerate-api" ? "#27ae60" : "#e67e22" }}>
                {rateSource === "exchangerate-api" ? "실시간" : "수동"}
              </span>
            </label>
            <input type="number" step="0.1" style={{ width: "100%", padding: "8px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 13 }}
              value={rate} onChange={(e) => setRate(Number(e.target.value))} />
          </div>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "#1a1a2e", color: "#fff" }}>
              <th style={{ padding: 8, width: 40 }}>No.</th>
              <th style={{ padding: 8 }}>Part #</th>
              <th style={{ padding: 8, width: 80 }}>QTY</th>
              <th style={{ padding: 8, width: 110 }}>U/PRICE ($)</th>
              <th style={{ padding: 8, width: 120 }}>Amount ($)</th>
              <th style={{ padding: 8, width: 100 }}>RATE</th>
              <th style={{ padding: 8, width: 120 }}>U/PRICE (₩)</th>
              <th style={{ padding: 8, width: 140 }}>AMOUNT (₩)</th>
              <th style={{ padding: 8, width: 40 }}></th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => {
              const qty = Number(it.qty) || 0;
              const price = Number(it.price) || 0;
              const amtUsd = qty * price;
              const priceKrw = price * rate;
              const amtKrw = amtUsd * rate;
              return (
                <tr key={i} style={{ borderBottom: "1px solid #f0f0f0" }}>
                  <td style={{ padding: 6, textAlign: "center", color: "#999" }}>{i + 1}</td>
                  <td style={{ padding: 6 }}>
                    <input style={{ width: "100%", padding: 6, border: "1px solid #eee", borderRadius: 3, fontSize: 13 }}
                      value={it.part} onChange={(e) => updateItem(i, "part", e.target.value)} placeholder="NEO-M8L-06B" />
                  </td>
                  <td style={{ padding: 6 }}>
                    <input type="number" style={{ width: "100%", padding: 6, border: "1px solid #eee", borderRadius: 3, fontSize: 13, textAlign: "right" }}
                      value={it.qty} onChange={(e) => updateItem(i, "qty", e.target.value)} placeholder="0" />
                  </td>
                  <td style={{ padding: 6 }}>
                    <input type="number" step="0.01" style={{ width: "100%", padding: 6, border: "1px solid #eee", borderRadius: 3, fontSize: 13, textAlign: "right" }}
                      value={it.price} onChange={(e) => updateItem(i, "price", e.target.value)} placeholder="0.00" />
                  </td>
                  <td style={{ padding: 6, textAlign: "right", fontWeight: 600 }}>{amtUsd ? amtUsd.toLocaleString() : "-"}</td>
                  <td style={{ padding: 6, textAlign: "center", color: "#888" }}>{rate}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>{priceKrw ? priceKrw.toLocaleString(undefined, { maximumFractionDigits: 1 }) : "-"}</td>
                  <td style={{ padding: 6, textAlign: "right", fontWeight: 600 }}>{amtKrw ? Math.round(amtKrw).toLocaleString() : "-"}</td>
                  <td style={{ padding: 6 }}>
                    {items.length > 1 && <button onClick={() => removeRow(i)} style={{ background: "none", border: "none", color: "#ccc", cursor: "pointer", fontSize: 16 }}>×</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr style={{ background: "#f9f9f9", fontWeight: 700 }}>
              <td colSpan={4} style={{ padding: 8, textAlign: "right" }}>소계</td>
              <td style={{ padding: 8, textAlign: "right" }}>${totalUsd.toLocaleString()}</td>
              <td></td>
              <td style={{ padding: 8, textAlign: "right" }}>소계</td>
              <td style={{ padding: 8, textAlign: "right" }}>₩{totalKrw.toLocaleString()}</td>
              <td></td>
            </tr>
            <tr style={{ background: "#fff3cd", fontWeight: 700, fontSize: 14 }}>
              <td colSpan={4} style={{ padding: 8, textAlign: "right" }}>합계 (VAT 포함)</td>
              <td style={{ padding: 8, textAlign: "right" }}>${Math.round(totalUsd * 1.1).toLocaleString()}</td>
              <td></td>
              <td style={{ padding: 8, textAlign: "right" }}>합계</td>
              <td style={{ padding: 8, textAlign: "right" }}>₩{Math.round(totalKrw * 1.1).toLocaleString()}</td>
              <td></td>
            </tr>
          </tfoot>
        </table>

        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 16 }}>
          <button onClick={addRow} style={{ background: "#f5f5f5", border: "1px solid #ddd", padding: "6px 16px", borderRadius: 4, fontSize: 13, cursor: "pointer" }}>+ 행 추가</button>
          <button onClick={generate} disabled={loading}
            style={{ background: "#10b981", color: "#fff", border: "none", padding: "8px 24px", borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
            {loading ? "생성 중..." : "엑셀 거래명세서 다운로드"}
          </button>
        </div>
      </div>
    </>
  );
}

export default Invoice;
