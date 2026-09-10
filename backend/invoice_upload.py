"""거래명세서 업로드 데이터(xlsx) 파서.

목적: 업로드한 파일의 내용을 **그대로** 거래명세서로 내보내기 위한 파싱.
환산·재계산을 하지 않는다 — 파일에 적힌 값과 셀 서식(소수점 자릿수)을 그대로 들고 나간다.
업체마다 소수점 표기가 다르고(2자리/4자리), 외화만 또는 원화만 쓰는 건도 있어서
서버가 임의로 반올림하면 원본과 어긋난다.

문서번호 단위로 한 장의 거래명세서가 된다(같은 문서번호 = 품목 여러 줄).
"""
import io
import re
from datetime import date as _date, datetime as _datetime

import openpyxl


def _col_key(h):
    """헤더 문자열 → 내부 키. 원화 기호가 ₩ / ￦ / \\ 무엇으로 들어와도 같게 본다."""
    n = re.sub(r"[\s_\\₩￦]", "", str(h or "")).upper()
    if not n:
        return None
    if n.startswith("DATE") or n in ("출고일자", "출고일"):
        return "date"
    if n.startswith("SALES") or n in ("담당자", "영업담당"):
        return "sales"
    if n.startswith("CUSTOMER") or "고객" in n or n == "업체명":
        return "customer"
    if n in ("MPN", "PART", "PART#", "PARTNO", "품번", "품명"):
        return "part"
    if n.replace("'", "") in ("QTY", "QUANTITY", "수량"):
        return "qty"
    if "TOTALAMT" in n or "합계금액" in n:
        return "total_amt_krw"
    if n.startswith("TAX") or "부가세" in n:
        return "tax_krw"
    if "U/PRICE" in n or "UPRICE" in n or "단가" in n:
        return "price_usd" if "$" in n else "price_krw"
    if "AMOUNT" in n or n == "금액":
        return "amount_usd" if "$" in n else "amount_krw"
    if n.startswith("RATE") or "환율" in n:
        return "rate"
    if "문서번호" in n:
        return "doc_no"
    if "거래명세서일자" in n or "발행일" in n or "명세서일자" in n:
        return "issue_date"
    return None


def _to_float(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("₩", "").replace("$", "").strip())
    except ValueError:
        return None


