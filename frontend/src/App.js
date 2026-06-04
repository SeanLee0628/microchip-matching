import React, { useState } from "react";
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
import Materials from "./components/Materials";
import MicrochipAiMatch from "./components/MicrochipAiMatch";
import MicrochipBuildSheet from "./components/MicrochipBuildSheet";
import InventoryAnalysis from "./components/InventoryAnalysis";
import SubulFilter from "./components/SubulFilter";
import BacklogPrdDiff from "./components/BacklogPrdDiff";
import FcstSalesDiff from "./components/FcstSalesDiff";
import CrdBoard from "./components/CrdBoard";
import "./App.css";

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeMenu, setActiveMenu] = useState(null);

  const handleUploadSuccess = (result) => {
    setData(result);
    setError(null);
  };

  const handleError = (msg) => {
    setError(msg);
  };

  return (
    <div className="app">
      <Sidebar activeMenu={activeMenu} setActiveMenu={setActiveMenu} />
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
        {activeMenu === "materials" && <Materials />}
        {activeMenu === "matching_ai" && <MicrochipAiMatch />}
        {activeMenu === "matching_build" && <MicrochipBuildSheet />}
        {activeMenu === "inventory_analysis" && <InventoryAnalysis />}
        {activeMenu === "subul_filter" && <SubulFilter />}
        {activeMenu === "backlog_prd_diff" && <BacklogPrdDiff />}
        {activeMenu === "fcst_sales_diff" && <FcstSalesDiff />}
      </main>
    </div>
  );
}

export default App;
