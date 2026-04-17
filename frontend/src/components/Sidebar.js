import React from "react";

function Sidebar({ activeMenu, setActiveMenu }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        <h2>UniQuant</h2>
        <span>Automation System</span>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-title">MICROCHIP MATCHING</div>
        <button
          className={`sidebar-menu-item ${activeMenu === "matching" ? "active" : ""}`}
          onClick={() => setActiveMenu("matching")}
        >
          <span className="icon">&#9679;</span>
          Microchip 매칭
        </button>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-title">UBLOX 백로그</div>
        <button
          className={`sidebar-menu-item ${activeMenu === "ublox" ? "active" : ""}`}
          onClick={() => setActiveMenu("ublox")}
        >
          <span className="icon">&#9679;</span>
          UBLOX 백로그
        </button>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-title">2실 영업실적</div>
        <button
          className={`sidebar-menu-item ${activeMenu === "sales" ? "active" : ""}`}
          onClick={() => setActiveMenu("sales")}
        >
          <span className="icon">&#9679;</span>
          2실 영업실적
        </button>
      </div>
    </nav>
  );
}

export default Sidebar;
