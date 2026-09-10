"""거래명세서 엑셀/PDF 생성 — main.py(로컬) · main_aws.py(배포) 공용.

두 백엔드가 각자 사본을 들고 있다가 갈라진 적이 있다(한쪽에만 발행일/담당자 블록,
다른 쪽에만 폰트 폴백). 받는 사람 입장에선 같은 양식이어야 하므로 한 곳으로 모았다.
배포본(main_aws.py)의 구현을 기준으로 삼는다.
"""
import io
import os
import re
import zipfile

from invoice_upload import decimals_from_fmt


def invoice_filename(data: dict) -> str:
    """거래명세서_고객사_문서번호. 윈도우 파일명에 못 쓰는 문자는 _ 로 바꾼다."""
    bad = r'[\/:*?"<>|]'
    customer = re.sub(bad, "_", str(data.get("customer") or "")).strip()
    tail = re.sub(bad, "_", str(data.get("doc_no") or data.get("date") or "")).strip()
    return "_".join(x for x in ("거래명세서", customer, tail) if x)


def build_invoice_zip_bytes(invoices) -> bytes:
    """거래명세서 여러 건을 엑셀+PDF 로 묶어 ZIP 바이트로."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used = set()
        for inv in invoices:
            base = invoice_filename(inv)
            name, n = base, 2
            while name in used:  # 문서번호가 겹쳐도 서로 덮어쓰지 않게
                name, n = f"{base}({n})", n + 1
            used.add(name)
            zf.writestr(f"{name}.xlsx", _build_invoice_xlsx_bytes(inv))
            zf.writestr(f"{name}.pdf", _build_invoice_pdf_bytes(inv))
    return buf.getvalue()


def _num(v):
    """숫자로 쓸 수 있으면 float, 아니면 None. 값 0 과 '값 없음' 을 구분해야 해서 필요하다."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_money(v, sym, fmt=None, decimals=2):
    """숫자 → 표시 문자열. 업로드 파일의 셀 서식이 있으면 그 소수점 자릿수를 그대로 따른다.

    PDF 는 엑셀 서식 코드를 쓸 수 없어서 자릿수만 뽑아 쓴다.
    """
    if v is None:
        return ""
    d = decimals_from_fmt(fmt)
    if d is None:
        d = decimals
    return f"{sym}{v:,.{d}f}"


def _fmt_price_cell(v, sym, fmt):
    """단가 칸: 업로드 서식이 있으면 그대로, 없으면 기존 규칙(소수점 2~5자리)."""
    return _fmt_money(v, sym, fmt) if fmt else _fmt_unit_price(v, sym)


