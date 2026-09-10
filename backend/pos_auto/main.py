# -*- coding: utf-8 -*-
"""Microchip POS Report 자동 생성 — 진입점.

사용 예:
  python -m src.main --rawdata "input/RAWDATA.xlsx" \
      --start-date 2026-03-01 --end-date 2026-03-31 --output-dir output
"""
import argparse
import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List

import yaml

if __package__ in (None, ""):                      # 직접 실행 대응
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "src"

from . import normalizer as N
from . import price_engine, schema_mapper, validator
from .address_source import CrmAddressSource
from .customer_matcher import CustomerMatcher
from .excel_loader import Workbook, file_sha256
from .excel_writer import write_address_master_template, write_result
from .models import ExceptionRec, ProcessResult
from .normalizer import CustomerNameNormalizer
from .pos_generator import PosGenerator
from .qtn_allocator import QTNAllocator

ROOT = Path(__file__).resolve().parent.parent


def setup_logging(log_dir: Path, run_id: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pos")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(log_dir / f"run_{run_id}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def make_run_id(parts: List[str]) -> str:
    """같은 입력·같은 설정이면 항상 같은 RunID (결정론적 재실행 보장)."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10]


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_approved_mapping(path) -> Dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    import pandas as pd
    df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xlsm") else pd.read_csv(p)
    cols = {str(c).strip(): c for c in df.columns}
    name_col = cols.get("QTN 원본 고객명") or cols.get("고객명") or list(df.columns)[0]
    code_col = cols.get("담당자 선택") or cols.get("고객코드") or list(df.columns)[1]
    out = {}
    for _, r in df.iterrows():
        n, c = N.text(r[name_col]), N.code(r[code_col])
        if n and c:
            out[n.upper()] = c
    return out


def load_address_master(path) -> Dict[str, dict]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    import pandas as pd
    df = pd.read_excel(p)
    out = {}
    for _, r in df.iterrows():
        code = N.code(r.get("고객코드"))
        if not code:
            continue
        out[code] = {k: N.text(r.get(k)) for k in
                     ("purchasing_city", "purchasing_state", "purchasing_postal",
                      "purchasing_country", "end_city", "end_state", "end_postal",
                      "end_country", "disti_customer_number")}
    return out


def run(args) -> int:
    cfg = load_config(Path(args.config))
    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    out_dir = Path(args.output_dir)

    raw_hash = file_sha256(args.rawdata)
    run_id = make_run_id([raw_hash, args.start_date, args.end_date,
                          cfg.get("rule_version", "?"), args.mode])
    logger = setup_logging(Path(args.log_dir), run_id)
    logger.info(f"=== POS Report 자동화 시작 (RunID {run_id}, mode={args.mode}) ===")
    logger.info(f"대상 기간: {start} ~ {end}")
    logger.info(f"RAWDATA: {args.rawdata}  sha256={raw_hash[:16]}…")

    namer = CustomerNameNormalizer(cfg.get("customer_normalization", {}))
    wb = Workbook(args.rawdata)
    sheets_cfg, cols_cfg = cfg["sheets"], cfg["columns"]
    scan = int(cfg.get("header_scan_rows", 6))

    crm = CrmAddressSource(args.crm_customers, cfg.get("address_source", {}))
    if crm.rows:
        logger.info(f"CRM 주소 원천: {Path(args.crm_customers).name} ({crm.rows:,}곳)")
    elif args.crm_customers:
        logger.info("CRM 주소 파일을 읽지 못했습니다 — 주소는 주소 마스터로만 채워집니다.")

    resolved = {}
    for key in ("shipment", "qtn", "asd", "customer_map"):
        name = wb.find_sheet(sheets_cfg[key])
        if not name:
            logger.error(f"[MISSING_REQUIRED_SHEET] '{key}' 시트를 찾지 못했습니다. "
                         f"후보={sheets_cfg[key]} / 실제={wb.sheet_names}")
            return 2
        resolved[key] = name
    logger.info("사용 시트: " + ", ".join(f"{k}={v}" for k, v in resolved.items()))

    df_sh, map_sh, hdr_sh = wb.read(resolved["shipment"], cols_cfg["shipment"], scan)
    df_q, map_q, hdr_q = wb.read(resolved["qtn"], cols_cfg["qtn"], scan)
    df_a, map_a, hdr_a = wb.read(resolved["asd"], cols_cfg["asd"], scan)
    df_c, map_c, hdr_c = wb.read(resolved["customer_map"], cols_cfg["customer_map"], scan)
    for key, mp, need in (("shipment", map_sh, ["ship_date", "customer_code", "part_number", "ship_qty"]),
                          ("qtn", map_q, ["part_number", "qtn_price"]),
                          ("asd", map_a, ["part_number", "asd_price"]),
                          ("customer_map", map_c, ["customer_code"])):
        miss = [c for c in need if c not in mp]
        if miss:
            logger.error(f"[MISSING_REQUIRED_COLUMN] {key}: {miss}")
            return 2
    logger.info(f"컬럼 매핑 완료 — 출고 {len(map_sh)}개 / QTN {len(map_q)}개 / "
                f"ASD {len(map_a)}개 / 매핑표 {len(map_c)}개")

    shipments_all, exc_map = schema_mapper.map_shipments(
        df_sh, map_sh, hdr_sh, Path(args.rawdata).name, resolved["shipment"])
    cust_entries = schema_mapper.map_customer_map(df_c, map_c, hdr_c, namer)
    qtns = schema_mapper.map_qtns(df_q, map_q, hdr_q, namer)
    asds = schema_mapper.map_asds(df_a, map_a, hdr_a)
    logger.info(f"원본 로드 — 출고 {len(shipments_all):,} / QTN {len(qtns):,} / "
                f"ASD {len(asds):,} / 고객매핑 {len(cust_entries):,}")

    matcher = CustomerMatcher(cust_entries, namer, cfg.get("customer_normalization", {}),
                              load_approved_mapping(args.approved_mapping))
    qtns, review_rows = matcher.apply(qtns)
    st = Counter(q.match_status for q in qtns)
    logger.info(f"QTN 고객 매칭 — {dict(st)}")

    shipments = [s for s in shipments_all if s.ship_date and start <= s.ship_date <= end]
    shipments.sort(key=lambda s: (s.ship_date, s.invoice_number, s.invoice_item_number, s.source_row))
    logger.info(f"대상 기간 출고: {len(shipments):,}행")

    # 표준 수입가(단가1 DC) — 유효한 QTN·ASD 가 없을 때 Disti Purchase Cost 로 쓴다.
    import_cost: Dict[str, float] = {}
    pc_sheet = wb.find_sheet(sheets_cfg.get("purchase_cost", []))
    if pc_sheet:
        df_pc, map_pc, _h = wb.read(pc_sheet, cols_cfg["purchase_cost"], scan)
        if "part_number" in map_pc and "price" in map_pc:
            for _i, r in df_pc.iterrows():
                p = N.part(r.get(map_pc["part_number"]))
                v = N.number(r.get(map_pc["price"]))
                if p and v is not None and p not in import_cost:
                    import_cost[p] = v
        logger.info(f"표준 수입가 로드: {pc_sheet} — {len(import_cost):,}품번")

    asd_by_part = defaultdict(list)
    for a in asds:
        asd_by_part[a.part_number].append(a)
    allocator = QTNAllocator(qtns)
    customer_by_code = {e.customer_code: e for e in cust_entries}
    disti_by_code = {}
    for q in qtns:
        if q.customer_code and q.disti_customer_number:
            disti_by_code.setdefault(q.customer_code, q.disti_customer_number)

    qtn_rules, asd_rules = cfg["qtn_rules"], cfg["asd_rules"]
    tie = cfg.get("price_selection", {}).get("tie_breaker", "QTN")
    no_price_policy = cfg.get("pricing_fallback", {}).get("no_price_policy",
                                                          "report_with_import_cost")
    sev = cfg.get("severity", {})
    excs: List[ExceptionRec] = [e for e in exc_map
                                if any(s.shipment_id == e.shipment_id for s in shipments)]
    results: List[ProcessResult] = []
    seen_ids = Counter(s.shipment_id for s in shipments)

    for sh in shipments:
        res = ProcessResult(shipment=sh)
        loc = f"{sh.source_sheet}!{sh.source_row}행"
        cust = customer_by_code.get(sh.customer_code)
        res.customer_name = cust.customer_name_original if cust else sh.customer_name_original

        if seen_ids[sh.shipment_id] > 1:
            res.processing_status = "EXCEPTION"
            res.add_exception("DUPLICATE_SHIPMENT")
            excs.append(ExceptionRec("DUPLICATE_SHIPMENT", sev.get("DUPLICATE_SHIPMENT", "ERROR"),
                                     sh.shipment_id, loc, "동일 출고 고유키가 2건 이상입니다",
                                     False, "원본 출고 데이터 중복 확인"))
            results.append(res)
            continue
        if sh.ship_qty is not None and sh.ship_qty < 0:
            res.processing_status = "EXCEPTION"
            res.add_exception("RETURN_REVIEW")
            res.review_required = True
            excs.append(ExceptionRec("RETURN_REVIEW", sev.get("RETURN_REVIEW", "REVIEW"),
                                     sh.shipment_id, loc, "마이너스 출고(반품)는 자동 처리하지 않습니다",
                                     False, "반품 처리 방침 확인 후 수동 반영"))
            results.append(res)
            continue
        if sh.ship_date is None or sh.ship_qty is None:
            res.processing_status = "EXCEPTION"
            res.add_exception("INVALID_DATE" if sh.ship_date is None else "INVALID_QUANTITY")
            results.append(res)
            continue
        if cust is None:
            res.add_exception("CUSTOMER_NOT_MATCHED")
            res.review_required = True
            res.processing_status = "EXCEPTION"
            excs.append(ExceptionRec("CUSTOMER_NOT_MATCHED", sev.get("CUSTOMER_NOT_MATCHED", "REVIEW"),
                                     sh.shipment_id, loc,
                                     f"고객코드 {sh.customer_code} 가 매핑표에 없습니다", False,
                                     "업체코드 매칭 시트에 해당 코드 추가"))
            results.append(res)
            continue

        qc = allocator.candidates(sh.customer_code, sh.part_number)
        ok_q, rej_q = price_engine.eligible_qtns(qc, sh.ship_date, float(sh.ship_qty), qtn_rules)
        ac = asd_by_part.get(sh.part_number, [])
        ok_a, rej_a = price_engine.eligible_asds(ac, sh.ship_date, asd_rules)
        res.qtn_candidate_count, res.asd_candidate_count = len(ok_q), len(ok_a)

        sel_q = ok_q[0] if ok_q else None
        sel_a = ok_a[0] if ok_a else None
        res.selected_qtn, res.selected_asd = sel_q, sel_a
        res.qtn_candidate_price = sel_q.qtn_price if sel_q else None
        res.asd_candidate_price = sel_a.asd_price if sel_a else None

        if price_engine.has_price_conflict(ok_q, "qtn_price"):
            res.add_exception("MULTIPLE_QTN_CONFLICT")
            res.review_required = True
            excs.append(ExceptionRec("MULTIPLE_QTN_CONFLICT", sev.get("MULTIPLE_QTN_CONFLICT", "REVIEW"),
                                     sh.shipment_id, loc, "동일 우선순위 QTN 의 가격이 서로 다릅니다",
                                     False, "QTN 원본 확인"))
        if price_engine.has_price_conflict(ok_a, "asd_price"):
            res.add_exception("MULTIPLE_ASD_CONFLICT")
            res.review_required = True

        ptype, price, reason, rule_id = price_engine.select(sel_q, sel_a, tie)
        res.applied_price_type, res.applied_price = ptype, price
        res.selection_reason, res.rule_id = reason, rule_id

        for code in set(rej_q):
            res.add_exception(code)
        for code in set(rej_a):
            res.add_exception(code)
        if not qc:
            res.add_exception("NO_VALID_QTN")
        if not ac:
            res.add_exception("PART_NUMBER_NOT_FOUND" if not qc else "NO_VALID_ASD")

        if ptype == "QTN":
            res.qtn_remaining_before = allocator.pool[sel_q.qtn_id]
            allocator.consume(sel_q, float(sh.ship_qty), sh.shipment_id, sh.ship_date)
            res.qtn_used_qty = float(sh.ship_qty)
            res.qtn_remaining_after = allocator.pool[sel_q.qtn_id]
            res.applied_quote_number = sel_q.quote_number
            res.applied_quote_item_number = sel_q.quote_item_number
        if ptype == "NONE":
            res.add_exception("NO_VALID_PRICE")
            res.review_required = True
            reason = sorted(set(rej_q + rej_a)) or ["후보 자체 없음"]
            fallback = import_cost.get(sh.part_number)
            if no_price_policy == "report_with_import_cost" and fallback is not None:
                res.disti_cost, res.disti_cost_source = fallback, "IMPORT_PRICE"
                res.selection_reason = (f"유효 QTN·ASD 없음({', '.join(reason)}) → "
                                        f"표준 수입가 {fallback} 적용")
                excs.append(ExceptionRec("NO_VALID_PRICE", "WARNING", sh.shipment_id, loc,
                                         f"특가 없음 → 표준 수입가로 보고 (사유: {', '.join(reason)})",
                                         True, "필요 시 QTN/ASD 유효기간·수량 확인"))
            else:
                res.processing_status = "EXCEPTION"
                res.disti_cost_source = "NONE"
                excs.append(ExceptionRec("NO_VALID_PRICE", sev.get("NO_VALID_PRICE", "REVIEW"),
                                         sh.shipment_id, loc,
                                         f"유효한 QTN·ASD 없음 (사유: {', '.join(reason)})",
                                         False, "QTN/ASD 유효기간·수량 확인 또는 표준 수입가 등록"))
        else:
            res.disti_cost, res.disti_cost_source = res.applied_price, "APPLIED"
        results.append(res)

    address_master = load_address_master(args.address_master)
    gen = PosGenerator(cfg["pos_fields"], customer_by_code, namer, address_master, disti_by_code,
                       crm=crm, strict_vendor_fields=(args.mode == "final"))
    pos_rows, pos_excs, mapping_table = gen.build(results)
    excs.extend(pos_excs)
    addr_needed = gen.address_master_needed(results)

    period = f"{start} ~ {end}"
    summary = validator.summarize(results, pos_rows, excs, allocator, sev, period)
    submit = validator.submit_ok(summary)

    if args.mode == "final" and not submit:
        logger.error("final 모드는 미해결 필수 예외가 0건일 때만 생성됩니다. "
                     "먼저 draft 로 예외를 해소하세요.")
        for r in summary:
            if r["항목"] in ("ERROR 예외 수", "REVIEW 예외 수", "필수 POS 필드 누락 건수"):
                logger.error(f"   {r['항목']}: {r['값']}")
        return 3

    proc_rows = [{
        "출고 고유키": r.shipment.shipment_id, "원본 행 번호": r.shipment.source_row,
        "출고일": r.shipment.ship_date, "고객코드": r.shipment.customer_code,
        "고객명": r.customer_name, "품번": r.shipment.part_number,
        "출고수량": r.shipment.ship_qty, "QTN 후보 수": r.qtn_candidate_count,
        "선택 QTN": r.selected_qtn.quote_number if r.selected_qtn else "",
        "Quote Item": r.selected_qtn.quote_item_number if r.selected_qtn else "",
        "QTN 가격": r.qtn_candidate_price,
        "QTN 시작일": r.selected_qtn.valid_start if r.selected_qtn else None,
        "QTN 종료일": r.selected_qtn.valid_end if r.selected_qtn else None,
        "QTN 적용 전 잔여수량": r.qtn_remaining_before, "QTN 사용수량": r.qtn_used_qty,
        "QTN 적용 후 잔여수량": r.qtn_remaining_after,
        "ASD 후보 수": r.asd_candidate_count, "선택 ASD 가격": r.asd_candidate_price,
        "ASD 시작일": r.selected_asd.valid_start if r.selected_asd else None,
        "ASD 종료일": r.selected_asd.valid_end if r.selected_asd else None,
        "최종 적용 유형": r.applied_price_type, "최종 적용 단가": r.applied_price,
        "Disti Purchase Cost": r.disti_cost, "원가 출처": r.disti_cost_source,
        "선택 근거": r.selection_reason, "적용 규칙 ID": r.rule_id,
        "처리 상태": r.processing_status, "예외 코드": ", ".join(r.exception_codes),
        "검토 필요 여부": "예" if r.review_required else "아니오",
    } for r in results]

    run_log = [{
        "Run ID": run_id, "실행일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "입력파일명": Path(args.rawdata).name, "파일 해시": raw_hash,
        "대상 기간": period, "규칙 버전": cfg.get("rule_version"),
        "처리 건수": len(results),
        "경고·오류 건수": len(excs),
        "프로그램 버전": cfg.get("program_version"),
        "승인 상태": "FINAL" if args.mode == "final" else "DRAFT",
    }]

    ym = f"{start:%Y%m}"
    out_path = out_dir / f"Microchip_POS_Result_{ym}_{run_id}.xlsx"
    write_result(out_path, {
        "POS_Report": pos_rows, "Processing_Result": proc_rows,
        "Exceptions": [e.as_row() for e in excs],
        "Customer_Match_Review": review_rows, "Validation_Summary": summary,
        "QTN_Usage_Ledger": allocator.ledger, "POS_Field_Mapping": mapping_table,
        "Address_Master_Needed": addr_needed, "Run_Log": run_log,
    }, gen.columns, draft=(args.mode != "final"))

    if addr_needed:
        tmpl = out_dir / f"customer_address_master_TEMPLATE_{run_id}.xlsx"
        write_address_master_template(tmpl, addr_needed)
        logger.info(f"주소 마스터 템플릿 생성: {tmpl.name} ({len(addr_needed)}개 고객)")

    if not wb.unchanged():
        logger.error("원본 파일이 실행 중 변경되었습니다!")
    logger.info(f"결과 파일: {out_path}")
    logger.info(f"결과 해시: {file_sha256(out_path)[:16]}…")
    for r in summary:
        logger.info(f"   {r['항목']}: {r['값']}")
    logger.info("=== 완료 ===")
    print(json.dumps({"run_id": run_id, "output": str(out_path),
                      "submit_ok": submit, "pos_rows": len(pos_rows),
                      "exceptions": len(excs)}, ensure_ascii=False))
    return 0 if submit else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Microchip POS Report 자동 생성")
    p.add_argument("--rawdata", required=True)
    p.add_argument("--template", default=None, help="기존 POS 템플릿(참고용, 읽지 않음)")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--output-dir", default=str(ROOT / "output"))
    p.add_argument("--log-dir", default=str(ROOT / "logs"))
    p.add_argument("--config", default=str(ROOT / "config" / "rules.yaml"))
    p.add_argument("--approved-mapping", default=None)
    p.add_argument("--address-master", default=None)
    p.add_argument("--crm-customers", default=None,
                   help="CRM '고객사' 내보내기 (우편번호·주소). 이름 정확일치로만 연결")
    p.add_argument("--mode", choices=["draft", "final"], default="draft")
    return p


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
