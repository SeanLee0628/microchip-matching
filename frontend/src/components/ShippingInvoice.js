import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function ShippingInvoice() {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef();

  const handleFile = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setGroups([]);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/shipping-request/parse`, fd);
      if (res.data.error) setError(res.data.error);
      else setGroups(res.data.groups || []);
    } catch (e) {
      setError("실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const downloadInvoice = async (g, format) => {
    const items = g.items.map(it => ({
      part: it.part,
      qty: it.qty,
      price: it.price,
      currency: it.currency,
      price_krw: it.price_krw,
      amount_krw: it.amount_krw,
      amount_usd: it.amount_usd,
      rate: it.rate,
      date: it.date,
    }));
    const payload = {
      customer: g.customer,
      date: g.date,
      rate: g.rate || 1400,
      issue_date: g.date,
      person_in_charge: g.sales,
      items,
    };
    const endpoint = format === "pdf" ? "/api/invoice/generate-pdf" : "/api/invoice/generate";
    try {
      const res = await axios.post(`${API_URL}${endpoint}`, payload, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      const safe = g.customer.replace(/[\\/:*?"<>|]/g, "_");
      a.download = `거래명세서_${safe}_${g.date}_${g.currency}.${format}`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
  };

  const fmt = (v) => v == null ? "-" : Number(v).toLocaleString();
  const fmtUsd = (v) => v == null ? "-" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const fmtUsdPrice = (v) => v == null ? "-" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 5 })}`;
  const fmtKrw = (v) => v == null ? "-" : `₩${Math.round(Number(v)).toLocaleString()}`;
  const fmtKrwPrice = (v) => v == null ? "-" : `₩${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 5 })}`;

  return (
    <div>
      <div className="page-header">
        <h1>출고요청 → 거래명세서 (4실)</h1>
        <p className="subtitle">출고일·고객사·통화별 자동 분할. KRW는 원화만, USD는 매매기준율 적용.</p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ marginBottom: 20, display: "flex", gap: 12, alignItems: "center" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files[0])}
        />
        <button
          onClick={() => fileRef.current.click()}
          disabled={loading}
          style={{ padding: "12px 24px", background: "#3b82f6", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
        >
          {loading ? "분석 중..." : "📥 출고요청내역 엑셀 업로드"}
        </button>
      </div>

      {groups.length > 0 && (
        <div style={{ marginBottom: 14, fontSize: 14, color: "#334155" }}>
          <b>{groups.length}건</b> 거래명세서 생성 가능 (출고일 × 고객 × 통화)
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {groups.map((g, idx) => (
          <div key={idx} style={{ border: "1px solid #e2e8f0", borderRadius: 8, background: "white", padding: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 13, color: "#64748b" }}>{g.date} · 담당 {g.sales || "-"} · 벤더 {g.vendor || "-"}</div>
                <h3 style={{ margin: "4px 0", fontSize: 17 }}>
                  {g.customer}
                  <span style={{ marginLeft: 10, padding: "2px 10px", borderRadius: 12, fontSize: 11, color: "white",
                    background: g.currency === "USD" ? "#3b82f6" : "#10b981" }}>
                    {g.currency}
                  </span>
                </h3>
                <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
                  {g.item_count}개 품목 · 총 수량 {fmt(g.total_qty)}
                  {g.currency === "USD"
                    ? <> · 총 ${fmt(g.total_usd)}</>
                    : <> · 총 {fmtKrw(g.total_krw)}</>}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={() => downloadInvoice(g, "xlsx")}
                  style={{ padding: "8px 14px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
                  📥 엑셀
                </button>
                <button onClick={() => downloadInvoice(g, "pdf")}
                  style={{ padding: "8px 14px", background: "#dc2626", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
                  📄 PDF
                </button>
              </div>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f1f5f9" }}>
                  <th style={{ padding: 6, textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>Part #</th>
                  <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>QTY</th>
                  {g.currency === "USD" ? (
                    <>
                      <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>U/Price ($)</th>
                      <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>Amount ($)</th>
                    </>
                  ) : (
                    <>
                      <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>U/Price (₩)</th>
                      <th style={{ padding: 6, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>Amount (₩)</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {g.items.map((it, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: 6 }}>{it.part}</td>
                    <td style={{ padding: 6, textAlign: "right" }}>{fmt(it.qty)}</td>
                    {g.currency === "USD" ? (
                      <>
                        <td style={{ padding: 6, textAlign: "right" }}>{fmtUsdPrice(it.price)}</td>
                        <td style={{ padding: 6, textAlign: "right", fontWeight: 600 }}>{fmtUsd(it.amount_usd)}</td>
                      </>
                    ) : (
                      <>
                        <td style={{ padding: 6, textAlign: "right" }}>{fmtKrwPrice(it.price_krw)}</td>
                        <td style={{ padding: 6, textAlign: "right", fontWeight: 600 }}>{fmtKrw(it.amount_krw)}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}

export default ShippingInvoice;
