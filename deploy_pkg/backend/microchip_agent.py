"""마이크로칩 매칭 — 하이브리드 AI Agent.

흐름:
  1단계 (결정론, 무료): 정확 믹스# 매칭 → 95% 처리
  2단계 (rapidfuzz, 도구로 캡슐화): 매칭 실패 행에 대해 백록 후보 좁힘
  3단계 (Claude tool-use Agent): top 후보 중 최종 판단 + 추론 사유
"""
from __future__ import annotations
import io
import json
import os
import re
from typing import Any

import anthropic
import pandas as pd
from openpyxl import load_workbook
from rapidfuzz import fuzz, process


def _norm(v):
    return "" if v is None else str(v).strip()


def _norm_code(v):
    """믹스#/Part# 정규화: 영숫자만 + 대문자."""
    return re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()


def _norm_name(v):
    """고객명 정규화: 공백 제거 + 괄호제거 + 대문자."""
    s = str(v or "")
    s = re.sub(r"\([^)]*\)", "", s)  # 괄호 안 제거
    s = re.sub(r"[\s（）()]", "", s)
    return s.upper()


# ========== 데이터 추출 (마이크로칩 엑셀 → 정규화된 인덱스) ==========
def parse_match_sources(file_bytes: bytes) -> dict:
    """업로드된 엑셀에서 출고내역/백록/FAB2 추출 + 인덱스 빌드."""
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    shipment_sheet = backlog_sheet = None
    for name in xls.sheet_names:
        if name == "출고내역":
            shipment_sheet = name
        elif name.startswith("백록") and "피벗" not in name:
            backlog_sheet = name

    ship_rows = []
    if shipment_sheet:
        df = pd.read_excel(xls, sheet_name=shipment_sheet, header=0)
        for _, row in df.iterrows():
            mix = _norm(row.get("믹스#"))
            if not mix:
                continue
            ship_rows.append({
                "mix": mix,
                "mix_norm": _norm_code(mix),
                "customer": _norm(row.get("고객")),
                "customer_norm": _norm_name(row.get("고객")),
                "end": _norm(row.get("END고객사명")),
                "purchasing": _norm(row.get("PURCHSING")),
                "part": _norm(row.get("품번")),
                "part_norm": _norm_code(row.get("품번")),
                "code": _norm(row.get("고객코드")),
            })

    bl_rows = []
    if backlog_sheet:
        df = pd.read_excel(xls, sheet_name=backlog_sheet, header=0)
        for _, row in df.iterrows():
            mix = _norm(row.get("믹스"))
            if not mix:
                continue
            bl_rows.append({
                "mix": mix,
                "mix_norm": _norm_code(mix),
                "customer": _norm(row.get("업체명")),
                "customer_norm": _norm_name(row.get("업체명")),
                "end": _norm(row.get("End Customer Name")),
                "purchasing": _norm(row.get("ODM/SubCon Name")),
                "part": _norm(row.get("Customer Part Number")),
                "part_norm": _norm_code(row.get("Customer Part Number")),
                "code": _norm(row.get("업체코드")),
            })
    return {
        "ship": ship_rows, "backlog": bl_rows,
        "ship_sheet": shipment_sheet, "bl_sheet": backlog_sheet,
    }


# ========== 1단계: 정확 매칭 ==========
def exact_match(sources: dict) -> dict:
    """정확 mix_norm 매칭. matched + unmatched 분리."""
    bl_by_mix = {}
    for b in sources["backlog"]:
        bl_by_mix.setdefault(b["mix_norm"], []).append(b)

    matched_pairs = []
    unmatched_ship = []
    matched_bl_norms = set()

    for s in sources["ship"]:
        bls = bl_by_mix.get(s["mix_norm"])
        if bls:
            matched_pairs.append({"ship": s, "backlog": bls[0], "method": "exact"})
            matched_bl_norms.add(s["mix_norm"])
        else:
            unmatched_ship.append(s)

    unmatched_bl = [b for b in sources["backlog"] if b["mix_norm"] not in matched_bl_norms]

    return {
        "matched": matched_pairs,
        "unmatched_ship": unmatched_ship,
        "unmatched_bl": unmatched_bl,
    }


