import React, { useState, useEffect } from "react";
import Sidebar from "./components/Sidebar";
import FileUpload from "./components/FileUpload";
import DataTable from "./components/DataTable";
import UbloxBacklog from "./components/UbloxBacklog";
import SalesPerformance from "./components/SalesPerformance";
import Invoice from "./components/Invoice";
import Micron from "./components/Micron";
import AuoBacklog from "./components/AuoBacklog";
import PoRequest from "./components/PoRequest";
import InvoiceBatch from "./components/InvoiceBatch";
import MicronInvoice from "./components/MicronInvoice";
import SalesReportConvert from "./components/SalesReportConvert";
import PoReportConvert from "./components/PoReportConvert";
import SalesSummary from "./components/SalesSummary";
import ShippingInvoice from "./components/ShippingInvoice";
import PosReportFill from "./components/PosReportFill";
import PosAutomation from "./components/PosAutomation";
import Materials from "./components/Materials";
import LabelMaker from "./components/LabelMaker";
import MicrochipAiMatch from "./components/MicrochipAiMatch";
import MicrochipBuildSheet from "./components/MicrochipBuildSheet";
import InventoryAnalysis from "./components/InventoryAnalysis";
import SubulFilter from "./components/SubulFilter";
import BacklogPrdDiff from "./components/BacklogPrdDiff";
import FcstSalesDiff from "./components/FcstSalesDiff";
import CrdBoard from "./components/CrdBoard";
import Match5 from "./components/Match5";
import "./App.css";

// 섹션 키 ↔ URL 경로 라우팅. 각 섹션은 /<key> 로 직접 링크 가능.
const VALID_MENUS = new Set([
  "matching", "ublox", "sales", "invoice", "micron", "crd_board", "auo",
  "po_request", "invoice_batch", "micron_invoice", "sales_report", "po_report",
  "sales_summary", "shipping_invoice", "pos_report_fill", "materials", "label_maker",
  "matching_ai", "matching_build", "inventory_analysis", "subul_filter",
  "backlog_prd_diff", "fcst_sales_diff", "match5", "pos_auto",
]);

function menuFromPath() {
  const p = (window.location.pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "").trim();
  return VALID_MENUS.has(p) ? p : null;
}

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeMenu, setActiveMenu] = useState(menuFromPath);

  // 섹션 ↔ URL 경로(/key) 동기화 — 링크 공유 · 새로고침 · 뒤로/앞으로 지원
  useEffect(() => {
    const onPop = () => setActiveMenu(menuFromPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = (key) => {
    const target = key ? `/${key}` : "/";
    if (window.location.pathname !== target) window.history.pushState({}, "", target);
    setActiveMenu(key || null);
  };

  const handleUploadSuccess = (result) => {
    setData(result);
    setError(null);
  };

  const handleError = (msg) => {
    setError(msg);
  };

  return (
    <div className="app">
      <Sidebar activeMenu={activeMenu} setActiveMenu={navigate} />
      <main className="main-content">
        {!activeMenu && (
          <div style={{ padding: 60, textAlign: "center", color: "#64748b" }}>
            <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 8 }}>
              <span style={{ color: "#000000" }}>Welcome,</span>{" "}
              <span style={{ color: "#dc2626" }}>CS</span>
              <span style={{ color: "#000000" }}> @ </span>
              <span style={{ color: "#2563eb" }}>Unitrontech</span>
            </h1>
            <p style={{ fontSize: 14 }}>좌측에서 소속을 선택하여 분석을 시작하세요.</p>
          </div>
        )}
        {activeMenu === "matching" && (
          <>
            <div className="page-header">
              <h1>Unitron AI</h1>
              <p className="subtitle">
                마이크로칩 End Customer / Purchasing Customer 매칭 현황
              </p>
            </div>

            <FileUpload
              onSuccess={handleUploadSuccess}
              onError={handleError}
              loading={loading}
              setLoading={setLoading}
            />

            {error && <div className="error-banner">{error}</div>}

            {data && (
              <DataTable
                columns={data.columns}
                rows={data.data}
                sheetName={data.sheet_name}
                totalRows={data.total_rows}
              />
            )}
          </>
        )}

        {activeMenu === "ublox" && <UbloxBacklog />}
        {activeMenu === "sales" && <SalesPerformance />}
        {activeMenu === "invoice" && <Invoice />}
        {activeMenu === "micron" && <Micron />}
        {activeMenu === "crd_board" && <CrdBoard />}
        {activeMenu === "auo" && <AuoBacklog />}
        {activeMenu === "po_request" && <PoRequest />}
        {activeMenu === "invoice_batch" && <InvoiceBatch />}
        {activeMenu === "micron_invoice" && <MicronInvoice />}
        {activeMenu === "sales_report" && <SalesReportConvert />}
        {activeMenu === "po_report" && <PoReportConvert />}
        {activeMenu === "sales_summary" && <SalesSummary />}
        {activeMenu === "shipping_invoice" && <ShippingInvoice />}
        {activeMenu === "pos_report_fill" && <PosReportFill />}
        {activeMenu === "pos_auto" && <PosAutomation />}
        {activeMenu === "materials" && <Materials />}
        {activeMenu === "label_maker" && <LabelMaker />}
        {activeMenu === "matching_ai" && <MicrochipAiMatch />}
        {activeMenu === "matching_build" && <MicrochipBuildSheet />}
        {activeMenu === "inventory_analysis" && <InventoryAnalysis />}
        {activeMenu === "subul_filter" && <SubulFilter />}
        {activeMenu === "backlog_prd_diff" && <BacklogPrdDiff />}
        {activeMenu === "fcst_sales_diff" && <FcstSalesDiff />}
        {activeMenu === "match5" && <Match5 />}
      </main>
    </div>
  );
}

export default App;
