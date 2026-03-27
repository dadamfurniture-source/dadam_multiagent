-- 007: 벽면 측정 보정 테이블
-- AI 측정값 vs 설치자 실측값 비교 → 자동 보정 계수 산출

CREATE TABLE IF NOT EXISTS public.measurement_calibrations (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                 UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,

    -- AI 측정값 (space_analysis에서 추출)
    ai_wall_width_mm           INT NOT NULL,
    ai_sink_position_mm        INT,
    ai_cooktop_position_mm     INT,
    ai_confidence              REAL,

    -- 설치자 실측값 (현장 방문 후 입력)
    actual_wall_width_mm       INT,
    actual_sink_position_mm    INT,
    actual_cooktop_position_mm INT,

    -- 자동 계산 컬럼
    width_error_mm             INT GENERATED ALWAYS AS (ai_wall_width_mm - actual_wall_width_mm) STORED,
    width_error_pct            REAL GENERATED ALWAYS AS (
        CASE WHEN actual_wall_width_mm > 0
        THEN (ai_wall_width_mm - actual_wall_width_mm)::REAL / actual_wall_width_mm
        ELSE NULL END
    ) STORED,

    category                   TEXT NOT NULL,
    created_at                 TIMESTAMPTZ DEFAULT now()
);

-- 카테고리별 최근 보정 데이터 빠른 조회
CREATE INDEX IF NOT EXISTS idx_meas_cal_category
    ON public.measurement_calibrations (category, created_at DESC);
