import React from "react";

// 부품 라벨 검수(label-inspector) — 백엔드 /label 페이지를 iframe 으로 표시.
function Materials() {
  return (
    <iframe
      src="/label"
      title="부품 라벨 검수"
      style={{
        width: "100%",
        height: "calc(100vh - 48px)",
        border: 0,
        display: "block",
      }}
    />
  );
}

export default Materials;
