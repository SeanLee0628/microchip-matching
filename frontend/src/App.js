import React, { useState, useEffect } from "react";
import axios from "axios";
import Sidebar from "./components/Sidebar";
import FileUpload from "./components/FileUpload";
import DataTable from "./components/DataTable";
import "./App.css";

const API_URL = process.env.REACT_APP_API_URL || "";

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeMenu, setActiveMenu] = useState("matching");
  const [dbInfo, setDbInfo] = useState(null);

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
              <h1>마이크로칩 (매칭)</h1>
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
      </main>
    </div>
  );
}

export default App;
