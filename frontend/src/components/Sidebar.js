import React, { useState } from "react";

const GROUPS = [
  { team: "1실", items: [
    { key: "micron", label: "재고조회" },
    { key: "crd_board", label: "CRD 현황판" },
    { key: "micron_invoice", label: "거래명세서" },
    { key: "sales_report", label: "영업실적 변환" },
    { key: "po_report", label: "발주요청서 변환" },
  ]},
  { team: "2실", items: [
    { key: "ublox", label: "UBLOX 백로그" },
    { key: "sales", label: "2실 영업실적" },
    { key: "invoice", label: "거래명세서" },
  ]},
  { team: "3실", items: [
    { key: "auo", label: "AUO 백로그" },
    { key: "po_request", label: "발주요청서" },
  ]},
  { team: "4실", items: [
    { key: "sales_summary", label: "주간 영업실적 취합" },
    { key: "shipping_invoice", label: "출고요청 → 거래명세서" },
    { key: "inventory_analysis", label: "재고 분석" },
    { key: "subul_filter", label: "수불부 품목 필터" },
  ]},
  { team: "5실", items: [
    { key: "matching", label: "Microchip 매칭" },
    { key: "invoice_batch", label: "거래명세서 일괄" },
    { key: "pos_report_fill", label: "출고내역 자동완성" },
    { key: "backlog_prd_diff", label: "백록 PRD 변동 비교" },
    { key: "fcst_sales_diff", label: "영업FCST/매출 비교" },
  ]},
  { team: "자재", items: [
    { key: "materials", label: "출고 자동등록 (AI Agent)" },
  ]},
];

const LOCKED_TEAMS = {};

function Sidebar({ activeMenu, setActiveMenu }) {
  // 현재 활성 메뉴가 속한 실 자동으로 펼침. activeMenu가 없으면 아무것도 펼치지 않음.
  const initialOpen = (() => {
    for (const g of GROUPS) {
      if (g.items.some(it => it.key === activeMenu)) return g.team;
    }
    return null;
  })();
  const [openTeam, setOpenTeam] = useState(initialOpen);

  const tryToggle = (team) => {
    const isOpen = openTeam === team;
    if (isOpen) { setOpenTeam(null); return; }
    if (LOCKED_TEAMS[team]) {
      const pw = window.prompt(`${team} 비밀번호 입력`);
      if (pw == null) return;
      if (pw !== LOCKED_TEAMS[team]) { window.alert("비밀번호가 틀렸습니다."); return; }
    }
    setOpenTeam(team);
  };

  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        <h2 style={{ fontSize: 20, fontWeight: 800, letterSpacing: "1.5px", margin: 0 }}>
          UNITRON<span style={{ color: "#c43a3a" }}>TECH</span>
        </h2>
        <span style={{ fontSize: 10.5, color: "#999", display: "block", marginTop: 6, letterSpacing: "1px", textTransform: "uppercase", fontWeight: 600 }}>
          Sales Intelligence
        </span>
      </div>

      <div className="sidebar-section">
        {GROUPS.map((g) => {
          const isOpen = openTeam === g.team;
          return (
            <div key={g.team} style={{ marginBottom: 4 }}>
              <button
                className="sidebar-menu-item"
                onClick={() => tryToggle(g.team)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontWeight: 700,
                  background: isOpen ? "#f1f5f9" : "transparent",
                }}
              >
                <span><b>{g.team}</b></span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.25"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{
                    color: "#94a3b8",
                    transition: "transform 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
                    transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
                  }}
                >
                  <polyline points="9 6 15 12 9 18" />
                </svg>
              </button>
              {isOpen && (
                <div style={{ paddingLeft: 12, marginTop: 2 }}>
                  {g.items.map((it) => (
                    <button
                      key={it.key}
                      className={`sidebar-menu-item ${activeMenu === it.key ? "active" : ""}`}
                      onClick={() => setActiveMenu(it.key)}
                      style={{ fontSize: 13, padding: "6px 12px" }}
                    >
                      <span className="icon" style={{ fontSize: 8 }}>&#9679;</span>
                      &nbsp;{it.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-footer-title">문의</div>
        <div className="sidebar-footer-text">경영기획팀 이희서 매니저</div>
        <div className="sidebar-footer-email">seanlee@unitrontech.com</div>
      </div>
    </nav>
  );
}

export default Sidebar;
