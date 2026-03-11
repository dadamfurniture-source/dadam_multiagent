# 다담 SaaS 플랫폼 - 멀티 에이전트 아키텍처 설계

## 1. 서비스 개요

**다담 SaaS** — 주문제작 가구 AI 시뮬레이션 & 견적 플랫폼

고객이 현장 사진을 업로드하면:
1. AI가 공간을 분석하고
2. 선택한 가구가 설치된 시뮬레이션 이미지를 생성하고
3. 자동 견적을 산출하며
4. (유료) 제작용 상세 설계도를 생성한다

### 대상 품목
싱크대, 아일랜드, 붙박이장, 냉장고장, 신발장, 화장대, 수납장, 창고장

### 대상 사용자
| 등급 | 사용자 | 기능 |
|------|--------|------|
| Free | 일반 고객 | 사진 업로드 → 1회 시뮬레이션 이미지 + 기본 견적 |
| Basic (₩9,900/월) | 고객/소규모 업체 | 무제한 시뮬레이션 + 다중 스타일 + 상세 견적 |
| Pro (₩49,000/월) | 인테리어 업체 | + 디테일 설계도 + BOM + 고객 관리 |
| Enterprise (₩199,000/월) | 가구 공장 | + CAD 연동 + API 접근 + 브랜드 커스텀 |

---

## 2. 멀티 에이전트 조직도

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          CEO Agent (Orchestrator)                        │
│                고객 요청 + 내부 운영 전체 오케스트레이션                     │
└───────┬──────────────────────┬──────────────────────┬───────────────────┘
        │                      │                      │
  ┌─────▼──────────┐   ┌──────▼──────────┐   ┌───────▼────────────┐
  │  제품본부        │   │  운영본부         │   │  경영지원본부        │
  │  Product Div.   │   │  Operations Div. │   │  Management Div.  │
  │  (AI 시뮬레이션) │   │  (주문 생명주기)   │   │  (매출매입/일정)    │
  └─────┬──────────┘   └──────┬──────────┘   └───────┬────────────┘
        │                     │                       │
  ┌──┬──┼──┬──┐       ┌──┬───┼──┬──┬──┐        ┌──┬──┼──┐
  │  │  │  │  │       │  │   │  │  │  │        │  │  │  │
  ▼  ▼  ▼  ▼  ▼       ▼  ▼   ▼  ▼  ▼  ▼        ▼  ▼  ▼  ▼
 Sp De Im Qu  QA    상담 발주 제작 설치 A/S     회계 일정 알림
 ac si ag ot  Re    Con Ord Mfg Ins  AS      Acc Sch Ntf
 e  gn e  e   vi    slt  er  g  tl         tng dle  fy
    er     ew    ati
 +Detail +BOM +CAD
```

### 에이전트 상세

| Agent | 역할 | 입력 | 출력 | 모델 |
|-------|------|------|------|------|
| **CEO (Orchestrator)** | 요청 분류, 워크플로우 오케스트레이션 | 사용자 요청 | 최종 결과 | Claude Opus |
| **Space Analyst** | 공간 사진 분석 (벽, 배관, 치수 추정) | 사진 | 공간 JSON (벽 길이, 배관 위치, 장애물) | Claude Opus |
| **Design Planner** | 가구 배치 계획, 모듈 구성 | 공간 JSON + 품목 | 배치 레이아웃 JSON | Claude Sonnet |
| **Image Generator** | 시뮬레이션 이미지 생성 | 배치 JSON + 원본 사진 | 설치 시뮬레이션 이미지 | Gemini + Flux LoRA |
| **Quote Calculator** | 견적 산출 (자재+인건비+마진) | 배치 JSON + 가격DB | 견적서 JSON | Claude Haiku |
| **Detail Designer** | 상세 제작 설계도 (Pro+) | 배치 JSON | 상세 치수도 + 조립도 | Claude Opus |
| **BOM Generator** | 자재 명세서 (Pro+) | 상세 설계 | BOM 리스트 | Claude Sonnet |
| **CAD Exporter** | CAD 파일 변환 (Enterprise) | 상세 설계 | DXF/SVG 파일 | 전용 라이브러리 |
| **QA Reviewer** | 설계 검증, 시공 가능성 체크 | 전체 결과 | 검증 리포트 | Claude Opus |

---

## 3. 처리 파이프라인

### 3.1 기본 파이프라인 (Free/Basic)

```
사진 업로드
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Space Analyst │ ──▶ │Design Planner│ ──▶ │Image Generator│ ──▶ │Quote Calcul. │
│  (공간 분석)   │     │ (배치 계획)    │     │ (이미지 생성)  │     │  (견적 산출)   │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                                                                       │
                                                                       ▼
                                                              최종 결과 (이미지 + 견적)
