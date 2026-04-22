import React, { useState } from "react";
import Sidebar from "./components/Sidebar";
import FileUpload from "./components/FileUpload";
import DataTable from "./components/DataTable";
import UbloxBacklog from "./components/UbloxBacklog";
import SalesPerformance from "./components/SalesPerformance";
import Invoice from "./components/Invoice";
import Micron from "./components/Micron";
import "./App.css";

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeMenu, setActiveMenu] = useState("micron");

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
      </main>
    </div>
  );
}

export default App;