# ========== 2단계: rapidfuzz로 후보 좁히기 (도구) ==========
def search_backlog_tool(bl_rows: list, query: dict, top_n: int = 5) -> list:
    """rapidfuzz로 백록에서 유사 후보 검색. AI Agent가 호출하는 도구.
    query: {mix, customer, part} — 비어있으면 무시
    """
    candidates = []
    mix_q = _norm_code(query.get("mix", ""))
    cust_q = _norm_name(query.get("customer", ""))
    part_q = _norm_code(query.get("part", ""))

    for b in bl_rows:
        score = 0
        if mix_q:
            score += fuzz.ratio(mix_q, b["mix_norm"]) * 0.5  # 믹스가 가장 중요
        if cust_q:
            score += fuzz.token_set_ratio(cust_q, b["customer_norm"]) * 0.3
        if part_q:
            score += fuzz.ratio(part_q, b["part_norm"]) * 0.2
        candidates.append((score, b))

    candidates.sort(key=lambda x: -x[0])
    out = []
    for sc, b in candidates[:top_n]:
        out.append({
            "score": round(sc, 1),
            "mix": b["mix"], "customer": b["customer"], "part": b["part"],
            "end": b["end"], "purchasing": b["purchasing"], "code": b["code"],
        })
    return out


# ========== 3단계: AI Agent (tool use loop) ==========
def run_agent_for_row(client, ship_row: dict, bl_rows: list, agent_log: list = None) -> dict:
    """단일 unmatched 출고내역 행에 대해 백록 매칭 후보 AI 판단."""
    if agent_log is None:
        agent_log = []

    tools = [
        {
            "name": "search_backlog",
            "description": "백록에서 유사 후보 검색. mix/customer/part 중 채워진 필드로 fuzzy 검색해 top 5 후보 반환. score는 0~100.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "mix": {"type": "string", "description": "믹스# 검색어 (예: M3203-001097)"},
                    "customer": {"type": "string", "description": "고객명 검색어"},
                    "part": {"type": "string", "description": "Part# 검색어"},
                },
            },
        },
        {
            "name": "submit_match",
            "description": "최종 매칭 결정 제출. confidence는 0~100 (95+이면 자동 적용 권장, 70~95이면 사람 확인, 70 미만은 매칭 없음 처리).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "matched_mix": {"type": "string", "description": "매칭된 백록 믹스. 매칭 없으면 빈 문자열"},
                    "confidence": {"type": "integer"},
                    "reason": {"type": "string", "description": "한국어로 1~2문장 사유"},
                },
                "required": ["matched_mix", "confidence", "reason"],
            },
        },
    ]

    system = """당신은 마이크로칩 출고내역과 백록을 매칭하는 AI Agent입니다.
정확 매칭이 실패한 출고내역 행을 받아 백록에서 가장 가능성 높은 행을 찾으세요.

절차:
1. search_backlog로 후보 검색 (믹스#가 있으면 그것 위주, 부족하면 customer/part로 추가 검색)
2. 후보 점수와 필드 일치도 분석:
   - 믹스#가 표기 차이뿐이면 (하이픈/공백 차이) 강한 신호
   - 고객명이 같은 회사 다른 표기 (예: SJI ≈ 에스제이아이)면 보강 신호
   - Part#가 표준화 차이뿐이면 (MT 접두사 등) 추가 보강
3. submit_match로 결과 제출

confidence 가이드:
- 95+: 표기 차이만 (하이픈/대소문자/공백 차이) — 명백히 같은 entity
- 80~95: 의미적으로 같지만 표기 차이 큼 (회사명 다른 표기 등)
- 50~80: 부분 일치, 사람 확인 필요
- 50 미만: matched_mix=""로 매칭 없음 처리

호출 횟수는 최소화하세요 (최대 3~4회)."""

    user_msg = f"""다음 출고내역 행에 대해 백록 매칭을 찾아주세요:
- 믹스#: {ship_row.get('mix')}
- 고객: {ship_row.get('customer')}
- END: {ship_row.get('end')}
- PURCHASING: {ship_row.get('purchasing')}
- Part#: {ship_row.get('part')}
"""
    messages = [{"role": "user", "content": user_msg}]
    final = None

    for step in range(6):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                system=system,
                tools=tools,
                messages=messages,
            )
        except Exception as e:
            agent_log.append({"step": step, "error": str(e)})
            return {"error": str(e), "matched_mix": "", "confidence": 0, "reason": f"API 오류: {e}"}

        tool_results = []
        assistant_blocks = list(resp.content)
        for blk in resp.content:
            if blk.type != "tool_use":
                continue
            args = blk.input
            if blk.name == "search_backlog":
                results = search_backlog_tool(bl_rows, args)
                agent_log.append({"step": step, "tool": "search_backlog", "args": args, "results_count": len(results)})
                tool_results.append({
                    "type": "tool_result", "tool_use_id": blk.id,
                    "content": json.dumps(results, ensure_ascii=False),
                })
            elif blk.name == "submit_match":
                final = {
                    "matched_mix": args.get("matched_mix", ""),
                    "confidence": int(args.get("confidence", 0)),
                    "reason": args.get("reason", ""),
                }
                agent_log.append({"step": step, "tool": "submit_match", "result": final})
                tool_results.append({
                    "type": "tool_result", "tool_use_id": blk.id,
                    "content": json.dumps({"ok": True}, ensure_ascii=False),
                })

        if final is not None:
            return final
        if resp.stop_reason == "end_turn":
            break
        messages.append({"role": "assistant", "content": assistant_blocks})
        messages.append({"role": "user", "content": tool_results})

    return {"matched_mix": "", "confidence": 0, "reason": "Agent가 매칭을 결정하지 못함"}