```

### 3.2 Pro 파이프라인 (인테리어 업체)

```
기본 파이프라인 결과
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│Detail Designer│ ──▶ │BOM Generator │ ──▶ │ QA Reviewer  │
│ (상세 설계)    │     │ (자재 명세)    │     │ (설계 검증)   │
└──────────────┘     └──────────────┘     └──────────────┘
                                                  │
                                                  ▼
                                    상세 설계도 + BOM + 검증 리포트
```

### 3.3 Enterprise 파이프라인

```
Pro 파이프라인 결과
    │
    ▼
┌──────────────┐
│ CAD Exporter │ ──▶ DXF/SVG + API 응답
│ (CAD 변환)    │
└──────────────┘
```

---

## 4. 기술 스택

### Backend (Python)
- **Claude Agent SDK** — 멀티 에이전트 오케스트레이션
- **FastAPI** — REST API + WebSocket (실시간 진행 상황)
- **Celery + Redis** — 비동기 작업 큐 (이미지 생성 등 긴 작업)
- **PostgreSQL** (Supabase) — 메인 DB
- **Supabase Auth** — 인증/인가
- **Supabase Storage** — 이미지 저장
- **Stripe** — 결제/구독 관리

### AI/ML
- **Claude API** — 공간 분석, 설계, 견적, 검증
- **Gemini API** — 이미지 생성 (Cleanup, Correction, Open)
- **Flux LoRA** (Replicate) — 가구 이미지 생성
- **Claude Vision** — 사진 분석

### Frontend
- **Next.js 15** (App Router) — 웹 프론트엔드
- **Tailwind CSS + shadcn/ui** — UI
- **React Query** — 서버 상태 관리
- **Zustand** — 클라이언트 상태

### Infrastructure
- **Vercel** — 프론트엔드 배포
- **Railway / Fly.io** — 백엔드 배포
- **Supabase** — DB + Auth + Storage
- **Upstash Redis** — 캐시 + 작업 큐

---

## 5. 데이터 모델 (핵심)

```sql
-- 사용자
users (id, email, name, plan, company_name, company_type)

-- 프로젝트 (하나의 시뮬레이션 요청)
projects (id, user_id, name, status, category, created_at)

-- 공간 분석 결과
space_analyses (id, project_id, original_image_url, analysis_json, wall_data)

-- 가구 배치
layouts (id, project_id, space_analysis_id, layout_json, modules)

-- 생성된 이미지
generated_images (id, project_id, layout_id, image_url, type, style)

-- 견적
quotes (id, project_id, layout_id, items_json, total_price, margin_rate)

-- 상세 설계 (Pro+)
detail_designs (id, project_id, layout_id, design_json, drawings_url)

-- BOM (Pro+)
bom_lists (id, detail_design_id, items_json, total_material_cost)

