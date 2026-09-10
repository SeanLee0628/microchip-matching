import React from "react";

// 자재 라벨 생성(label-maker) — 백엔드 /labelmaker 페이지를 iframe 으로 표시.
// Mobis 출고내역 엑셀 → 최근 LOT 선택 → QR 라벨 인쇄.
function LabelMaker() {
  return (
    <iframe
      src="/labelmaker"
      title="라벨 생성"
      style={{
        width: "100%",
        height: "calc(100vh - 48px)",
        border: 0,
        display: "block",
      }}
    />
  );
}

export default LabelMaker;
