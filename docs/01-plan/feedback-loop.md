# 피드백 루프 시스템 설계

## 개요

데이터가 쌓일수록 AI 능력이 강화되는 4가지 피드백 루프를 구축한다.

```
┌──────────────────────────────────────────────────────────────────┐
│                    4개 피드백 루프                                 │
│                                                                  │
│  ① RAG 루프        ② LoRA 재학습 루프    ③ 가격 보정 루프          │
│  (설계 품질 ↑)      (이미지 품질 ↑)       (견적 정확도 ↑)          │
│                                                                  │
│  ④ 제약조건 학습 루프                                              │
│  (시공 문제 사전 방지)                                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 루프 ① RAG — 유사 사례 검색으로 설계 품질 향상

### 원리
```
고객 사진 업로드
    │
    ▼
Space Analyst (공간 분석)
    │
    ▼
┌─────────────────────────────────┐
│ Vector Search: 유사 공간 사례 검색  │ ◀── 시공 완료 사례 DB
│ (벽 길이, 카테고리, 스타일 유사도)   │
└──────────┬──────────────────────┘
           │ Top 3 유사 사례
           ▼
Design Planner (배치 계획)
    │  "과거 유사 사례에서 이 배치가 만족도 높았음"
    │  "이 공간에서는 600mm 하부장이 최적이었음"
    ▼
더 나은 설계 결과
```

### 데이터 수집 시점
| 시점 | 수집 데이터 | 용도 |
|------|-----------|------|
| 시뮬레이션 완료 | 공간 분석 JSON + 배치 JSON + 이미지 | 사례 DB |
| 고객 선택 | 여러 스타일 중 선택한 결과 | 선호도 |
| 설치 완료 | 실측 데이터 + 시공 사진 | 정답 데이터 |
| 고객 평가 | 만족도 (1~5) + 코멘트 | 품질 라벨 |
| A/S 발생 | 하자 유형 + 원인 | 네거티브 라벨 |

### 벡터 임베딩 대상
```python
# 사례 하나의 임베딩 입력
embedding_input = f"""
카테고리: {category}
스타일: {style}
벽면 길이: {wall_lengths}
배관 위치: {pipe_positions}
모듈 구성: {modules}
만족도: {rating}/5
특이사항: {notes}
"""
# → Claude/OpenAI Embedding → 1536차원 벡터
# → Supabase pgvector에 저장
# → 코사인 유사도 검색
```

---

## 루프 ② LoRA 재학습 — 시공 사진으로 이미지 품질 향상

### 원리
```
설치 완료 시 시공 사진 업로드
    │
    ▼
자동 분류 (Claude Vision)
    │  카테고리, 스타일, 품질 등급
    ▼
학습 대기열 (training_queue)
    │
    ▼  카테고리당 50장 이상 새 사진 누적 시
    │
LoRA 재학습 트리거 (Replicate)
    │  기존 모델 + 신규 사진 → 업데이트된 모델
    ▼
모델 버전 업데이트 (lora_models 테이블)
    │
    ▼
다음 시뮬레이션부터 개선된 모델 사용
```

### 재학습 기준
| 조건 | 트리거 |
|------|--------|
| 신규 시공 사진 50장 누적 | 자동 재학습 |
| 고객 만족도 4.5+ 사진 30장 | 고품질 데이터 우선 학습 |
| 분기 1회 | 정기 재학습 |
| 수동 트리거 | 관리자 판단 |

---

## 루프 ③ 가격 보정 — 실거래로 견적 정확도 향상

### 원리
```
AI 견적 (quote_calculator)
    │
    ▼
계약 확정 (실제 계약 금액)
    │
    ▼
┌───────────────────────────────┐
│ 오차 분석                       │
│ 오차율 = (계약금액 - AI견적) / AI견적  │
│                               │
│ 카테고리별 평균 오차율 산출        │
│ 모듈 유형별 오차율 산출            │
└──────────┬────────────────────┘
           │
           ▼
가격 보정 계수 테이블 업데이트
    │
    ▼
다음 견적부터 보정 계수 적용
```

### 보정 공식
```
보정 견적 = AI 기본 견적 × 카테고리 보정계수 × 지역 보정계수

예) 싱크대 AI 견적 180만원
    카테고리 보정: 1.08 (과거 평균 8% 과소 산출)
    지역 보정: 1.05 (서울 기준)
    → 보정 견적: 180만 × 1.08 × 1.05 = 204만원
```

---

## 루프 ④ 제약조건 학습 — A/S로부터 설계 규칙 자동 추가

### 원리
```
A/S 접수 (하자 유형 + 사진 + 설명)
    │
    ▼
원인 분석 (Claude)
    │  "600mm 하부장에 개수대를 넣으면 배수관 간섭 발생"
    │  "2400mm 이상 상부장은 처짐 발생"
    ▼
패턴 감지 (동일 원인 3건 이상)
    │
    ▼
제약조건 후보 생성
    │  "개수대 하부장은 최소 700mm 이상"
    │  "상부장 연속 길이 2100mm 초과 시 중간 지지대 필수"
    ▼
관리자 승인
    │
    ▼
Design Planner 프롬프트에 규칙 추가
    │
    ▼
다음 설계부터 해당 문제 사전 방지
```

---

## 데이터 모델 (신규 테이블)

```sql
-- ① RAG: 사례 벡터 DB
case_embeddings (
  id, project_id, category, style,
  space_summary, layout_summary,
  rating, embedding vector(1536),
  metadata JSONB
)

-- ② LoRA: 학습 대기열
training_queue (
  id, image_url, category, style,
  quality_grade, source,
  status, trained_at
)

-- ② LoRA: 모델 버전 관리
lora_model_versions (
  id, category, version, replicate_model_id,
  training_images_count, trigger_word,
  performance_score, is_active
)

-- ③ 가격: 보정 계수
price_calibrations (
  id, category, module_type, region,
  correction_factor, sample_count,
  avg_error_rate, last_calibrated_at
)

-- ④ 제약조건: 학습된 규칙
learned_constraints (
  id, category, rule_text, source_type,
  source_count, confidence,
  status, approved_by, applied_at
)

-- 공통: 고객 피드백
customer_feedback (
  id, project_id, order_id,
  rating, satisfaction_areas JSONB,
  selected_style, comment,
  installation_photos TEXT[]
)
```