-- 구독/결제
subscriptions (id, user_id, plan, stripe_subscription_id, status)
```

---

## 6. 프로젝트 구조

```
dadam-saas/
├── agents/                    # 멀티 에이전트 시스템
│   ├── __init__.py
│   ├── orchestrator.py        # CEO Agent
│   ├── space_analyst.py       # 공간 분석 에이전트
│   ├── design_planner.py      # 배치 계획 에이전트
│   ├── image_generator.py     # 이미지 생성 에이전트
│   ├── quote_calculator.py    # 견적 에이전트
│   ├── detail_designer.py     # 상세 설계 에이전트 (Pro+)
│   ├── bom_generator.py       # BOM 에이전트 (Pro+)
│   ├── cad_exporter.py        # CAD 에이전트 (Enterprise)
│   ├── qa_reviewer.py         # QA 에이전트
│   └── tools/                 # 커스텀 MCP 도구
│       ├── supabase_tools.py  # DB/Storage 도구
│       ├── image_tools.py     # Gemini/Flux 도구
│       ├── pricing_tools.py   # 가격 조회 도구
│       └── cad_tools.py       # CAD 변환 도구
├── api/                       # FastAPI 백엔드
│   ├── main.py
│   ├── routes/
│   ├── middleware/
│   └── schemas/
├── web/                       # Next.js 프론트엔드
│   ├── app/
│   ├── components/
│   └── lib/
├── workers/                   # Celery 워커
│   ├── tasks.py
│   └── celery_config.py
├── shared/                    # 공유 유틸리티
│   ├── models.py
│   ├── config.py
│   └── constants.py
├── tests/
├── docs/
├── pyproject.toml
└── docker-compose.yml
```

---

## 7. 구현 로드맵

### Phase 1: Foundation (2주)
- [ ] 프로젝트 셋업 (Python + FastAPI + Next.js)
- [ ] Supabase 스키마 구축
- [ ] 인증 시스템 (Supabase Auth)
- [ ] 기본 에이전트 프레임워크 구축

### Phase 2: Core Pipeline (3주)
- [ ] Space Analyst Agent (기존 wall analysis 이식)
- [ ] Design Planner Agent (기존 computeLayout 이식)
- [ ] Image Generator Agent (기존 Gemini+Flux 이식)
- [ ] Quote Calculator Agent (가격 DB + 견적 로직)
- [ ] CEO Orchestrator (파이프라인 통합)

### Phase 3: Customer UX (2주)
- [ ] 사진 업로드 UI
- [ ] 실시간 진행 상황 (WebSocket)
- [ ] 결과 뷰어 (이미지 + 견적)
- [ ] 프로젝트 관리 대시보드

### Phase 4: Operations - 상담/발주/제작/설치/A·S (3주)
- [ ] Order 생명주기 상태 머신
- [ ] Consultation Agent (상담 → 계약)
- [ ] Ordering Agent (자재 발주 + 공장 의뢰)
- [ ] Manufacturing Agent (제작 추적 + 품질 검수)
- [ ] Installation Agent (배송/설치 조율)
- [ ] AfterService Agent (A/S 접수 → 처리)

### Phase 5: Operations - 매출매입/일정 (2주)
- [ ] Accounting Agent (매출 전표/매입 전표/손익)
- [ ] Schedule Agent (일정 총괄 + 충돌 감지)
- [ ] Notification Agent (카카오/SMS/이메일/슬랙)
- [ ] 이벤트 기반 자동 라우팅

### Phase 6: B2B Features (3주)
- [ ] Detail Designer Agent
- [ ] BOM Generator Agent
- [ ] QA Reviewer Agent
- [ ] 업체용 대시보드
- [ ] 운영 대시보드 (매출/매입/일정/A·S)

### Phase 7: Monetization (2주)
- [ ] Stripe 구독 시스템
- [ ] 요금제별 기능 게이팅
- [ ] 사용량 추적 & 제한

### Phase 8: Enterprise (2주)
- [ ] CAD Exporter Agent
- [ ] API 키 발급 & 관리
- [ ] 브랜드 커스터마이징

---

## 8. Agent SDK 구현 패턴

### Orchestrator (CEO Agent) 핵심 코드 구조

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

async def process_project(project_request):
    """메인 오케스트레이터 - 전체 파이프라인 관리"""

    async for message in query(
        prompt=f"""
        고객 요청을 처리하세요:
        - 품목: {project_request.category}
        - 스타일: {project_request.style}
        - 예산: {project_request.budget}
        - 사진: {project_request.image_url}

        순서:
        1. space-analyst로 공간 분석
        2. design-planner로 배치 계획
        3. image-generator로 시뮬레이션 이미지 생성
        4. quote-calculator로 견적 산출
        5. 결과 통합하여 반환
        """,
        options=ClaudeAgentOptions(
            allowed_tools=["Agent", "mcp__supabase__*"],
            agents={
                "space-analyst": AgentDefinition(
                    description="공간 사진을 분석하여 벽면, 배관, 치수를 추출",
                    prompt=SPACE_ANALYST_PROMPT,
                    tools=["mcp__supabase__read", "mcp__vision__analyze"],
                    model="opus",
                ),
                "design-planner": AgentDefinition(
                    description="공간 분석 결과를 기반으로 가구 모듈 배치 계획",
                    prompt=DESIGN_PLANNER_PROMPT,
                    tools=["mcp__supabase__read", "mcp__pricing__lookup"],
                    model="sonnet",
                ),
                "image-generator": AgentDefinition(
                    description="배치 계획을 기반으로 시뮬레이션 이미지 생성",
                    prompt=IMAGE_GENERATOR_PROMPT,
                    tools=["mcp__image__generate", "mcp__supabase__upload"],
                    model="sonnet",
                ),
                "quote-calculator": AgentDefinition(
                    description="배치 계획과 자재 정보로 견적 산출",
                    prompt=QUOTE_CALCULATOR_PROMPT,
                    tools=["mcp__pricing__lookup", "mcp__supabase__write"],
                    model="haiku",
                ),
            },
        ),
    ):
        yield message
```

---

## 9. 기존 다담AI 자산 재활용

| 기존 자산 | 재활용 위치 | 비고 |
|-----------|------------|------|
| Wall Analysis (Claude) | Space Analyst Agent | 프롬프트 이식 |
| computeLayout | Design Planner Agent | 로직 Python 변환 |
| Gemini 이미지 생성 | Image Generator Agent 도구 | API 호출 이식 |
| Flux LoRA 모델 8종 | Image Generator Agent 도구 | Replicate API |
| 가격 데이터 | Quote Calculator 도구 | DB 마이그레이션 |
| design-rules/*.md | Design Planner 프롬프트 | 규칙 내장 |
| SVG 렌더링 | Detail Designer Agent | 설계도 생성 |
