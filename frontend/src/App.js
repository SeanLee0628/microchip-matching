import React, { useState, useEffect } from "react";
import axios from "axios";
import Sidebar from "./components/Sidebar";
import FileUpload from "./components/FileUpload";
import DataTable from "./components/DataTable";
import UbloxBacklog from "./components/UbloxBacklog";
import SalesPerformance from "./components/SalesPerformance";
import Invoice from "./components/Invoice";
import "./App.css";

const API_URL = process.env.REACT_APP_API_URL || "";

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeMenu, setActiveMenu] = useState("matching");
  const [dbInfo, setDbInfo] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);

  // 페이지 진입 시 DB에서 데이터 자동 로드
  useEffect(() => {
    axios
      .get(`${API_URL}/api/data`)
      .then((res) => {
        if (res.data.total_rows > 0) {
          setData(res.data);
          setDbInfo({
            uploaded_at: res.data.uploaded_at,
            batch_id: res.data.batch_id,
          });
        }
      })
      .catch(() => {});
  }, []);

  const handleUploadSuccess = (result) => {
    setData(result);
    setError(null);
    setDbInfo({
      uploaded_at: new Date().toLocaleString("ko-KR"),
      batch_id: result.batch_id,
    });
    if (result.inserted !== undefined) {
      setUploadResult({ inserted: result.inserted, updated: result.updated });
      setTimeout(() => setUploadResult(null), 5000);
    }
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
                {dbInfo?.uploaded_at && (
                  <span className="db-status">
                    {" "}| DB 저장: {dbInfo.uploaded_at}
                  </span>
                )}
              </p>
            </div>

            <FileUpload
              onSuccess={handleUploadSuccess}
              onError={handleError}
              loading={loading}
              setLoading={setLoading}
            />

            {uploadResult && (
              <div className="success-banner">
                신규 {uploadResult.inserted}건 추가 / 기존 {uploadResult.updated}건 업데이트
              </div>
            )}

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
      </main>
    </div>
  );
}

export default App;
