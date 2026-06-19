# 재고분석 — 활동성 등급(A~E) · 재고계수 · 유동/비유동 설계

작성일: 2026-06-10
대상: `microchip-matching` 4실 재고분석 (`/api/inventory-analysis`, `InventoryAnalysis.js`)
범위: **로컬(`main.py`) 전용.** prod(`main_aws.py`) 이식은 별도 작업.

## 목적
재고를 "잘 굴러가는 자재(유동) vs 안 굴러가는 자재(비유동)"로 세분화해서,
등급별로 **돈이 얼마 묶여 있는지**를 보고 악성재고를 골라낸다.
기존 판매 파레토 ABC는 유지하고, **재고계수 기반 활동등급 A~E**를 새로 추가한다.

## 핵심 지표

| 지표 | 정의 |
|---|---|
| `monthly_avg` | 최근 6개월 월평균 판매수량 (기존) |
| `stock_coef` (재고계수) | `현재고 ÷ monthly_avg` = "몇 개월치 재고냐" = 소진속도. `monthly_avg=0`이면 `null`(∞) |
| `months_with_sales` | 최근 6개월 중 판매(qty>0)가 있던 달 수 (0~6) — 신규 |
| `total_6mo` | 최근 6개월 총판매 (기존 `recent_total`) |
| `stock_value` | 현재고 × 매입가 (행별 합산, 이미 구현됨) |

## 활동등급 (활동등급, A~E) — 규칙 기반·결정적

판정 순서:

1. `total_6mo == 0` (6개월 무판매) → **E** (비유동, 규칙 확정, AI 불필요)
2. 그 외 `stock_coef` 계산:
   - `stock_coef > 100` → **E** (비유동)
   - `stock_coef ≤ 6` → **A**
   - `6 < stock_coef ≤ 10` → **B**
   - `10 < stock_coef ≤ 15` → **C**
   - `15 < stock_coef ≤ 100` → **D**
   - `stock == 0`(판매는 있음) → `stock_coef = 0` → **A** (재고 없음·잘 나감, 재고금액 0)

**유동재고 = A~D, 비유동재고 = E.**

검산: KEC가 30개/월 예상으로 100개 사놓음 → 계수 3.3 → A(유동). 안 팔려 평균 5개/월로 떨어지면 계수 20 → D. 0이 되면 E.

## 저판매 애매건 → AI 판정 (E 후보만)

- **AI 후보 조건**: `total_6mo > 0` 이고 `months_with_sales ≤ 1` (6개월 중 한 달만 팔린 들쭉날쭉 건). `ai_candidate = true` 플래그.
- 업로드 분석 시에는 AI 호출 안 함 → 규칙 등급 그대로 표시 + `ai_candidate` 표시.
- 사용자가 **"AI 판정" 버튼**을 누르면 후보들만 모아 Claude에 보내 **유동 vs 비유동(E)** 판정 + 한 줄 사유(국문)를 받아 해당 행 등급/사유 갱신.
- **API 키 없거나 실패** → graceful: 후보 전부 등급 유지하되 `ai_reason = "규칙판정(저판매)"` 로 표기, 에러 메시지 반환.
- 모델: 분류 작업이라 저렴·빠른 모델 사용(구현 시 `claude-api` 스킬로 모델 id/SDK 사용법 확인). 후보 전체를 1회 호출에 배치 → 구조화 JSON 응답 파싱.

## API

### `POST /api/inventory-analysis` (확장)
응답 `items[]` 항목에 추가:
- `stock_coef` (number|null)
- `activity_grade` ("A"|"B"|"C"|"D"|"E")
- `liquidity` ("유동"|"비유동")
- `months_with_sales` (int)
- `ai_candidate` (bool)
- `ai_reason` (string|null, 초기 null)

응답 `summary`에 추가:
- `grade_rollup`: `{ "A": {count, value}, ... "E": {count, value} }`
- `liquid_value` (A~D `stock_value` 합), `nonliquid_value` (E 합)
- (기존 `has_price`, `total_stock_value` 유지)

기존 필드(`abc`, `recommended`, `monthly[]` 등) 전부 유지.

### `POST /api/inventory-analysis/ai-classify` (신규)
- body: `{ items: [{pn, stock, monthly_avg, months_with_sales, monthly, stock_coef}] }` (ai_candidate 행만)
- 응답: `{ results: [{pn, liquidity, grade, reason}], error? }`
- 키 없으면 `{ error, results: [규칙 fallback] }`.

### `POST /api/inventory-analysis/export` (확장)
- 컬럼 추가: 재고계수 · 활동등급 · 유동/비유동 · AI사유.
- **요약 시트** 추가: 등급별 부품수·재고금액·비중% + 유동(A~D) vs 비유동(E) 합계.

## 프론트 (`InventoryAnalysis.js`)
- 표 컬럼: P/N · 현재고 · 매입가 · 재고금액 · 월평균 · **재고계수** · **활동등급(A~E)** · **유동/비유동** · **AI사유**. 기존 **ABC(판매)** 컬럼 유지.
- 정렬 토글 추가: **활동등급 / 재고계수 / 파트명** (기존 정렬 유지).
- 필터칩 추가: 유동 / 비유동 / 활동등급 A~E (기존 ABC 필터 유지).
- **등급별 금액 요약** 섹션 신규: 등급별 부품수·재고금액·비중% 표 + 유동 vs 비유동 합계 카드.
- **"AI 판정" 버튼**: `ai_candidate` 있을 때 활성. 클릭 → `/ai-classify` → 해당 행 갱신.
- **기존 월별 판매 sparkline 그래프 유지.**

## 엣지 케이스
- `monthly_avg=0` → `stock_coef=null`, 등급 E (1번 규칙).
- `stock=0` & 판매 있음 → 계수 0 → A, 재고금액 0.
- 매입가 컬럼 없는 파일 → `stock_value=0`, 금액 롤업 0, `has_price=false` (기존 처리). 등급/계수는 정상 동작.
- 같은 P/N 여러 행(매입가 다름) → 재고·재고금액 행별 누적(기존). 계수는 합산 재고 ÷ 평균판매.

## 범위
- **포함**: `main.py` 3개 엔드포인트, `InventoryAnalysis.js`, 프론트 재빌드→`backend/static`.
- **제외(YAGNI)**: prod(`main_aws.py`) 이식(별도), 판매빈도 복합등급(재고계수만으로 결정됨), 전체 부품 AI 재검토.