def _build_invoice_xlsx_bytes(data: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.drawing.image import Image as XlImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor


    items = data.get("items", [])
    customer = data.get("customer", "")
    date_str = data.get("date", "")
    rate = float(data.get("rate", 1400))

    wb = Workbook()
    ws = wb.active
    ws.title = "거래명세서"

    widths = {"A": 5, "B": 20, "C": 8, "D": 18, "E": 16, "F": 10, "G": 20, "H": 18}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    thin = Side(style="thin")
    thick = Side(style="medium")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    left_a = Alignment(horizontal="left", vertical="center")

    # 제목
    ws.merge_cells("A1:H1")
    ws["A1"] = "거래명세표"
    ws["A1"].font = Font(bold=True, size=36)
    ws["A1"].alignment = center
    ws.row_dimensions[1].height = 55
    ws.row_dimensions[2].height = 8

    # 공급자
    ws.merge_cells("A3:C3")
    ws["A3"] = "공 급 자"
    ws["A3"].font = Font(bold=True, size=10)
    ws["A3"].alignment = center
    ws["A3"].border = Border(left=thick, top=thick, bottom=thin)
    ws["B3"].border = Border(top=thick, bottom=thin)
    ws["C3"].border = Border(right=thick, top=thick, bottom=thin)

    info = [(4, "등록번호 : 229-81-00105"), (5, "상      호 : ㈜유니트론텍"),
            (6, "대표이사 : 남궁 선"), (7, "주 : 서울 강남구 영동대로 638(삼성동, 삼보빌딩 9층)"),
            (8, "업      태 : 도.소매"), (9, "종      목 :전자부품 외")]
    for row_num, val in info:
        ws.merge_cells(f"A{row_num}:C{row_num}")
        ws[f"A{row_num}"] = val
        ws[f"A{row_num}"].font = Font(size=8)
        ws[f"A{row_num}"].alignment = left_a
        if row_num == 9:
            ws[f"A{row_num}"].border = Border(left=thick, bottom=thick)
            ws[f"B{row_num}"].border = Border(bottom=thick)
            ws[f"C{row_num}"].border = Border(right=thick, bottom=thick)
        else:
            ws[f"A{row_num}"].border = Border(left=thick)
            ws[f"C{row_num}"].border = Border(right=thick)

    # 공급받는자
    ws.merge_cells("F3:H3")
    ws["F3"] = "공급받는자"
    ws["F3"].font = Font(bold=True, size=10)
    ws["F3"].alignment = center
    ws["F3"].border = Border(left=thick, top=thick, bottom=thin)
    ws["G3"].border = Border(top=thick, bottom=thin)
    ws["H3"].border = Border(right=thick, top=thick, bottom=thin)

    ws.merge_cells("F4:H9")
    ws["F4"] = customer
    ws["F4"].font = Font(bold=True, size=16)
    ws["F4"].alignment = center
    for rn in range(4, 10):
        for cl in ["F", "G", "H"]:
            l = thick if cl == "F" else Side()
            r = thick if cl == "H" else Side()
            b = thick if rn == 9 else Side()
            ws[f"{cl}{rn}"].border = Border(left=l, right=r, bottom=b)

    # 발행일 / 담당자 (오른쪽 상단)
    doc_no = data.get("doc_no", "")
    issue_date = data.get("issue_date", "")
    person = data.get("person_in_charge", "")
    if doc_no:
        ws["K4"] = "문서번호"
        ws["K4"].font = Font(size=10)
        ws["K4"].alignment = center
        ws.merge_cells("L4:M4")
        ws["L4"] = doc_no
        ws["L4"].font = Font(size=10)
        ws["L4"].alignment = center
    if issue_date:
        ws["K5"] = "발행일"
        ws["K5"].font = Font(size=10)
        ws["K5"].alignment = center
        ws.merge_cells("L5:M5")
        ws["L5"] = issue_date
        ws["L5"].font = Font(size=10)
        ws["L5"].alignment = center
    if person:
        ws["K6"] = "담당자"
        ws["K6"].font = Font(size=10)
        ws["K6"].alignment = center
        ws.merge_cells("L6:M6")
        ws["L6"] = person
        ws["L6"].font = Font(size=10)
        ws["L6"].alignment = center

    ws["B12"] = date_str
    ws["B12"].font = Font(bold=True, size=10)

    # 헤더
    headers = ["No.", "Part #", "QTY", "U/PRICE ($)", "Amount ($)", "RATE", "U/PRICE (￦)", "AMOUNT (￦)"]
    header_fill = PatternFill(start_color="CCFF33", end_color="CCFF33", fill_type="solid")
    for j, h in enumerate(headers):
        cell = ws.cell(row=13, column=j+1)
        cell.value = h
        cell.font = Font(bold=True, size=9)
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border

    total_usd = total_krw = 0
    for i, item in enumerate(items):
        row = 14 + i
        qty = float(item.get("qty", 0))
        currency = (item.get("currency") or "USD").upper()
        # 업로드 건은 파일에 적힌 금액을 그대로 쓴다 — 여기서 환산·반올림을 다시 하지 않는다.
        # 화면 입력 건은 값이 없으니 종전대로 계산한다.
        if currency == "KRW":
            # 원화만
            price_krw = _num(item.get("price_krw"))
            if price_krw is None:
                price_krw = float(item.get("price", 0))
            amount_krw = _num(item.get("amount_krw"))
            if amount_krw is None:
                amount_krw = round(qty * price_krw, 0)
            total_krw += amount_krw
            vals = [i+1, item.get("part",""), int(qty), None, None, None, price_krw, amount_krw]
            fmts = [None, None, None, None, None, None, '₩#,##0.00###', '₩#,##0']
        elif float(item.get("rate") or 0) or rate:
            # 환율 있는 USD: 양쪽 표시. 행 RATE 가 비면 상단 환율을 쓴다
            # (화면 계산과 동일 — MicronInvoice.js: Number(it.rate) || rate)
            price_usd = float(item.get("price", 0))
            item_rate = float(item.get("rate") or 0) or rate
            amount_usd = _num(item.get("amount_usd"))
            if amount_usd is None:
                amount_usd = round(qty * price_usd, 2)
            price_krw = _num(item.get("price_krw"))
            if price_krw is None:
                price_krw = round(price_usd * item_rate, 2)
            amount_krw = _num(item.get("amount_krw"))
            if amount_krw is None:
                amount_krw = round(amount_usd * item_rate, 0)
            total_usd += amount_usd
            total_krw += amount_krw
            vals = [i+1, item.get("part",""), int(qty), price_usd, amount_usd, item_rate, price_krw, amount_krw]
            fmts = [None, None, None, '$#,##0.00###', '$#,##0.00', '#,##0.00', '₩#,##0.00###', '₩#,##0']
        else:
            # USD only: $ 컬럼만, RATE/₩ 빈칸
            price_usd = float(item.get("price", 0))
            amount_usd = _num(item.get("amount_usd"))
            if amount_usd is None:
                amount_usd = round(qty * price_usd, 2)
            total_usd += amount_usd
            vals = [i+1, item.get("part",""), int(qty), price_usd, amount_usd, None, None, None]
            fmts = [None, None, None, '$#,##0.00###', '$#,##0.00', None, None, None]
        # 업로드 파일의 셀 서식(업체별 소수점 자릿수)을 그대로 물려준다
        ov = item.get("fmt") or {}
        for idx, key in ((3, "price_usd"), (4, "amount_usd"), (5, "rate"),
                         (6, "price_krw"), (7, "amount_krw")):
            if ov.get(key) and vals[idx] is not None:
                fmts[idx] = ov[key]
        for j, (v, fmt) in enumerate(zip(vals, fmts)):
            c = ws.cell(row=row, column=j+1, value=v)
            c.alignment = center
            c.border = border
            if fmt:
                c.number_format = fmt

    for i in range(len(items), 10):
        row = 14 + i
        ws.cell(row=row, column=1, value=i+1).alignment = center
        for j in range(1, 9):
            ws.cell(row=row, column=j).border = border

    sr = 24
    # 업로드 건은 파일의 AMOUNT/TAX/Total 열 합계를 그대로 쓴다.
    # 값이 None 인 통화(원화 전용 건의 $ 쪽)는 칸을 비운다.
    tot = data.get("totals") or {}
    sub_usd = tot.get("sub_usd", total_usd)
    sub_krw = tot.get("sub_krw", total_krw)
    tax_usd = tot.get("tax_usd", round(total_usd * 0.1, 2))
    tax_krw = tot.get("tax_krw", round(total_krw * 0.1, 0))
    sum_usd = tot.get("total_usd", total_usd + round(total_usd * 0.1, 2))
    sum_krw = tot.get("total_krw", total_krw + round(total_krw * 0.1, 0))
    for label, uv, kv, off in [("소  계", sub_usd, sub_krw, 0), ("부가세", tax_usd, tax_krw, 1),
                                ("합  계", sum_usd, sum_krw, 2)]:
        r = sr + off
        fnt = Font(bold=True, size=9)
        ws.cell(row=r, column=4, value=label).font = fnt
        ws.cell(row=r, column=4).alignment = center
        ws.cell(row=r, column=5, value=uv).number_format = '$#,##0.00'
        ws.cell(row=r, column=5).alignment = center
        ws.cell(row=r, column=5).font = fnt
        ws.cell(row=r, column=7, value=label).font = fnt
        ws.cell(row=r, column=7).alignment = center
        ws.cell(row=r, column=8, value=kv).number_format = '₩#,##0'
        ws.cell(row=r, column=8).alignment = center
        ws.cell(row=r, column=8).font = fnt
        for j in [4, 5, 7, 8]:
            ws.cell(row=r, column=j).border = border

    # 합계 아래 줄
    for j in range(1, 9):
        c = ws.cell(row=sr+2, column=j)
        c.border = Border(left=c.border.left, right=c.border.right, top=c.border.top, bottom=Side(style="medium"))

    ws.merge_cells("A27:H27")
    # 비고 칸은 빈칸으로 둔다 — 적용환율 기준(최초고시/평균/특정일자)이 건마다 달라
    # 고정 문구를 찍으면 사실과 다를 수 있다.
    ws["A27"] = ""
    ws["A27"].font = Font(size=9)

    ws.merge_cells("E30:H30")
    ws["E30"] = "인수자 :                                              (인)"
    for j in range(1, 9):
        ws.cell(row=30, column=j).border = Border(bottom=thin)

    ws["B31"] = "계좌정보"
    ws["B31"].font = Font(bold=True, size=9)
    ws["B32"] = "원화> 기업은행 528-002245-01011"
    ws["B32"].font = Font(size=9)
    ws["B33"] = "외화> 기업은행 528-002245-56-00013"
    ws["B33"].font = Font(size=9)

    stamp_path = os.path.join(os.path.dirname(__file__), "stamp.png")
    if os.path.exists(stamp_path):
        img = XlImage(stamp_path)
        img.width = 75
        img.height = 75
        m1 = AnchorMarker(col=2, colOff=300000, row=1, rowOff=50000)
        m2 = AnchorMarker(col=3, colOff=200000, row=4, rowOff=100000)
        img.anchor = TwoCellAnchor(_from=m1, to=m2)
        ws.add_image(img)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def _fmt_unit_price(v, sym):
    """단가 포맷: 소수점 2~5자리, trailing 0 제거 (단 최소 2자리 유지)."""
    if v is None:
        return ""
    s = f"{v:,.5f}"
    if "." in s:
        intpart, dec = s.split(".")
        dec = dec.rstrip("0")
        if len(dec) < 2:
            dec = (dec + "00")[:2]
        s = f"{intpart}.{dec}"
    return f"{sym}{s}"


def _build_invoice_pdf_bytes(data: dict) -> bytes:
    """reportlab으로 거래명세서 PDF 생성 (양식 PDF 기준 레이아웃)."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import Color, black
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    # 번들 폰트 우선 (PDF에 임베드 → 뷰어 의존성 제거, ₩ 확실히 렌더)
    KF = None
    bundled = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic.ttf")
    if os.path.exists(bundled):
        try:
            pdfmetrics.registerFont(TTFont("KoreanFont", bundled))
            KF = "KoreanFont"
        except Exception:
            pass
    # 시스템 NotoSansCJK 폴백
    if not KF:
        import glob
        candidates = []
        for ext in ("ttc", "otf", "ttf"):
            candidates.extend(glob.glob(f"/usr/share/fonts/**/NotoSans*CJK*.{ext}", recursive=True))
        for path in list(dict.fromkeys(candidates)):
            if path.endswith(".ttc"):
                for sub_idx in range(8):
                    try:
                        pdfmetrics.registerFont(TTFont("KoreanFont", path, subfontIndex=sub_idx))
                        if pdfmetrics.stringWidth("₩", "KoreanFont", 10) > 2:
                            KF = "KoreanFont"
                            break
                    except Exception:
                        continue
            else:
                try:
                    pdfmetrics.registerFont(TTFont("KoreanFont", path))
                    KF = "KoreanFont"
                except Exception:
                    pass
            if KF:
                break
    if not KF:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
            KF = "HYGothic-Medium"
        except Exception:
            KF = "Helvetica"
    KF_BOLD = KF
    WON = "₩"

    items = data.get("items", [])
    customer = data.get("customer", "")
    default_rate = float(data.get("rate", 1400))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    margin_x = 28
    margin_top = 25
    uw = W - 2 * margin_x
    grey = Color(0.88, 0.88, 0.88)

    c.setLineWidth(0.8)

    # === 9열 컬럼 너비 (pt, 긴 텍스트 맞춤) ===
    # A(No.)/B(Part#)/C(QTY)/D(U/P$)/E(Amt$)/F(Rate)/G(U/P₩)/H(Amt₩)/I(비고)
    # Part# 14자 수용 + 요약 라벨 "공급가액 합계(₩)" 수용
    # 넉넉한 패딩 (모든 셀 최소 10pt 이상 여유, Part#는 24pt+ 여유)
    # Part#: 100pt (14char 76+24pad) / 19char Part#는 auto-shrink로 대응
    # 요약 라벨은 8pt 폰트로 안전 수용
    # U/P 컬럼은 5자리 소수점까지 수용 (예: $0.00645) — D, G 확장
    col_w_pt = [22, 90, 43, 78, 54, 50, 84, 66, 38]
    assert sum(col_w_pt) == 525, f"col sum {sum(col_w_pt)}"
    scale = uw / sum(col_w_pt)
    col_w = [w * scale for w in col_w_pt]
    x_bounds = [margin_x]
    for w in col_w:
        x_bounds.append(x_bounds[-1] + w)
    # x_bounds[0..9]

    # === 레이아웃 높이 계획 ===
    title_h = 55
    info_row_h = 18  # A4-A9 각 행 높이 축소
    box_header_h = 28
    box_body_h = info_row_h * 6  # 6 info lines → 108
    box_total_h = box_header_h + box_body_h  # 136
    gap_before_table = 18
    table_header_h = 24
    data_row_h = 19
    n_data = max(24, len(items))  # 양식은 24행 (row 14-37)
    summary_row_h = 22
    summary_total_h = summary_row_h * 3 + 10  # 3 rows + bottom pad

    # === 제목 영역 ===
    title_y = H - margin_top - title_h + 18  # 베이스라인
    c.setFont(KF_BOLD, 34)
    c.drawCentredString(W / 2, title_y, "거래명세서")

    # 문서번호 / 발행일 / 담당자 (제목 아래 우측)
    doc_no = data.get("doc_no", "")
    issue_date = data.get("issue_date", "")
    person = data.get("person_in_charge", "")
    extra_offset = 0
    if doc_no or issue_date or person:
        c.setFont(KF, 9)
        extra_y = title_y - 22
        for _label, _val in (("문서번호", doc_no), ("발행일", issue_date), ("담당자", person)):
            if not _val:
                continue
            c.drawRightString(margin_x + uw, extra_y, f"{_label} : {_val}")
            extra_y -= 12
            extra_offset += 12

    # === 공급자 / 공급받는자 박스 ===
    box_top = H - margin_top - title_h - 10 - extra_offset
    # 공급자: 컬럼 A~D (x_bounds[0] → x_bounds[4])
    # 공급받는자: 컬럼 F~I (x_bounds[5] → x_bounds[9])
    # 컬럼 E는 공백 gap
    sup_x = x_bounds[0]
    sup_w = x_bounds[4] - x_bounds[0]
    cust_x = x_bounds[5]
    cust_w = x_bounds[9] - x_bounds[5]

    # 공급자 박스
    c.setFillColor(grey)
    c.rect(sup_x, box_top - box_header_h, sup_w, box_header_h, stroke=1, fill=1)
    c.setFillColor(black)
    c.rect(sup_x, box_top - box_total_h, sup_w, box_total_h, stroke=1, fill=0)
    c.line(sup_x, box_top - box_header_h, sup_x + sup_w, box_top - box_header_h)
    c.setFont(KF_BOLD, 12)
    c.drawCentredString(sup_x + sup_w / 2, box_top - box_header_h + 10, "공 급 자")

    # 공급받는자 박스
    c.setFillColor(grey)
    c.rect(cust_x, box_top - box_header_h, cust_w, box_header_h, stroke=1, fill=1)
    c.setFillColor(black)
    c.rect(cust_x, box_top - box_total_h, cust_w, box_total_h, stroke=1, fill=0)
    c.line(cust_x, box_top - box_header_h, cust_x + cust_w, box_top - box_header_h)
    c.setFont(KF_BOLD, 12)
    c.drawCentredString(cust_x + cust_w / 2, box_top - box_header_h + 10, "공급받는자")

    # 공급자 정보 (6줄, 박스 안에 정확히 맞춤)
    info = [
        "등록번호 : 229-81-00105",
        "상      호 : ㈜유니트론텍",
        "대표이사 : 남궁 선",
        "주      소 : 서울 강남구 영동대로 638(삼성동, 삼보빌딩 9층)",
        "업      태 : 도.소매",
        "종      목 : 전자부품 외",
    ]
    c.setFont(KF, 8.5)
    info_top_y = box_top - box_header_h - 12
    for i, line in enumerate(info):
        c.drawString(sup_x + 6, info_top_y - i * info_row_h, line)

    # 도장 (공급자 박스 우측 상단, 텍스트와 안 겹치게)
    stamp_path = os.path.join(os.path.dirname(__file__), "stamp.png")
    if os.path.exists(stamp_path):
        try:
            ss = 42
            c.drawImage(stamp_path, sup_x + sup_w - ss - 12, box_top - box_header_h - 8 - ss,
                        ss, ss, mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

    # 공급받는자 이름 (중앙)
    c.setFont(KF_BOLD, 12)
    cust_center_y = box_top - box_header_h - box_body_h / 2 - 4
    c.drawCentredString(cust_x + cust_w / 2, cust_center_y, customer)

    # === 품목 테이블 ===
    table_top = box_top - box_total_h - gap_before_table
    headers = ["No.", "Part #", "QTY", "U/PRICE ($)", "Amount ($)", "RATE", f"U/PRICE ({WON})", f"AMOUNT ({WON})", "비고"]

    # 헤더 행
    c.setFillColor(grey)
    c.rect(margin_x, table_top - table_header_h, uw, table_header_h, stroke=1, fill=1)
    c.setFillColor(black)
    c.setFont(KF_BOLD, 9.5)
    for i, h in enumerate(headers):
        c.drawCentredString((x_bounds[i] + x_bounds[i + 1]) / 2, table_top - table_header_h + 8, h)
        if i > 0:
            c.line(x_bounds[i], table_top, x_bounds[i], table_top - table_header_h)

    # 데이터 행
    total_usd = 0
    total_krw = 0
    total_qty = 0
    c.setFont(KF, 9)
    data_top = table_top - table_header_h
    for i in range(n_data):
        row_btm = data_top - data_row_h * (i + 1)
        c.rect(margin_x, row_btm, uw, data_row_h, stroke=1, fill=0)
        for j in range(1, 9):
            c.line(x_bounds[j], row_btm, x_bounds[j], row_btm + data_row_h)

        if i < len(items):
            item = items[i]
            qty = float(item.get("qty", 0))
            currency = (item.get("currency") or "USD").upper()
            note = str(item.get("date", "") or "")
            # 업로드 파일의 셀 서식(업체별 소수점 자릿수)이 있으면 그 자릿수로 찍는다
            ov = item.get("fmt") or {}
            if currency == "KRW":
                # 원화만
                price_krw = _num(item.get("price_krw"))
                if price_krw is None:
                    price_krw = float(item.get("price", 0))
                amount_krw = _num(item.get("amount_krw"))
                if amount_krw is None:
                    amount_krw = round(qty * price_krw, 0)
                total_krw += amount_krw
                total_qty += qty
                vals = [
                    str(i + 1), str(item.get("part", "")), f"{int(qty):,}",
                    "", "", "",
                    _fmt_price_cell(price_krw, WON, ov.get("price_krw")),
                    _fmt_money(amount_krw, WON, ov.get("amount_krw"), 0), note,
                ]
            elif float(item.get("rate") or 0) or default_rate:
                # 환율 있음: 양쪽. 행 RATE 가 비면 상단 환율을 쓴다 (화면 계산과 동일)
                price = float(item.get("price", 0))
                item_rate = float(item.get("rate") or 0) or default_rate
                amount_usd = _num(item.get("amount_usd"))
                if amount_usd is None:
                    amount_usd = round(qty * price, 2)
                price_krw = _num(item.get("price_krw"))
                if price_krw is None:
                    price_krw = round(price * item_rate, 2)
                amount_krw = _num(item.get("amount_krw"))
                if amount_krw is None:
                    amount_krw = round(amount_usd * item_rate, 0)
                total_usd += amount_usd
                total_krw += amount_krw
                total_qty += qty
                vals = [
                    str(i + 1), str(item.get("part", "")), f"{int(qty):,}",
                    _fmt_price_cell(price, "$", ov.get("price_usd")),
                    _fmt_money(amount_usd, "$", ov.get("amount_usd"), 2),
                    _fmt_money(item_rate, "", ov.get("rate"), 2),
                    _fmt_price_cell(price_krw, WON, ov.get("price_krw")),
                    _fmt_money(amount_krw, WON, ov.get("amount_krw"), 0), note,
                ]
            else:
                # USD only
                price = float(item.get("price", 0))
                amount_usd = _num(item.get("amount_usd"))
                if amount_usd is None:
                    amount_usd = round(qty * price, 2)
                total_usd += amount_usd
                total_qty += qty
                vals = [
                    str(i + 1), str(item.get("part", "")), f"{int(qty):,}",
                    _fmt_price_cell(price, "$", ov.get("price_usd")),
                    _fmt_money(amount_usd, "$", ov.get("amount_usd"), 2), "",
                    "", "", note,
                ]
        else:
            vals = ["", "", "", "", "", "", "", "", ""]
        # 모든 셀: 내용이 셀 넓이를 초과하면 자동 축소 — 절대 셀 벗어나지 않음
        for j, v in enumerate(vals):
            if not v:
                continue
            s = str(v)
            cell_w = col_w[j] - 6  # 양쪽 3pt 여백
            text_w = pdfmetrics.stringWidth(s, KF, 9)
            fs = 9
            if text_w > cell_w:
                fs = max(5.5, 9 * cell_w / text_w)
            c.setFont(KF, fs)
            if j == 1:  # Part# 좌측정렬
                c.drawString(x_bounds[j] + 3, row_btm + 6, s)
            else:
                c.drawCentredString((x_bounds[j] + x_bounds[j + 1]) / 2, row_btm + 6, s)
        c.setFont(KF, 9)

    # === 요약 영역 (양식 파일 row 38-40 구조) ===
    # 행 38: [소계 A:B병합] [C=qty] [D=$라벨] [E:F=$값 병합] [G=₩라벨] [H:I=₩값 병합]
    # 행 39: [비고 A:C병합 (2행높이)] [D=$부가세라벨] [E:F=$부가세값] [G=₩부가세라벨] [H:I=₩부가세값]
    # 행 40: [(A:C는 39와 병합)] [D=총금액$ 라벨] [E:F=총금액$ 값] [G=총금액₩ 라벨] [H:I=총금액₩ 값]
    sum_top = data_top - data_row_h * n_data
    # 업로드 건은 파일의 AMOUNT/TAX/Total 열 합계를 그대로 쓴다.
    # None 인 통화(원화 전용 건의 $ 쪽)는 칸을 비운다.
    tot = data.get("totals") or {}
    sub_usd = tot.get("sub_usd", total_usd)
    sub_krw = tot.get("sub_krw", total_krw)
    tax_usd = tot.get("tax_usd", round(total_usd * 0.1, 2))
    tax_krw = tot.get("tax_krw", round(total_krw * 0.1, 0))
    total_usd_sum = tot.get("total_usd", total_usd + round(total_usd * 0.1, 2))
    total_krw_sum = tot.get("total_krw", total_krw + round(total_krw * 0.1, 0))

    r38_top = sum_top
    r38_btm = r38_top - summary_row_h
    r39_top = r38_btm
    r39_btm = r39_top - summary_row_h
    r40_top = r39_btm
    r40_btm = r40_top - summary_row_h

    # ------- 행 38 (소계) -------
    # A:B 병합 "소 계"
    c.rect(x_bounds[0], r38_btm, x_bounds[2] - x_bounds[0], summary_row_h, stroke=1, fill=0)
    # C (qty)
    c.rect(x_bounds[2], r38_btm, x_bounds[3] - x_bounds[2], summary_row_h, stroke=1, fill=0)
    # D ($라벨)
    c.rect(x_bounds[3], r38_btm, x_bounds[4] - x_bounds[3], summary_row_h, stroke=1, fill=0)
    # E:F 병합 ($값)
    c.rect(x_bounds[4], r38_btm, x_bounds[6] - x_bounds[4], summary_row_h, stroke=1, fill=0)
    # G (₩라벨)
    c.rect(x_bounds[6], r38_btm, x_bounds[7] - x_bounds[6], summary_row_h, stroke=1, fill=0)
    # H:I 병합 (₩값)
    c.rect(x_bounds[7], r38_btm, x_bounds[9] - x_bounds[7], summary_row_h, stroke=1, fill=0)

    # ------- 행 39+40 (비고 왼쪽은 세로 병합) -------
    # A:C 병합, rows 39+40 세로 병합 (한 큰 셀)
    c.rect(x_bounds[0], r40_btm, x_bounds[3] - x_bounds[0], summary_row_h * 2, stroke=1, fill=0)
    # 오른쪽: 행 39
    c.rect(x_bounds[3], r39_btm, x_bounds[4] - x_bounds[3], summary_row_h, stroke=1, fill=0)  # D
    c.rect(x_bounds[4], r39_btm, x_bounds[6] - x_bounds[4], summary_row_h, stroke=1, fill=0)  # E:F
    c.rect(x_bounds[6], r39_btm, x_bounds[7] - x_bounds[6], summary_row_h, stroke=1, fill=0)  # G
    c.rect(x_bounds[7], r39_btm, x_bounds[9] - x_bounds[7], summary_row_h, stroke=1, fill=0)  # H:I
    # 오른쪽: 행 40
    c.rect(x_bounds[3], r40_btm, x_bounds[4] - x_bounds[3], summary_row_h, stroke=1, fill=0)
    c.rect(x_bounds[4], r40_btm, x_bounds[6] - x_bounds[4], summary_row_h, stroke=1, fill=0)
    c.rect(x_bounds[6], r40_btm, x_bounds[7] - x_bounds[6], summary_row_h, stroke=1, fill=0)
    c.rect(x_bounds[7], r40_btm, x_bounds[9] - x_bounds[7], summary_row_h, stroke=1, fill=0)

    # === 요약 영역 텍스트 (auto-shrink로 셀 초과 방지) ===
    def draw_cell(text, left_x, right_x, y, base_fs=8, right_align=False):
        if not text:
            return
        cell_w = (right_x - left_x) - 6
        tw = pdfmetrics.stringWidth(text, KF, base_fs)
        fs = base_fs
        if tw > cell_w and tw > 0:
            fs = max(5.5, base_fs * cell_w / tw)
        c.setFont(KF, fs)
        if right_align:
            c.drawRightString(right_x - 4, y, text)
        else:
            c.drawCentredString((left_x + right_x) / 2, y, text)

    # 행 38
    draw_cell("소  계", x_bounds[0], x_bounds[2], r38_btm + 7)
    draw_cell(f"{int(total_qty):,}", x_bounds[2], x_bounds[3], r38_btm + 7)
    draw_cell("공급가액 합계($)" if sub_usd is not None else "", x_bounds[3], x_bounds[4], r38_btm + 7)
    draw_cell(_fmt_money(sub_usd, "$", None, 2), x_bounds[4], x_bounds[6], r38_btm + 7)
    draw_cell(f"공급가액 합계({WON})" if sub_krw is not None else "", x_bounds[6], x_bounds[7], r38_btm + 7)
    draw_cell(_fmt_money(sub_krw, WON, None, 0), x_bounds[7], x_bounds[9], r38_btm + 7)
    # 행 39
    draw_cell("부가세($)" if tax_usd is not None else "", x_bounds[3], x_bounds[4], r39_btm + 7)
    draw_cell(_fmt_money(tax_usd, "$", None, 2), x_bounds[4], x_bounds[6], r39_btm + 7)
    draw_cell(f"부가세({WON})" if tax_krw is not None else "", x_bounds[6], x_bounds[7], r39_btm + 7)
    draw_cell(_fmt_money(tax_krw, WON, None, 0), x_bounds[7], x_bounds[9], r39_btm + 7)
    # 행 40
    draw_cell("총 금액($)" if total_usd_sum is not None else "", x_bounds[3], x_bounds[4], r40_btm + 7)
    draw_cell(_fmt_money(total_usd_sum, "$", None, 2), x_bounds[4], x_bounds[6], r40_btm + 7)
    draw_cell(f"총 금액({WON})" if total_krw_sum is not None else "", x_bounds[6], x_bounds[7], r40_btm + 7)
    draw_cell(_fmt_money(total_krw_sum, WON, None, 0), x_bounds[7], x_bounds[9], r40_btm + 7)
    # 비고 칸은 빈칸으로 둔다 — 적용환율 기준(최초고시/평균/특정일자)이 건마다 달라
    # 고정 문구를 찍으면 사실과 다를 수 있다. 테두리는 위에서 이미 그렸다.

    c.showPage()
    c.save()
    return buf.getvalue()
