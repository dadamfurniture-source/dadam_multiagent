# 다담 SaaS — 내부 운영 시스템 설계

## 1. 전체 비즈니스 플로우

```
고객 접점                    내부 운영                         외부 연동
─────────                  ──────────                       ──────────
                      ┌─────────────────────────────────┐
사진 업로드 ──────────▶│         상담 (Consultation)       │
AI 시뮬레이션 ◀────────│  · 고객 요구사항 확인              │
견적 확인 ◀────────────│  · 현장 실측 일정 조율              │
                      │  · 견적 확정 / 계약                │
                      └──────────┬──────────────────────┘
                                 │ 계약 확정
                      ┌──────────▼──────────────────────┐
                      │         발주 (Ordering)           │
                      │  · 자재 발주 (매입)                │───▶ 자재 공급업체
                      │  · 공장 제작 의뢰                  │───▶ 제작 공장
                      │  · 발주서/PO 생성                  │
                      └──────────┬──────────────────────┘
                                 │ 발주 완료
                      ┌──────────▼──────────────────────┐
                      │         제작 (Manufacturing)      │
                      │  · 제작 진행률 추적                │◀──── 공장 상태 업데이트
                      │  · 품질 검수 (QC)                 │
                      │  · 완제품 입고                     │
                      └──────────┬──────────────────────┘
                                 │ 제작 완료
                      ┌──────────▼──────────────────────┐
                      │         설치 (Installation)       │
                      │  · 배송 일정 조율                  │───▶ 배송업체
설치 완료 알림 ◀────────│  · 설치 기사 배정                  │───▶ 설치팀
시공 사진 확인 ◀────────│  · 현장 시공                      │
                      │  · 설치 완료 검수                  │
                      └──────────┬──────────────────────┘
                                 │ 설치 완료
                      ┌──────────▼──────────────────────┐
A/S 접수 ─────────────▶│         A/S (After Service)      │
처리 결과 ◀────────────│  · A/S 접수 & 분류                │
                      │  · 기사 배정 & 일정                │───▶ A/S 기사
                      │  · 처리 완료 & 비용 정산            │
                      └─────────────────────────────────┘

        ┌───────────────────────────────────────────────────┐
        │              매출매입 (Accounting)                  │
        │                                                   │
        │  매출: 견적확정 → 계약금 → 중도금 → 잔금 → 수금완료   │
        │  매입: 자재발주 → 입고확인 → 대금지급 → 정산완료      │
        │  손익: 프로젝트별 매출 - 매입 - 인건비 = 영업이익     │
        └───────────────────────────────────────────────────┘
```

---

## 2. 운영 에이전트 조직 (확장)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CEO Agent (Orchestrator)                        │
│              고객 요청 + 내부 운영 전체 오케스트레이션                     │
└───────┬──────────────────┬──────────────────────┬─────────────────────┘
        │                  │                      │
  ┌─────▼─────┐    ┌──────▼──────┐     ┌─────────▼──────────┐
  │ 제품본부    │    │  운영본부     │     │    경영지원본부       │
  │ Product    │    │ Operations  │     │    Management      │
  │ Division   │    │ Division    │     │    Division        │
  └─────┬─────┘    └──────┬──────┘     └─────────┬──────────┘
        │                 │                       │
   기존 에이전트       ┌───┼────┬────┬────┐    ┌──┼──────┐
   Space Analyst      │   │    │    │    │    │  │      │
   Design Planner     ▼   ▼    ▼    ▼    ▼    ▼  ▼      ▼
   Image Generator  상담 발주  제작  설치  A/S  회계 일정   알림
   Quote Calculator Agent Agent Agent Agent Agent Agent Agent Agent
   Detail Designer
   BOM Generator
   QA Reviewer
   CAD Exporter
