import React, { useState, useMemo } from "react";
import * as XLSX from "xlsx";
import { saveAs } from "file-saver";

const NUMBER_COLS = new Set([
  "LT", "2023년", "2024년", "2025년", "2026년", "23~25추이", "25-26(w/BL)",
  "BLOG TTL", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월",
]);

// 0은 "-"로 표시 (원본 엑셀과 동일), null/빈값은 빈칸
function formatNumber(val) {
  if (val === null || val === undefined || val === "") return "";
  const num = Number(val);
  if (isNaN(num)) return val;
  if (num === 0) return "-";
  return num.toLocaleString();
}

function DataTable({ columns, rows, sheetName, totalRows }) {
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return rows;
    const q = search.toLowerCase();
    return rows.filter((row) =>
      columns.some((col) => {
        const v = row[col];
        return v !== null && v !== undefined && String(v).toLowerCase().includes(q);
      })
    );
  }, [rows, search, columns]);

  const handleExport = () => {
    const exportData = filtered.map((row) => {
      const obj = {};
      columns.forEach((col) => {
        obj[col] = row[col] ?? "";
      });
      return obj;
    });

    const ws = XLSX.utils.json_to_sheet(exportData, { header: columns });

    // 컬럼 너비 자동 설정
    const colWidths = columns.map((col) => {
      const maxLen = Math.max(
        col.length * 2,
        ...filtered.slice(0, 100).map((r) => String(r[col] ?? "").length)
      );
      return { wch: Math.min(maxLen + 2, 30) };
    });
    ws["!cols"] = colWidths;

    // 필터 옵션 설정
    const lastCol = XLSX.utils.encode_col(columns.length - 1);
    const lastRow = exportData.length + 1;
    ws["!autofilter"] = { ref: `A1:${lastCol}${lastRow}` };

    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "마이크로칩(매칭)");
    const buf = XLSX.write(wb, { bookType: "xlsx", type: "array" });
    saveAs(new Blob([buf]), `마이크로칩_매칭_${new Date().toISOString().slice(0, 10)}.xlsx`);
  };

  return (
    <div>
      <div className="table-header">
        <div className="table-info">
          시트: <strong>{sheetName}</strong> | 총{" "}
          <strong>{filtered.length}</strong>건
          {search && ` (전체 ${totalRows}건 중)`}
        </div>
        <button className="export-btn" onClick={handleExport}>
          <span>&#128229;</span> 엑셀 내보내기
        </button>
      </div>

      <div className="search-bar">
        <input
          className="search-input"
          type="text"
          placeholder="검색 (고객명, PART#, 담당자 등)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="table-container">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((row, i) => (
              <tr key={i}>
                <td style={{ color: "#999", textAlign: "center" }}>{i + 1}</td>
                {columns.map((col) => {
                  const val = row[col];
                  const isNum = NUMBER_COLS.has(col);
                  const numVal = Number(val);
                  let cellClass = "";
                  if (isNum) {
                    cellClass = "cell-number";
                    if (!isNaN(numVal) && numVal > 0 && (col === "23~25추이" || col === "25-26(w/BL)")) {
                      cellClass += " cell-positive";
                    } else if (!isNaN(numVal) && numVal < 0 && (col === "23~25추이" || col === "25-26(w/BL)")) {
                      cellClass += " cell-negative";
                    }
                  }
                  return (
                    <td key={col} className={cellClass}>
                      {isNum ? formatNumber(val) : (val ?? "")}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DataTable;
