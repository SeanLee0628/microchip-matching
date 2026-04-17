import React, { useRef, useState } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function FileUpload({ onSuccess, onError, loading, setLoading }) {
  const fileRef = useRef();
  const [dragging, setDragging] = useState(false);

  const handleFile = async (file) => {
    if (!file) return;
    if (!file.name.endsWith(".xlsx") && !file.name.endsWith(".xls")) {
      onError("엑셀 파일(.xlsx)만 업로드 가능합니다.");
      return;
    }

    setLoading(true);
    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await axios.post(`${API_URL}/api/upload`, formData);
      if (res.data.error) {
        onError(res.data.error);
      } else {
        onSuccess(res.data);
      }
    } catch (err) {
      onError("업로드 실패: " + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    handleFile(file);
  };

  return (
    <div
      className={`upload-area ${dragging ? "dragging" : ""}`}
      onClick={() => fileRef.current.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,.xls"
        style={{ display: "none" }}
        onChange={(e) => handleFile(e.target.files[0])}
      />
      {loading ? (
        <div className="upload-loading">처리 중...</div>
      ) : (
        <>
          <div className="upload-icon">&#128196;</div>
          <div className="upload-text">마이크로칩 매칭 엑셀 파일을 드래그하거나 클릭하여 업로드</div>
          <div className="upload-hint">.xlsx 파일 지원</div>
        </>
      )}
    </div>
  );
}

export default FileUpload;