```

### 신규 에이전트 상세

| Agent | 역할 | 핵심 기능 | 모델 |
|-------|------|----------|------|
| **Consultation Agent** | 상담 관리 | 고객 문의 응대, 실측 일정 잡기, 견적→계약 전환 | Sonnet |
| **Ordering Agent** | 발주 관리 | 자재 발주서 생성, 공장 제작 의뢰, 재고 확인 | Sonnet |
| **Manufacturing Agent** | 제작 추적 | 제작 진행률 관리, 품질 검수, 납기일 추적 | Haiku |
| **Installation Agent** | 설치 조율 | 배송/설치 일정 조율, 기사 배정, 완료 검수 | Sonnet |
| **AfterService Agent** | A/S 관리 | A/S 접수, 원인 분류, 기사 배정, 비용 산정 | Sonnet |
| **Accounting Agent** | 매출매입 관리 | 매출 기록, 매입 기록, 수금/지급 추적, 손익 분석 | Opus |
| **Schedule Agent** | 일정 총괄 | 전체 프로젝트 타임라인, 리소스 충돌 감지, 알림 | Sonnet |
| **Notification Agent** | 알림 발송 | 고객/내부 알림 (카카오톡, SMS, 이메일, 슬랙) | Haiku |

---

## 3. 주문 생명주기 (Order Lifecycle)

### 3.1 상태 머신

```
[상담중] ──▶ [견적확정] ──▶ [계약완료] ──▶ [발주중] ──▶ [제작중]
                                                        │
[A/S완료] ◀── [A/S접수] ◀── [설치완료] ◀── [설치중] ◀── [제작완료]
                                 │
                                 ▼
                            [정산완료]
```

### 3.2 상태별 트리거 & 자동화

| 상태 변경 | 트리거 | 자동 액션 |
|-----------|--------|----------|
| 상담중 → 견적확정 | 고객 견적 승인 | 계약서 PDF 생성, 계약금 청구 |
| 견적확정 → 계약완료 | 계약금 입금 확인 | 매출 전표 생성, 발주 에이전트 가동 |
| 계약완료 → 발주중 | 자동 | 자재 발주서 생성, 공장에 제작 의뢰 |
| 발주중 → 제작중 | 공장 접수 확인 | 제작 일정 등록, 고객에게 알림 |
| 제작중 → 제작완료 | 공장 완료 보고 | 품질 검수, 배송 일정 조율 시작 |
| 제작완료 → 설치중 | 설치일 도래 | 설치 기사 배정, 고객에게 알림 |
| 설치중 → 설치완료 | 설치 완료 보고 | 시공 사진 업로드, 잔금 청구, A/S 보증 시작 |
| 설치완료 → 정산완료 | 잔금 입금 확인 | 매출 확정, 프로젝트 마감 |
| 설치완료 → A/S접수 | 고객 A/S 요청 | A/S 분류, 기사 배정 |

---

## 4. 매출매입 시스템

### 4.1 매출 (Revenue)

```
프로젝트 계약
    │
    ├── 계약금 (30%) ─── 계약 시점
    ├── 중도금 (40%) ─── 제작 완료 시점
    └── 잔금  (30%) ─── 설치 완료 시점
