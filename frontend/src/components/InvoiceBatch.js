import React, { useState, useRef } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function InvoiceBatch() {
  const [invoices, setInvoices] = useState([]);
  // selectedMap: { [invIdx]: Set<itemIdx> } — 카드별로 체크된 품목 인덱스
  const [selectedMap, setSelectedMap] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef();

  const handleFile = async (file) => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setInvoices([]);
    setSelectedMap({});
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await axios.post(`${API_URL}/api/invoice-batch/preview`, fd);
      if (res.data.error) {
        setError(res.data.error);
      } else {
        const invs = res.data.invoices || [];
        setInvoices(invs);
        // 기본값: 전체 체크
        const init = {};
        invs.forEach((inv, idx) => {
          init[idx] = new Set(inv.items.map((_, i) => i));
        });
        setSelectedMap(init);
      }
    } catch (e) {
      setError("업로드 실패: " + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const toggleItem = (invIdx, itemIdx) => {
    setSelectedMap((prev) => {
      const next = { ...prev };
      const s = new Set(next[invIdx] || []);
      if (s.has(itemIdx)) s.delete(itemIdx);
      else s.add(itemIdx);
      next[invIdx] = s;
      return next;
    });
  };

  const toggleAll = (invIdx, inv) => {
    setSelectedMap((prev) => {
      const next = { ...prev };
      const cur = prev[invIdx] || new Set();
      if (cur.size === inv.items.length) {
        next[invIdx] = new Set(); // 전체 해제
      } else {
        next[invIdx] = new Set(inv.items.map((_, i) => i)); // 전체 선택
      }
      return next;
    });
  };

  const getSelectedItems = (invIdx, inv) => {
    const s = selectedMap[invIdx] || new Set();
    return inv.items.filter((_, i) => s.has(i));
  };

  const computeTotals = (items) => {
    let usd = 0, krw = 0;
    items.forEach((it) => {
      usd += it.amount_usd || 0;
      krw += it.amount_krw || 0;
    });
    return { total_usd: Math.round(usd * 100) / 100, total_krw: Math.round(krw) };
  };

  const handleDownload = async (inv, invIdx, format /* "xlsx" | "pdf" */) => {
    const items = getSelectedItems(invIdx, inv);
    if (items.length === 0) {
      setError("최소 1개 품목을 선택해야 합니다.");
      return;
    }
    const earliest = items.reduce((min, it) => (it.date && (!min || it.date < min) ? it.date : min), null);
    const endpoint = format === "pdf" ? "/api/invoice/generate-pdf" : "/api/invoice/generate";
    const mime = format === "pdf" ? "application/pdf" : "application/vnd.openxmlformats";
    try {
      const res = await axios.post(`${API_URL}${endpoint}`, {
        customer: inv.customer,
        date: earliest || new Date().toISOString().slice(0, 10),
        rate: inv.rate,
        items,
      }, { responseType: "blob" });
      // JSON error 응답 처리
      if (res.data.type && res.data.type.includes("json")) {
        const text = await res.data.text();
        setError(JSON.parse(text).error || "변환 실패");
        return;
      }
      const url = window.URL.createObjectURL(new Blob([res.data], { type: mime }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `거래명세서_${inv.customer.replace(/[\\/:*?"<>|]/g, "_")}_${earliest || "today"}.${format}`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError("다운로드 실패: " + e.message);
    }
  };

  const fmt = (v) => (v == null ? "-" : Number(v).toLocaleString());
  const fmtUsd = (v) => (v == null ? "-" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  const fmtUsdPrice = (v) => (v == null ? "-" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 5 })}`);
  const fmtKrw = (v) => (v == null ? "-" : `₩${Math.round(Number(v)).toLocaleString()}`);
  const fmtKrwPrice = (v) => (v == null ? "-" : `₩${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 5 })}`);

  return (
    <div>
      <div className="page-header">
        <h1>거래명세서 일괄 생성</h1>
        <p className="subtitle">
          출고기안 엑셀 업로드 → 고객별 거래명세서 자동 분할 · <b>출고일자별 환율 자동 조회</b>
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ marginBottom: 20 }}>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xlsm,.xls"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files[0])}
        />
        <button
          onClick={() => fileRef.current.click()}
          disabled={loading}
          style={{ padding: "12px 24px", background: "#3b82f6", color: "white", border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
        >
          {loading ? "처리 중..." : "📥 출고기안 엑셀 업로드"}
        </button>
        <span style={{ marginLeft: 12, fontSize: 13, color: "#64748b" }}>
          .xlsx / .xlsm 지원 · "출고기안" 시트를 자동으로 찾아 파싱
        </span>
      </div>

      {invoices.length > 0 && (
        <div style={{ marginBottom: 14, fontSize: 14, color: "#334155" }}>
          <b>{invoices.length}개 고객사</b> 거래명세서 생성됨 · 체크박스로 포함할 품목 선택
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {invoices.map((inv, idx) => {
          const selected = selectedMap[idx] || new Set();
          const selectedItems = inv.items.filter((_, i) => selected.has(i));
          const totals = computeTotals(selectedItems);
          const allChecked = selected.size === inv.items.length;
          return (
            <div key={idx} style={{ border: "1px solid #e2e8f0", borderRadius: 8, background: "white", padding: 20 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, borderBottom: "2px solid #f1f5f9", paddingBottom: 10 }}>
                <div>
                  <h3 style={{ margin: 0, fontSize: 18 }}>{inv.customer}</h3>
                  <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
                    <b style={{ color: "#3b82f6" }}>{selected.size} / {inv.items.length}</b> 품목 선택됨 · 최초 출고 {inv.earliest_date} · 담당 {inv.담당자 || "-"}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    onClick={() => toggleAll(idx, inv)}
                    style={{ padding: "8px 14px", background: "white", color: "#475569", border: "1px solid #cbd5e1", borderRadius: 6, cursor: "pointer", fontSize: 13 }}
                  >
                    {allChecked ? "전체 해제" : "전체 선택"}
                  </button>
                  <button
                    onClick={() => handleDownload(inv, idx, "xlsx")}
                    disabled={selected.size === 0}
                    style={{ padding: "8px 18px", background: selected.size === 0 ? "#94a3b8" : "#10b981", color: "white", border: 0, borderRadius: 6, cursor: selected.size === 0 ? "not-allowed" : "pointer", fontWeight: 600 }}
                  >
                    📥 엑셀 다운로드
                  </button>
                  <button
                    onClick={() => handleDownload(inv, idx, "pdf")}
                    disabled={selected.size === 0}
                    style={{ padding: "8px 18px", background: selected.size === 0 ? "#94a3b8" : "#dc2626", color: "white", border: 0, borderRadius: 6, cursor: selected.size === 0 ? "not-allowed" : "pointer", fontWeight: 600 }}
                  >
                    📄 PDF 다운로드
                  </button>
                </div>
              </div>

              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ background: "#f1f5f9" }}>
                    <th style={{ padding: 8, textAlign: "center", borderBottom: "1px solid #cbd5e1", width: 36 }}>
                      <input
                        type="checkbox"
                        checked={allChecked}
                        onChange={() => toggleAll(idx, inv)}
                      />
                    </th>
                    <th style={{ padding: 8, textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>출고일자</th>
                    <th style={{ padding: 8, textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>Part #</th>
                    <th style={{ padding: 8, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>QTY</th>
                    <th style={{ padding: 8, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>U/Price ($)</th>
                    <th style={{ padding: 8, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>Amount ($)</th>
                    <th style={{ padding: 8, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>Rate</th>
                    <th style={{ padding: 8, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>U/Price (₩)</th>
                    <th style={{ padding: 8, textAlign: "right", borderBottom: "1px solid #cbd5e1" }}>Amount (₩)</th>
                  </tr>
                </thead>
                <tbody>
                  {inv.items.map((it, i) => {
                    const checked = selected.has(i);
                    return (
                      <tr key={i} style={{ borderBottom: "1px solid #f1f5f9", opacity: checked ? 1 : 0.4 }}>
                        <td style={{ padding: 8, textAlign: "center" }}>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleItem(idx, i)}
                          />
                        </td>
                        <td style={{ padding: 8 }}>{it.date}</td>
                        <td style={{ padding: 8 }}>{it.part}</td>
                        <td style={{ padding: 8, textAlign: "right" }}>{fmt(it.qty)}</td>
                        <td style={{ padding: 8, textAlign: "right" }}>{fmtUsdPrice(it.price)}</td>
                        <td style={{ padding: 8, textAlign: "right" }}>{fmtUsd(it.amount_usd)}</td>
                        <td style={{ padding: 8, textAlign: "right", color: "#64748b" }}>{it.rate ? it.rate.toLocaleString() : "-"}</td>
                        <td style={{ padding: 8, textAlign: "right" }}>{fmtKrwPrice(it.price_krw)}</td>
                        <td style={{ padding: 8, textAlign: "right" }}>{fmtKrw(it.amount_krw)}</td>
                      </tr>
                    );
                  })}
                  <tr style={{ background: "#fef9c3", fontWeight: 700 }}>
                    <td colSpan="5" style={{ padding: 8 }}>소계 (선택된 {selected.size}건)</td>
                    <td style={{ padding: 8, textAlign: "right" }}>{fmtUsd(totals.total_usd)}</td>
                    <td colSpan="2"></td>
                    <td style={{ padding: 8, textAlign: "right" }}>{fmtKrw(totals.total_krw)}</td>
                  </tr>
                  <tr style={{ background: "#fef9c3", fontWeight: 700 }}>
                    <td colSpan="5" style={{ padding: 8 }}>부가세 (10%)</td>
                    <td style={{ padding: 8, textAlign: "right" }}>{fmtUsd(totals.total_usd * 0.1)}</td>
                    <td colSpan="2"></td>
                    <td style={{ padding: 8, textAlign: "right" }}>{fmtKrw(totals.total_krw * 0.1)}</td>
                  </tr>
                  <tr style={{ background: "#fde68a", fontWeight: 800 }}>
                    <td colSpan="5" style={{ padding: 8 }}>합계</td>
                    <td style={{ padding: 8, textAlign: "right" }}>{fmtUsd(totals.total_usd * 1.1)}</td>
                    <td colSpan="2"></td>
                    <td style={{ padding: 8, textAlign: "right" }}>{fmtKrw(totals.total_krw * 1.1)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default InvoiceBatch;