def _to_date_str(v):
    if v is None:
        return ""
    if isinstance(v, (_datetime, _date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s[:10] if s and s.lower() != "nan" else ""


def decimals_from_fmt(fmt):
    """엑셀 서식 코드에서 소수점 자릿수를 뽑는다. PDF 는 서식 코드를 못 쓰니 자릿수만 가져간다.

    '"W"#,##0.0000_);[Red]...' -> 4 / '\\$#,##0.00' -> 2 / '#,##0' -> 0
    """
    if not fmt:
        return None
    first = str(fmt).split(";")[0]
    # 리터럴("...", \x, [Red] 등)을 걷어내고 숫자 패턴만 본다
    first = re.sub(r'"[^"]*"', "", first)
    first = re.sub(r"\[[^\]]*\]", "", first)
    first = re.sub(r"\\.", "", first)
    m = re.search(r"\.([0#?]+)", first)
    if m:
        return len(m.group(1))
    return 0 if re.search(r"[0#?]", first) else None


def _header_row(ws):
    """헤더 행 번호와 {열번호: 키}. 상단에 제목 줄이 있어도 견디게 10행까지 훑는다."""
    for r in range(1, min(ws.max_row, 10) + 1):
        mapping = {}
        for c in range(1, ws.max_column + 1):
            k = _col_key(ws.cell(row=r, column=c).value)
            if k and k not in mapping.values():
                mapping[c] = k
        keys = set(mapping.values())
        if "part" in keys and "qty" in keys and len(keys) >= 4:
            return r, mapping
    return None, {}


def parse_invoice_upload(contents: bytes) -> dict:
    """업로드 xlsx → 문서번호별 거래명세서 목록.

    반환: {"invoices": [...], "invoice_count": n} 또는 {"error": "..."}
    """
    try:
        wb_val = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
        wb_raw = openpyxl.load_workbook(io.BytesIO(contents), data_only=False)
    except Exception as e:
        return {"error": f"엑셀을 열 수 없습니다: {e}"}

    ws_val = wb_val[wb_val.sheetnames[0]]
    ws_raw = wb_raw[wb_raw.sheetnames[0]]

    hrow, cols = _header_row(ws_val)
    if not hrow:
        return {"error": "헤더 행(MPN/Q'ty 등)을 찾을 수 없습니다."}

    groups = {}
    order = []
    for r in range(hrow + 1, ws_val.max_row + 1):
        row, fmts, formulas = {}, {}, set()
        for c, key in cols.items():
            cell = ws_val.cell(row=r, column=c)
            row[key] = cell.value
            fmts[key] = cell.number_format
            raw = ws_raw.cell(row=r, column=c).value
            if isinstance(raw, str) and raw.startswith("="):
                formulas.add(key)

        part = str(row.get("part") or "").strip()
        qty = _to_float(row.get("qty"))
        if not part or not qty:
            continue

        price_usd = _to_float(row.get("price_usd"))
        rate = _to_float(row.get("rate"))
        price_krw = _to_float(row.get("price_krw"))
        amount_usd = _to_float(row.get("amount_usd"))
        amount_krw = _to_float(row.get("amount_krw"))
        tax_krw = _to_float(row.get("tax_krw"))
        total_amt_krw = _to_float(row.get("total_amt_krw"))

        # 수식만 있고 캐시값이 없는 파일 대비 — 원본 수식과 같은 계산으로 되살린다
        if amount_usd is None and price_usd is not None and "amount_usd" in formulas:
            amount_usd = qty * price_usd
        if price_krw is None and price_usd is not None and rate and "price_krw" in formulas:
            price_krw = round(price_usd * rate, decimals_from_fmt(fmts.get("price_krw")) or 0)
        if amount_krw is None and price_krw is not None and "amount_krw" in formulas:
            amount_krw = qty * price_krw
        if tax_krw is None and amount_krw is not None and "tax_krw" in formulas:
            tax_krw = round(amount_krw * 0.1, 0)
        if total_amt_krw is None and amount_krw is not None and "total_amt_krw" in formulas:
            total_amt_krw = amount_krw + (tax_krw or 0)

        # 표기 통화 판정: 환율+외화단가가 다 있으면 양쪽, 원화만 있으면 원화만, 그 반대면 외화만
        has_usd = bool(price_usd)
        has_krw = bool(price_krw)
        if has_usd and rate and has_krw:
            currency = "BOTH"
        elif has_krw:
            currency = "KRW"
        else:
            currency = "USD"

        doc_no = str(row.get("doc_no") or "").strip()
        customer = str(row.get("customer") or "").strip()
        issue_date = _to_date_str(row.get("issue_date"))
        gkey = doc_no or f"{customer}|{issue_date}"

        if gkey not in groups:
            groups[gkey] = {
                "doc_no": doc_no,
                "customer": customer,
                "issue_date": issue_date,
                "person_in_charge": str(row.get("sales") or "").strip(),
                "date": "",
                "items": [],
            }
            order.append(gkey)
        g = groups[gkey]

        d = _to_date_str(row.get("date"))
        if d and (not g["date"] or d < g["date"]):
            g["date"] = d

        g["items"].append({
            "part": part,
            "qty": qty,
            "price": price_usd,
            "amount_usd": amount_usd,
            "rate": rate,
            "price_krw": price_krw,
            "amount_krw": amount_krw,
            "tax_krw": tax_krw,
            "total_amt_krw": total_amt_krw,
            "currency": currency,
            "fmt": {k: fmts.get(k) for k in
                    ("price_usd", "amount_usd", "rate", "price_krw", "amount_krw")},
        })

    if not order:
        return {"error": "품목 행(MPN/Q'ty)을 한 건도 읽지 못했습니다."}

    invoices = []
    for k in order:
        g = groups[k]
        items = g["items"]
        has_usd = any(it["currency"] in ("BOTH", "USD") for it in items)
        has_krw = any(it["currency"] in ("BOTH", "KRW") for it in items)
        # 합계는 파일의 AMOUNT/TAX/Total 열을 그대로 더한다 (재계산하지 않는다)
        sub_usd = sum(it["amount_usd"] or 0 for it in items) if has_usd else None
        sub_krw = sum(it["amount_krw"] or 0 for it in items) if has_krw else None
        tax_krw = sum(it["tax_krw"] or 0 for it in items) if has_krw else None
        tot_krw = sum(it["total_amt_krw"] or 0 for it in items) if has_krw else None
        g["totals"] = {
            "sub_usd": sub_usd,
            "tax_usd": round(sub_usd * 0.1, 2) if sub_usd is not None else None,
            "total_usd": round(sub_usd * 1.1, 2) if sub_usd is not None else None,
            "sub_krw": sub_krw,
            "tax_krw": tax_krw,
            "total_krw": tot_krw,
        }
        g["item_count"] = len(items)
        invoices.append(g)

    return {"invoices": invoices, "invoice_count": len(invoices)}