```

**매출 전표 구조:**
```json
{
  "id": "rev-001",
  "order_id": "ord-001",
  "type": "revenue",
  "category": "contract_deposit",     // contract_deposit | interim | balance
  "amount": 594000,                   // ₩594,000
  "tax_amount": 54000,                // 부가세 10%
  "status": "collected",              // pending | invoiced | collected | overdue
  "due_date": "2026-04-01",
  "collected_date": "2026-03-28",
  "payment_method": "bank_transfer",  // bank_transfer | card | cash
  "customer_id": "cust-001",
  "notes": "싱크대 2400mm 계약금"
}
```

### 4.2 매입 (Expense)

```
자재 발주 ───── 자재비 (PB, MDF, 상판, 하드웨어, 도어)
공장 의뢰 ───── 제작비 (가공 + 조립)
배송 의뢰 ───── 물류비
설치 의뢰 ───── 설치 인건비
기타 ────────── 철거비, 소모품, 교통비
```

**매입 전표 구조:**
```json
{
  "id": "exp-001",
  "order_id": "ord-001",
  "type": "expense",
  "category": "material",        // material | manufacturing | logistics | installation | misc
  "vendor_id": "ven-001",
  "amount": 380000,
  "tax_amount": 38000,
  "status": "paid",              // pending | approved | paid | overdue
  "due_date": "2026-04-15",
  "paid_date": "2026-04-10",
  "items": [
    {"name": "18T PB 측판", "qty": 4, "unit_price": 15000, "total": 60000},
    {"name": "인조대리석 상판", "qty": 1, "unit_price": 180000, "total": 180000}
  ],
  "po_number": "PO-2026-0042"
}
```

### 4.3 프로젝트별 손익

```json
{
  "order_id": "ord-001",
  "customer": "김다담",
  "category": "sink",
  "contract_amount": 1980000,
  "revenue": {
    "contract_deposit": {"amount": 594000, "status": "collected"},
    "interim":          {"amount": 792000, "status": "collected"},
    "balance":          {"amount": 594000, "status": "pending"}
  },
  "expenses": {
    "material":       380000,
    "manufacturing":  250000,
    "logistics":       50000,
    "installation":   200000,
    "misc":            30000
  },
  "total_revenue": 1980000,
  "total_expense":  910000,
  "gross_profit":  1070000,
  "margin_rate":      0.54
}
```

### 4.4 대시보드 지표

| 지표 | 설명 | 집계 |
|------|------|------|
| 월 매출 | 해당 월 수금 완료 금액 | 일/주/월/분기/연 |
| 월 매입 | 해당 월 지급 완료 금액 | 일/주/월/분기/연 |
| 미수금 | 청구 후 미입금 금액 | 실시간 |
| 미지급금 | 발주 후 미지급 금액 | 실시간 |
| 프로젝트 마진율 | (매출-매입)/매출 | 프로젝트별 |
| 평균 리드타임 | 계약→설치완료 평균 일수 | 카테고리별 |
| A/S 발생률 | A/S건수/설치건수 | 월별 |

---

## 5. 일정 조율 시스템

### 5.1 일정 유형

| 일정 유형 | 담당 | 연관 리소스 |
|-----------|------|------------|
| 실측 방문 | 상담사 | 상담사 캘린더 |
| 자재 입고 | 구매 | 자재 리드타임 |
| 제작 | 공장 | 공장 캘린더 + 생산 용량 |
| 배송 | 물류 | 배송 차량 |
| 설치 | 설치팀 | 설치 기사 캘린더 |
| A/S 방문 | A/S팀 | A/S 기사 캘린더 |

### 5.2 자동 일정 계산

```python
# 표준 리드타임 (영업일 기준)
LEAD_TIMES = {
    "measurement_visit":  2,   # 계약 후 실측까지
    "material_order":     1,   # 실측 후 자재 발주까지
    "material_delivery":  3,   # 자재 발주 후 입고까지
    "manufacturing":      7,   # 자재 입고 후 제작까지
    "quality_check":      1,   # 제작 완료 후 검수
    "delivery":           1,   # 검수 후 배송
    "installation":       1,   # 배송 후 설치
}
# 총 리드타임: 약 16영업일 (3~4주)
```

### 5.3 충돌 감지 & 알림

Schedule Agent가 자동 감지하는 상황:
- 설치 기사 **일정 겹침** (같은 시간 2건 배정)
- 공장 **생산 용량 초과** (주간 최대 처리량 도달)
- **납기일 위험** (제작 지연으로 설치일 영향)
- **자재 부족** (재고 없는 자재 발주 필요)
- **미수금 연체** (수금 예정일 초과)

---

## 6. 확장된 데이터 모델

```sql
-- ===== 기존 (제품본부) =====
-- users, projects, space_analyses, layouts, generated_images,
-- quotes, detail_designs, bom_lists, subscriptions

-- ===== 신규: 운영본부 =====

-- 주문 (견적 확정 → 최종 정산까지의 생명주기)
CREATE TABLE orders (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID REFERENCES projects(id),
  customer_id   UUID REFERENCES users(id),
  quote_id      UUID REFERENCES quotes(id),
  status        TEXT NOT NULL DEFAULT 'consulting',
  -- consulting | quoted | contracted | ordering | manufacturing
  -- manufactured | installing | installed | as_received | as_completed | settled
  contract_amount   BIGINT,          -- 계약 금액 (원)
  contract_date     TIMESTAMPTZ,
  estimated_install TIMESTAMPTZ,     -- 예상 설치일
  actual_install    TIMESTAMPTZ,     -- 실제 설치일
  assigned_installer UUID,           -- 설치 기사
  assigned_factory   UUID,           -- 제작 공장
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now()
);