# ========== 메인 진입점 ==========
def hybrid_match(file_bytes: bytes, max_ai_rows: int = 30) -> dict:
    """전체 흐름. AI는 unmatched 행 중 최대 max_ai_rows건만 처리 (비용/시간 보호)."""
    import time as _t
    t0 = _t.time()

    sources = parse_match_sources(file_bytes)
    t1 = _t.time()

    stage1 = exact_match(sources)
    t2 = _t.time()

    # 2~3단계: AI Agent
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        except Exception:
            pass

    ai_results = []
    if api_key and stage1["unmatched_ship"]:
        client = anthropic.Anthropic(api_key=api_key)
        target_rows = stage1["unmatched_ship"][:max_ai_rows]
        for s in target_rows:
            log = []
            result = run_agent_for_row(client, s, sources["backlog"], agent_log=log)
            ai_results.append({
                "ship_row": {
                    "mix": s["mix"], "customer": s["customer"], "part": s["part"],
                    "end": s["end"], "purchasing": s["purchasing"],
                },
                "ai": result,
                "agent_log": log,
            })
    t3 = _t.time()

    return {
        "stats": {
            "shipment_total": len(sources["ship"]),
            "backlog_total": len(sources["backlog"]),
            "exact_matched": len(stage1["matched"]),
            "unmatched_ship": len(stage1["unmatched_ship"]),
            "unmatched_bl": len(stage1["unmatched_bl"]),
            "ai_processed": len(ai_results),
            "ai_skipped": max(0, len(stage1["unmatched_ship"]) - len(ai_results)),
        },
        "ai_results": ai_results,
        "timings": {
            "parse": round(t1 - t0, 2),
            "exact": round(t2 - t1, 2),
            "ai_agent": round(t3 - t2, 2),
        },
    }
