"""벽면 측정 정확도 개선 — 듀얼 모델 교차 검증 + 원근 보정.

Phase 2 구현:
- analyze_space_validated(): Claude + Gemini 병렬 실행 → 수치 confidence
- correct_for_perspective(): camera_params 기반 원근 왜곡 보정
- refine_utility_position(): 배관 영역 크롭 정밀 분석
"""

import asyncio
import logging
import math

logger = logging.getLogger(__name__)


# ─── 2-1. 듀얼 모델 교차 검증 ───

async def analyze_space_validated(
    image_b64: str,
    prompt: str,
    media_type: str = "image/jpeg",
) -> tuple[dict, float]:
    """Claude Sonnet 4 (주) + Gemini Flash (부) 병렬 실행 → 교차 검증.

    Returns:
        (analysis_result, numeric_confidence 0.0-1.0)
    """
    from agents.tools.image_tools import _call_gemini_vision
    from agents.tools.vision_tools import _call_claude_vision

    # 병렬 실행
    claude_task = _call_claude_vision(image_b64, prompt, media_type)
    gemini_task = _call_gemini_vision(image_b64, prompt, media_type)

    results = await asyncio.gather(claude_task, gemini_task, return_exceptions=True)
    claude_result = results[0] if not isinstance(results[0], Exception) else None
    gemini_result = results[1] if not isinstance(results[1], Exception) else None

    if isinstance(results[0], Exception):
        logger.warning("Claude vision failed: %s", results[0])
    if isinstance(results[1], Exception):
        logger.warning("Gemini vision failed: %s", results[1])

    # 하나만 성공한 경우
    if claude_result and not gemini_result:
        return claude_result, 0.7
    if gemini_result and not claude_result:
        return gemini_result, 0.5
    if not claude_result and not gemini_result:
        return {}, 0.0

    # 양쪽 모두 성공 → 벽면 너비 교차 검증
    claude_width = claude_result.get("wall_dimensions_mm", {}).get("width", 0)
    gemini_width = gemini_result.get("wall_dimensions_mm", {}).get("width", 0)
    delta = abs(claude_width - gemini_width)

    logger.info(
        "Cross-validation: Claude=%dmm, Gemini=%dmm, delta=%dmm",
        claude_width, gemini_width, delta,
    )

    if delta < 100:
        # 높은 일치 → Claude 결과 사용
        return claude_result, 0.9
    elif delta < 300:
        # 중간 일치 → 평균값으로 보정
        avg_width = (claude_width + gemini_width) // 2
        merged = claude_result.copy()
        if "wall_dimensions_mm" in merged:
            merged["wall_dimensions_mm"]["width"] = avg_width
        # 배관 위치도 평균
        for util_key in ("water_supply", "exhaust_duct", "gas_pipe"):
            c_pos = claude_result.get("utility_positions", {}).get(util_key, {}).get("from_origin_mm")
            g_pos = gemini_result.get("utility_positions", {}).get(util_key, {}).get("from_origin_mm")
            if c_pos and g_pos:
                merged.setdefault("utility_positions", {}).setdefault(util_key, {})["from_origin_mm"] = (c_pos + g_pos) // 2
        return merged, 0.6
    else:
        # 큰 불일치 → Claude 사용하되 낮은 신뢰도
        logger.warning("Large measurement delta: %dmm — flagging for review", delta)
        return claude_result, 0.3


# ─── 2-3. 원근 보정 ───

# 초점 거리별 방사 왜곡 계수 (광각일수록 가장자리 왜곡 큼)
_DISTORTION_K = {
    24: -0.08,
    28: -0.05,
    35: -0.02,
    50: -0.01,
}


def correct_for_perspective(
    measured_width_mm: int,
    camera_params: dict,
) -> int:
    """camera_params의 focal_length 기반 원근 왜곡 보정.

    광각 렌즈(24mm)에서 가장자리 타일이 실제보다 넓게 보임 → 총 너비 과대 추정.
    보정: 타일이 벽면 가장자리에서 주로 측정되었다고 가정하고 축소 보정.

    Args:
        measured_width_mm: 타일 카운팅 기반 측정값
        camera_params: {"focal_length_mm": 24, "camera_distance_mm": 3000, ...}

    Returns:
        보정된 벽면 너비 (mm)
    """
    focal = camera_params.get("focal_length_mm", 35)
    if not focal or focal <= 0:
        return measured_width_mm

    # 가장 가까운 초점 거리의 왜곡 계수 사용
    sorted_focals = sorted(_DISTORTION_K.keys())
    k = _DISTORTION_K.get(focal)
    if k is None:
        # 보간
        if focal <= sorted_focals[0]:
            k = _DISTORTION_K[sorted_focals[0]]
        elif focal >= sorted_focals[-1]:
            k = _DISTORTION_K[sorted_focals[-1]]
        else:
            for i in range(len(sorted_focals) - 1):
                f1, f2 = sorted_focals[i], sorted_focals[i + 1]
                if f1 <= focal <= f2:
                    t = (focal - f1) / (f2 - f1)
                    k = _DISTORTION_K[f1] * (1 - t) + _DISTORTION_K[f2] * t
                    break

    # 가장자리 타일의 평균 위치 (벽면 끝 = 1.0)
    avg_edge_position = 0.8  # 타일 대부분이 0.8 위치에서 측정된다고 가정
    correction_factor = 1.0 + k * (avg_edge_position ** 2)
    corrected = int(measured_width_mm * correction_factor)

    if corrected != measured_width_mm:
        logger.info(
            "Perspective correction: %dmm → %dmm (focal=%dmm, k=%.4f)",
            measured_width_mm, corrected, focal, k,
        )

    return corrected
