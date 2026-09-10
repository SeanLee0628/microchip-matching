import React, { useRef, useState, useEffect } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "";

function FileUpload({ onSuccess, onError, loading, setLoading }) {
  const fileRef = useRef();
  const dragDepth = useRef(0); // 자식 요소 위 dragenter/leave 카운팅 (깜빡임 방지)
  const [dragging, setDragging] = useState(false);

  // 드롭 지점이 영역을 살짝 벗어나면 브라우저가 파일을 '열어버려' 페이지가 날아간다.
  // 창 전체에서 기본 동작을 막아, 실수로 빗나간 드롭이 페이지를 교체하지 못하게 한다.
  useEffect(() => {
    const prevent = (e) => {
      e.preventDefault();
    };
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  const handleFile = async (file) => {
    if (!file) {
      onError("파일을 인식하지 못했습니다. 다시 시도해 주세요.");
      return;
    }
    const name = (file.name || "").toLowerCase();
    if (!name.endsWith(".xlsx") && !name.endsWith(".xls")) {
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

  const onDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragDepth.current += 1;
    setDragging(true);
  };

  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  };

  const onDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };

  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragDepth.current = 0;
    setDragging(false);

    // dataTransfer.files 가 비어있으면(브라우저 탭에서 끌어온 경우 등) items 로 폴백 시도
    let file = e.dataTransfer?.files?.[0];
    if (!file && e.dataTransfer?.items?.length) {
      const item = Array.from(e.dataTransfer.items).find((it) => it.kind === "file");
      if (item) file = item.getAsFile();
    }
    if (!file) {
      onError(
        "파일을 받지 못했습니다. SharePoint·브라우저 탭에서 직접 끌면 파일이 전달되지 않습니다. " +
          "탐색기에서 파일을 끌거나 클릭해서 선택해 주세요."
      );
      return;
    }
    handleFile(file);
  };

  return (
    <div
      className={`upload-area ${dragging ? "dragging" : ""}`}
      onClick={() => fileRef.current.click()}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
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
          <div className="upload-text">
            {dragging
              ? "여기에 놓으세요"
              : "백록 + 출고내역 원본 파일을 드래그하거나 클릭하여 업로드"}
          </div>
          <div className="upload-hint">백록YYMMDD · 출고내역 · FAB2 시트 포함 .xlsx</div>
        </>
      )}
    </div>
  );
}

export default FileUpload;
