import React, { useState } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

const Cell = ({ value, fmt, editable, onChange, bold }) => {
  const display = (v) => {
    if (v === null || v === undefined || v === "") return "-";
    if (fmt === "money") return `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (fmt === "money0") return `$${Math.round(Number(v)).toLocaleString()}`;
    if (fmt === "pct") return `${Number(v).toFixed(2)}%`;
    if (fmt === "qty") return Number(v).toLocaleString();
    return String(v);
  };
  return (
    <td style={{ padding: 8, borderBottom: "1px solid #e2e8f0", fontWeight: bold ? 700 : 400, whiteSpace: "nowrap" }}>
      {editable ? (
        <input
          type="number"
          step="0.01"
          value={value || ""}
          onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
          style={{ width: 90, padding: 4, border: "1px solid #cbd5e1", borderRadius: 3, fontSize: 12 }}
        />
      ) : display(value)}
    </td>
  );
};

function PoRequest() {
  const [form, setForm] = useState({
    mpn: "",
    customer: "",
    qty: "",
    sales: "",
    package: "Pallet",
    uni_crd: "",
    customer_delivery: "",
    quote_price: "",
    resale_price: "",
    po_customer: "",
    real_end_customer: "",
    payment_term: "",
    remark: "",
    customer_po_balance: "",
  });
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const num = (v) => {
    const n = parseFloat(v);
    return isNaN(n) ? 0 : n;
  };

  const setField = (k, v) => setForm({ ...form, [k]: v });

  const loadPreview = async () => {
    setError(null);
    if (!form.mpn || !form.customer || !form.qty) {
      setError("MPN · 고객 · 수량은 필수입니다.");
      return;
    }
    setLoading(true);
    try {
      const res = await axios.post(`${API_URL}/api/po-request/preview`, {
        ...form,
        qty: num(form.qty),
        quote_price: form.quote_price ? num(form.quote_price) : null,
        resale_price: form.resale_price ? num(form.resale_price) : null,
        customer_po_balance: form.customer_po_balance ? num(form.customer_po_balance) : 0,
      });
      if (res.data.error) {
        setError(res.data.error);
      } else {
        setPreview(res.data);
      }
    } catch (e) {
      setError("조회 실패: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const updatePreviewField = (path, value) => {
    const next = JSON.parse(JSON.stringify(preview));
    const parts = path.split(".");
    let obj = next;
    for (let i = 0; i < parts.length - 1; i++) obj = obj[parts[i]];
    const last = parts[parts.length - 1];
    obj[last] = value;

    // 재계산
    const qty = num(next.input.qty);
    const b = num(next.table1.b_price);
    const r = num(next.table1.resale_price);
    next.table1.buying_amt = Math.round(b * qty * 100) / 100;
    next.table1.resale_amt = Math.round(r * qty * 100) / 100;
    next.table1.gp_amt = Math.round((next.table1.resale_amt - next.table1.buying_amt) * 100) / 100;
    next.table1.gp_pct = next.table1.resale_amt ? Math.round(next.table1.gp_amt / next.table1.resale_amt * 10000) / 100 : 0;

    // 표3 재계산
    let inv = num(next.table2.inventory_qty);
    next.table3.inventory_start = inv;
    next.table3.months.forEach((m, i) => {
      const buy = i === 0 ? qty : num(m.buy);
      m.buy = buy;
      const sell = num(m.sell);
      m.end_inv = inv + buy - sell;
      inv = m.end_inv;
    });

    setPreview(next);
  };

  const downloadExcel = async () => {
    try {
      const res = await axios.post(`${API_URL}/api/po-request/generate`, preview, {
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `PO_Request_${preview.input.mpn}_${preview.input.date}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>발주요청서 생성</h1>
        <p className="subtitle">MPN · 고객 · 수량 입력 → 재고/백로그 자동 집계 → 발주요청서 엑셀 생성</p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* 입력 폼 */}
      <div style={{ background: "#f8fafc", padding: 20, borderRadius: 8, marginBottom: 20 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
          {[
            { label: "MPN (Part Name 또는 AUO P/N)", k: "mpn", required: true, placeholder: "C129HAX01.1 or 97.31C01.003" },
            { label: "고객", k: "customer", required: true, placeholder: "Denso" },
            { label: "수량 (Qty)", k: "qty", type: "number", required: true },
            { label: "담당 (Sales)", k: "sales", required: true, placeholder: "Mason" },
            { label: "Package", k: "package" },
            { label: "UNI CRD (AUO 요청납기)", k: "uni_crd", type: "date" },
            { label: "고객사 납품일", k: "customer_delivery", type: "date" },
            { label: "Quote Price ($)", k: "quote_price", type: "number", placeholder: "자동" },
            { label: "Resale Price ($)", k: "resale_price", type: "number", placeholder: "자동 (B/P×1.1)" },
            { label: "PO customer", k: "po_customer", placeholder: "Denso Korea" },
            { label: "Real End Customer", k: "real_end_customer", placeholder: "Denso Korea" },
            { label: "결재조건", k: "payment_term", placeholder: "월 마감 후 45일" },
            { label: "Customer PO Balance", k: "customer_po_balance", type: "number", placeholder: "자동 (override)" },
            { label: "Remark", k: "remark" },
          ].map((f) => (
            <label key={f.k} style={{ display: "flex", flexDirection: "column", fontSize: 12, color: "#64748b", minWidth: 120 }}>
              {f.label} {f.required && <span style={{ color: "#ef4444" }}>*</span>}
              <input
                type={f.type || "text"}
                value={form[f.k]}
                onChange={(e) => setField(f.k, e.target.value)}
                placeholder={f.placeholder}
                style={{ padding: 7, border: "1px solid #cbd5e1", borderRadius: 4, marginTop: 4, fontSize: 13 }}
              />
            </label>
          ))}
        </div>
        <div style={{ marginTop: 14 }}>
          <button
            onClick={loadPreview}
            disabled={loading}
            style={{ padding: "10px 24px", background: "#3b82f6", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
          >
            {loading ? "계산 중..." : "미리보기"}
          </button>
          {preview && (
            <button
              onClick={downloadExcel}
              style={{ padding: "10px 24px", background: "#10b981", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600, marginLeft: 8 }}
            >
              📥 엑셀 다운로드
            </button>
          )}
        </div>
      </div>

      {preview && (
        <>
          {/* 표1 */}
          <h3 style={{ marginTop: 24 }}>표1. 발주 요청</h3>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#d9ead3" }}>
                  {["Date", "담당 Sales", "MPN", "Package", "Qty", "UNI CRD", "고객사 납품일", "Quote Price", "Resale Price", "Buying AMT", "Resale AMT", "GP AMT", "GPM", "PO customer", "Real End Customer", "결재조건", "Remark"].map(h => (
                    <th key={h} style={{ padding: 8, textAlign: "center", borderBottom: "2px solid #cbd5e1", whiteSpace: "nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <Cell value={preview.input.date} />
                  <Cell value={preview.input.sales} />
                  <Cell value={preview.input.mpn} />
                  <Cell value={preview.input.package} />
                  <Cell value={preview.input.qty} fmt="qty" />
                  <Cell value={preview.input.uni_crd} />
                  <Cell value={preview.input.customer_delivery} />
                  <Cell value={preview.input.quote_price} fmt="money" editable onChange={(v) => updatePreviewField("input.quote_price", v)} />
                  <Cell value={preview.table1.resale_price} fmt="money" editable onChange={(v) => updatePreviewField("table1.resale_price", v)} />
                  <Cell value={preview.table1.buying_amt} fmt="money" />
                  <Cell value={preview.table1.resale_amt} fmt="money" />
                  <Cell value={preview.table1.gp_amt} fmt="money0" />
                  <Cell value={preview.table1.gp_pct} fmt="pct" />
                  <Cell value={preview.input.po_customer} />
                  <Cell value={preview.input.real_end_customer} />
                  <Cell value={preview.input.payment_term} />
                  <Cell value={preview.input.remark} />
                </tr>
                <tr style={{ background: "#fff2cc" }}>
                  <Cell value="" />
                  <Cell value="Sum" bold />
                  <Cell value="" /><Cell value="" />
                  <Cell value={preview.input.qty} fmt="qty" bold />
                  <Cell value="" /><Cell value="" /><Cell value="" /><Cell value="" />
                  <Cell value={preview.table1.buying_amt} fmt="money" bold />
                  <Cell value={preview.table1.resale_amt} fmt="money" bold />
                  <Cell value={preview.table1.gp_amt} fmt="money0" bold />
                  <Cell value={preview.table1.gp_pct} fmt="pct" bold />
                  <Cell value="" /><Cell value="" /><Cell value="" /><Cell value="" />
                </tr>
              </tbody>
            </table>
          </div>

          {/* 표2 */}
          <h3 style={{ marginTop: 24 }}>표2. 재고·백로그 스냅샷</h3>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#d9ead3" }}>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Sales</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Date</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>PART NO.</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Customer</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Customer<br/>PO Balance</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Inventory<br/>Total (Qty)</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>B/Price</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Inventory<br/>AMT</th>
                  <th colSpan="4" style={{ padding: 6, borderBottom: "1px solid #cbd5e1", border: "1px solid #cbd5e1" }}>매출 계획 (month)</th>
                  <th colSpan="3" style={{ padding: 6, borderBottom: "1px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Aging Inventory</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Backlog<br/>Total (Qty)</th>
                  <th rowSpan="2" style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Avg3M<br/>Resale (Qty)</th>
                  <th colSpan="3" style={{ padding: 6, borderBottom: "1px solid #cbd5e1", border: "1px solid #cbd5e1" }}>Requested PO Q'ty</th>
                </tr>
                <tr style={{ background: "#d9ead3" }}>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>{preview.table2.plan_months[0]?.label}</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>{preview.table2.plan_months[1]?.label}</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>{preview.table2.plan_months[2]?.label}</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>+3M</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>1M</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>2M</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>3M~6M</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>This Month<br/>({preview.table2.plan_months[0]?.label})</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>{preview.table2.plan_months[1]?.label}</th>
                  <th style={{ padding: 6, borderBottom: "2px solid #cbd5e1", border: "1px solid #cbd5e1" }}>{preview.table2.plan_months[2]?.label}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <Cell value={preview.input.sales} />
                  <Cell value={preview.input.date} />
                  <Cell value={preview.input.mpn} />
                  <Cell value={preview.input.customer} />
                  <Cell value={preview.input.customer_po_balance} fmt="qty" editable onChange={(v) => updatePreviewField("input.customer_po_balance", v)} />
                  <Cell value={preview.table2.inventory_qty} fmt="qty" editable onChange={(v) => updatePreviewField("table2.inventory_qty", v)} />
                  <Cell value={preview.table2.b_price} fmt="money" editable onChange={(v) => updatePreviewField("table2.b_price", v)} />
                  <Cell value={preview.table2.inventory_amt} fmt="money" />
                  <Cell value={preview.table2.plan_months[0]?.qty} fmt="qty" editable onChange={(v) => updatePreviewField("table2.plan_months.0.qty", v)} />
                  <Cell value={preview.table2.plan_months[1]?.qty} fmt="qty" editable onChange={(v) => updatePreviewField("table2.plan_months.1.qty", v)} />
                  <Cell value={preview.table2.plan_months[2]?.qty} fmt="qty" editable onChange={(v) => updatePreviewField("table2.plan_months.2.qty", v)} />
                  <Cell value={preview.table2.plus_3m_qty ?? "-"} fmt={preview.table2.plus_3m_qty ? "qty" : null} />
                  <Cell value={preview.table2.aging["1M"] || "-"} fmt={preview.table2.aging["1M"] ? "qty" : null} />
                  <Cell value={preview.table2.aging["2M"] || "-"} fmt={preview.table2.aging["2M"] ? "qty" : null} />
                  <Cell value={preview.table2.aging["3M_6M"] || "-"} fmt={preview.table2.aging["3M_6M"] ? "qty" : null} />
                  <Cell value={preview.table2.backlog_qty || "-"} fmt={preview.table2.backlog_qty ? "qty" : null} />
                  <Cell value={preview.table2.avg_3m} fmt="qty" />
                  <Cell value={preview.table2.requested_po_qty?.[0]} fmt="qty" editable onChange={(v) => updatePreviewField("table2.requested_po_qty.0", v)} />
                  <Cell value={preview.table2.requested_po_qty?.[1]} fmt="qty" editable onChange={(v) => updatePreviewField("table2.requested_po_qty.1", v)} />
                  <Cell value={preview.table2.requested_po_qty?.[2]} fmt="qty" editable onChange={(v) => updatePreviewField("table2.requested_po_qty.2", v)} />
                </tr>
                <tr style={{ background: "#fff2cc" }}>
                  <Cell value="" /><Cell value="" /><Cell value="" />
                  <Cell value="Total" bold />
                  <Cell value="" />
                  <Cell value={preview.table2.inventory_qty} fmt="qty" bold />
                  <Cell value="" />
                  <Cell value={preview.table2.inventory_amt} fmt="money" bold />
                  <Cell value="" /><Cell value="" /><Cell value="" /><Cell value="" />
                  <Cell value={preview.table2.aging["1M"] || "-"} fmt={preview.table2.aging["1M"] ? "qty" : null} bold />
                  <Cell value={preview.table2.aging["2M"] || "-"} fmt={preview.table2.aging["2M"] ? "qty" : null} bold />
                  <Cell value={preview.table2.aging["3M_6M"] || "-"} fmt={preview.table2.aging["3M_6M"] ? "qty" : null} bold />
                  <Cell value="" /><Cell value="" /><Cell value="" /><Cell value="" /><Cell value="" />
                </tr>
              </tbody>
            </table>
          </div>

          {/* 표3 */}
          <h3 style={{ marginTop: 24 }}>{preview.input.mpn} 월말 재고 예상</h3>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6, maxWidth: 600 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#d9ead3" }}>
                  <th style={{ padding: 8, textAlign: "center", borderBottom: "2px solid #cbd5e1" }}>구분</th>
                  <th style={{ padding: 8, borderBottom: "2px solid #cbd5e1" }}>재고({preview.table3.start_month_label})</th>
                  {preview.table3.months.map((m, i) => (
                    <th key={i} style={{ padding: 8, borderBottom: "2px solid #cbd5e1" }}>{m.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td style={{ padding: 8, fontWeight: 700 }}>매입</td>
                  <td></td>
                  {preview.table3.months.map((m, i) => (
                    <Cell key={i} value={m.buy} fmt="qty" editable onChange={(v) => updatePreviewField(`table3.months.${i}.buy`, v)} />
                  ))}
                </tr>
                <tr>
                  <td style={{ padding: 8, fontWeight: 700 }}>매출</td>
                  <td></td>
                  {preview.table3.months.map((m, i) => (
                    <Cell key={i} value={m.sell} fmt="qty" editable onChange={(v) => updatePreviewField(`table3.months.${i}.sell`, v)} />
                  ))}
                </tr>
                <tr style={{ background: "#fff2cc" }}>
                  <td style={{ padding: 8, fontWeight: 700 }}>재고(월말)</td>
                  <Cell value={preview.table3.inventory_start} fmt="qty" bold />
                  {preview.table3.months.map((m, i) => (
                    <Cell key={i} value={m.end_inv} fmt="qty" bold />
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export default PoRequest;
