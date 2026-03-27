"""벽면 측정 보정 — 설치자 실측 데이터 기반 자동 보정.

AI 측정값과 설치자 실측값을 비교하여 카테고리별 보정 계수를 산출합니다.
10건 미만이면 보정 없이 원본 반환.
"""

import logging

from shared.supabase_client import get_service_client

logger = logging.getLogger(__name__)

# 최소 보정 샘플 수
MIN_CALIBRATION_SAMPLES = 10
# 최대 보정 비율 (±15%)
MAX_CORRECTION_RATE = 0.15


async def get_calibration_factor(category: str) -> dict:
    """카테고리별 최근 50건의 평균 오차율 조회.

    Returns:
        {
            "correction_factor": 0.97,  # 1.0 = 보정 없음, 0.97 = 3% 축소
            "sample_count": 25,
            "avg_error_mm": -90,
            "avg_error_pct": -0.03,
        }
    """
    client = get_service_client()
    result = (
        client.table("measurement_calibrations")
        .select("width_error_mm, width_error_pct")
        .eq("category", category)
        .not_.is_("actual_wall_width_mm", "null")
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )

    rows = result.data or []
    if len(rows) < MIN_CALIBRATION_SAMPLES:
        return {
            "correction_factor": 1.0,
            "sample_count": len(rows),
            "avg_error_mm": 0,
            "avg_error_pct": 0.0,
            "note": f"샘플 부족 ({len(rows)}/{MIN_CALIBRATION_SAMPLES}), 보정 미적용",
        }

    valid_rows = [r for r in rows if r.get("width_error_pct") is not None]
    if not valid_rows:
        return {"correction_factor": 1.0, "sample_count": 0, "avg_error_mm": 0, "avg_error_pct": 0.0}

    avg_error_pct = sum(r["width_error_pct"] for r in valid_rows) / len(valid_rows)
    avg_error_mm = sum(r["width_error_mm"] for r in valid_rows) / len(valid_rows)

    # 보정 계수: AI가 과대 추정(+)이면 축소, 과소 추정(-)이면 확대
    correction = max(-MAX_CORRECTION_RATE, min(MAX_CORRECTION_RATE, -avg_error_pct))
    factor = 1.0 + correction

    logger.info(
        "Calibration for %s: factor=%.4f, avg_error=%.1fmm (%.2f%%), samples=%d",
        category, factor, avg_error_mm, avg_error_pct * 100, len(valid_rows),
    )

    return {
        "correction_factor": round(factor, 4),
        "sample_count": len(valid_rows),
        "avg_error_mm": round(avg_error_mm, 1),
        "avg_error_pct": round(avg_error_pct, 4),
    }


async def apply_calibration(wall_width_mm: int, category: str) -> tuple[int, dict]:
    """AI 측정값에 보정 계수 적용.

    Returns:
        (corrected_width_mm, calibration_metadata)
    """
    cal = await get_calibration_factor(category)
    corrected = int(wall_width_mm * cal["correction_factor"])

    if corrected != wall_width_mm:
        logger.info(
            "Calibration applied: %dmm → %dmm (factor=%.4f, %d samples)",
            wall_width_mm, corrected, cal["correction_factor"], cal["sample_count"],
        )

    return corrected, cal


async def save_ai_measurement(
    project_id: str,
    category: str,
    wall_width_mm: int,
    sink_position_mm: int | None = None,
    cooktop_position_mm: int | None = None,
    confidence: float = 0.5,
) -> None:
    """AI 측정값 저장 (설치자 실측값은 나중에 입력)."""
    client = get_service_client()
    client.table("measurement_calibrations").insert({
        "project_id": project_id,
        "category": category,
        "ai_wall_width_mm": wall_width_mm,
        "ai_sink_position_mm": sink_position_mm,
        "ai_cooktop_position_mm": cooktop_position_mm,
        "ai_confidence": confidence,
    }).execute()
    logger.info("Saved AI measurement for project %s: width=%dmm", project_id, wall_width_mm)