-- 주문 상태 이력
CREATE TABLE order_status_history (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id   UUID REFERENCES orders(id),
  from_status TEXT,
  to_status   TEXT NOT NULL,
  changed_by  UUID,                 -- 변경자 (agent 또는 user)
  reason      TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ===== 일정 =====

CREATE TABLE schedules (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    UUID REFERENCES orders(id),
  type        TEXT NOT NULL,
  -- measurement | material_delivery | manufacturing_start | manufacturing_end
  -- quality_check | delivery | installation | as_visit
  title       TEXT NOT NULL,
  scheduled_at TIMESTAMPTZ NOT NULL,
  duration_min INT DEFAULT 60,
  assignee_id  UUID,                -- 담당자
  location     TEXT,                -- 현장 주소
  status       TEXT DEFAULT 'scheduled',  -- scheduled | confirmed | in_progress | completed | cancelled
  notes        TEXT,
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- 리소스 (설치기사, 공장, 차량 등)
CREATE TABLE resources (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type        TEXT NOT NULL,       -- installer | factory | vehicle | as_technician | consultant
  name        TEXT NOT NULL,
  capacity    INT DEFAULT 1,       -- 일일 처리 가능 건수
  phone       TEXT,
  email       TEXT,
  is_active   BOOLEAN DEFAULT true,
  metadata    JSONB,               -- 전문분야, 지역 등
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 리소스 가용 시간
CREATE TABLE resource_availability (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id UUID REFERENCES resources(id),
  date        DATE NOT NULL,
  is_available BOOLEAN DEFAULT true,
  booked_slots JSONB,              -- [{"start": "09:00", "end": "12:00", "order_id": "..."}]
  notes       TEXT
);

-- ===== 매출매입 =====

-- 거래처 (공급업체, 공장 등)
CREATE TABLE vendors (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  type        TEXT NOT NULL,       -- material_supplier | factory | logistics | installer
  contact     TEXT,
  phone       TEXT,
  email       TEXT,
  bank_info   JSONB,               -- 계좌정보
  payment_terms TEXT DEFAULT 'net30', -- 결제조건
  is_active   BOOLEAN DEFAULT true,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 매출 전표
CREATE TABLE revenue_entries (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    UUID REFERENCES orders(id),
  category    TEXT NOT NULL,       -- contract_deposit | interim | balance | as_fee
  amount      BIGINT NOT NULL,
  tax_amount  BIGINT DEFAULT 0,
  status      TEXT DEFAULT 'pending',  -- pending | invoiced | collected | overdue | cancelled
  due_date    DATE,
  collected_date DATE,
  payment_method TEXT,             -- bank_transfer | card | cash
  invoice_number TEXT,             -- 세금계산서 번호
  notes       TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 매입 전표
CREATE TABLE expense_entries (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    UUID REFERENCES orders(id),
  vendor_id   UUID REFERENCES vendors(id),
  category    TEXT NOT NULL,       -- material | manufacturing | logistics | installation | misc
  amount      BIGINT NOT NULL,
  tax_amount  BIGINT DEFAULT 0,
  status      TEXT DEFAULT 'pending',  -- pending | approved | paid | overdue | cancelled
  due_date    DATE,
  paid_date   DATE,
  po_number   TEXT,                -- 발주번호
  items_json  JSONB,               -- 상세 품목
  invoice_number TEXT,             -- 세금계산서 번호
  notes       TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 발주서
CREATE TABLE purchase_orders (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    UUID REFERENCES orders(id),
  vendor_id   UUID REFERENCES vendors(id),
  po_number   TEXT UNIQUE NOT NULL,
  type        TEXT NOT NULL,       -- material | manufacturing | logistics
  items_json  JSONB NOT NULL,      -- 발주 품목
  total_amount BIGINT,
  status      TEXT DEFAULT 'draft', -- draft | sent | confirmed | partially_received | received | cancelled
  sent_at     TIMESTAMPTZ,
  expected_delivery DATE,
  actual_delivery DATE,
  notes       TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ===== A/S =====

CREATE TABLE after_service_tickets (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    UUID REFERENCES orders(id),
  customer_id UUID REFERENCES users(id),
  type        TEXT NOT NULL,       -- defect | damage | adjustment | add_on
  priority    TEXT DEFAULT 'normal', -- low | normal | high | urgent
  description TEXT NOT NULL,
  photos      TEXT[],              -- 사진 URL 배열
  status      TEXT DEFAULT 'received', -- received | assigned | in_progress | resolved | closed
  assigned_to UUID,                -- A/S 기사
  resolution  TEXT,                -- 처리 내용
  cost        BIGINT DEFAULT 0,    -- A/S 비용 (보증기간 내 0)
  is_warranty BOOLEAN DEFAULT true,
  warranty_expires DATE,
  scheduled_at TIMESTAMPTZ,
  resolved_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- ===== 알림 =====

CREATE TABLE notifications (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recipient_id UUID,               -- 수신자 (user 또는 resource)
  recipient_type TEXT NOT NULL,    -- customer | staff | vendor
  channel     TEXT NOT NULL,       -- kakao | sms | email | slack | in_app
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  related_order UUID,
  status      TEXT DEFAULT 'pending', -- pending | sent | failed | read
  sent_at     TIMESTAMPTZ,
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ===== 인덱스 =====

CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_schedules_date ON schedules(scheduled_at);
CREATE INDEX idx_schedules_assignee ON schedules(assignee_id);
CREATE INDEX idx_revenue_status ON revenue_entries(status);
CREATE INDEX idx_revenue_order ON revenue_entries(order_id);
CREATE INDEX idx_expense_status ON expense_entries(status);
CREATE INDEX idx_expense_order ON expense_entries(order_id);
CREATE INDEX idx_as_tickets_status ON after_service_tickets(status);
CREATE INDEX idx_notifications_recipient ON notifications(recipient_id, status);
```

---

## 7. 운영 에이전트 상호작용 흐름

### 7.1 계약 → 설치완료 자동화 시나리오

```
1. 고객이 견적 승인 (UI 클릭)
   └─▶ CEO Agent 감지

2. CEO → Consultation Agent
   └─▶ "계약서 생성 + 계약금 청구"
   └─▶ Notification Agent → 고객에게 카카오톡 계약금 안내

3. 계약금 입금 확인 (웹훅)
   └─▶ CEO → Accounting Agent "매출 전표 생성 (계약금)"
   └─▶ CEO → Ordering Agent "자재 발주 + 공장 의뢰"

4. Ordering Agent
   ├─▶ pricing_tools.get_materials() → BOM 기반 자재 리스트
   ├─▶ "PO 생성 + 공급업체에 발주"
   └─▶ Schedule Agent "자재 입고 D+3, 제작 D+10 일정 등록"

5. 공장 제작 완료 보고
   └─▶ CEO → Manufacturing Agent "품질 검수"
   └─▶ CEO → Accounting Agent "매입 전표 생성 (제작비)"
   └─▶ CEO → Schedule Agent "설치 일정 확정"
   └─▶ Notification Agent → 고객에게 "설치 예정일 안내"

6. 설치 완료
   └─▶ CEO → Installation Agent "완료 검수 + 시공 사진"
   └─▶ CEO → Accounting Agent "잔금 청구"
   └─▶ Notification Agent → 고객에게 "잔금 안내 + 보증 시작"

7. 잔금 입금
   └─▶ CEO → Accounting Agent "매출 확정 + 프로젝트 손익 산출"
```

### 7.2 A/S 자동화 시나리오

```
1. 고객 A/S 접수 (앱/전화)
   └─▶ CEO → AfterService Agent

2. AfterService Agent
   ├─▶ 사진 분석 (Claude Vision) → 원인 분류
   ├─▶ 보증기간 확인
   ├─▶ Schedule Agent → A/S 기사 배정 + 일정
   └─▶ Notification Agent → 고객에게 "방문 일정 안내"

3. A/S 완료
   ├─▶ 유상 A/S → Accounting Agent "A/S 매출 전표"
   └─▶ 무상 A/S → Accounting Agent "A/S 비용 처리"
```
