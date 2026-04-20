import React from "react";

function Sidebar({ activeMenu, setActiveMenu }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        <h2>Uniquant</h2>
        <span>Sales Intelligence Remix</span>
      </div>

      <div className="sidebar-section">
        <button
          className={`sidebar-menu-item ${activeMenu === "micron" ? "active" : ""}`}
          onClick={() => setActiveMenu("micron")}
        >
          <span className="icon">&#9679;</span>
          <b>[1실]</b>&nbsp;마이크론
        </button>

        <button
          className={`sidebar-menu-item ${activeMenu === "ublox" ? "active" : ""}`}
          onClick={() => setActiveMenu("ublox")}
        >
          <span className="icon">&#9679;</span>
          <b>[2실]</b>&nbsp;UBLOX 백로그
        </button>

        <button
          className={`sidebar-menu-item ${activeMenu === "sales" ? "active" : ""}`}
          onClick={() => setActiveMenu("sales")}
        >
          <span className="icon">&#9679;</span>
          <b>[2실]</b>&nbsp;2실 영업실적
        </button>

        <button
          className={`sidebar-menu-item ${activeMenu === "invoice" ? "active" : ""}`}
          onClick={() => setActiveMenu("invoice")}
        >
          <span className="icon">&#9679;</span>
          <b>[2실]</b>&nbsp;거래명세서
        </button>

        <button
          className={`sidebar-menu-item ${activeMenu === "matching" ? "active" : ""}`}
          onClick={() => setActiveMenu("matching")}
        >
          <span className="icon">&#9679;</span>
          <b>[5실]</b>&nbsp;Microchip 매칭
        </button>
      </div>
    </nav>
  );
}

export default Sidebar;
