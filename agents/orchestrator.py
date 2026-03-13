"""CEO Agent - 직접 API 호출 오케스트레이터 (claude_agent_sdk 제거)

파이프라인: 공간분석 → 배치계획 → 이미지생성 → 견적산출
"""

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import AsyncGenerator

from agents.layout_engine import OPEN_DOOR_CONTENTS, plan_layout
from agents.tools.image_tools import (
    _call_flux_lora,
    _call_gemini_image,
)
from agents.tools.pricing_tools import (
    BASE_PRICES,
    COUNTERTOP_PRICES,
    INSTALLATION_BASE,
)
from agents.tools.vision_tools import _call_claude_vision
from shared.constants import CATEGORIES, PLANS
from shared.supabase_client import get_service_client

logger = logging.getLogger(__name__)


@dataclass
class ProjectRequest:
    project_id: str
    user_id: str
    user_plan: str
    category: str
    style: str | None
    budget: int | None
    image_url: str
    notes: str | None


async def _download_image_b64(url: str) -> tuple[str, str]:
    """Download image from URL and return (base64, media_type)."""
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        b64 = base64.b64encode(resp.content).decode()
        ct = resp.headers.get("content-type", "image/jpeg")
        if "png" in ct:
            media_type = "image/png"
        elif "webp" in ct:
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"
        return b64, media_type


async def _upload_image(project_id: str, user_id: str, image_b64: str, image_type: str) -> str:
    """Upload base64 image to Supabase storage and record in generated_images."""
    client = get_service_client()
    image_bytes = base64.b64decode(image_b64)
    path = f"{user_id}/{project_id}/{image_type}.png"

    client.storage.from_("originals").upload(
        path, image_bytes, {"content-type": "image/png"}
    )
    image_url = client.storage.from_("originals").get_public_url(path)

    client.table("generated_images").insert({
        "project_id": project_id,
        "image_url": image_url,
        "type": image_type,
    }).execute()

    return image_url


def _update_stage(project_id: str, stage: str):
    """Update project pipeline stage."""
    client = get_service_client()
    client.table("projects").update({
        "pipeline_stage": stage,
    }).eq("id", project_id).execute()


async def process_project(request: ProjectRequest) -> AsyncGenerator[dict, None]:
    """메인 파이프라인 — 직접 API 호출 방식

    Yields:
        진행 상황 및 결과 딕셔너리
    """
    yield {"type": "status", "stage": "started", "project_id": request.project_id}

    client = get_service_client()
    style = request.style or "modern"

    # ─── 1. 공간 분석 (Claude Vision) ───
    yield {"type": "progress", "content": "space analysis started"}
    _update_stage(request.project_id, "space_analysis")

    try:
        image_b64, media_type = await _download_image_b64(request.image_url)
    except Exception as e:
        logger.error("Image download failed: %s", e)
        yield {"type": "error", "error": f"이미지 다운로드 실패: {e}"}
        return

    try:
        from agents.prompts import SPACE_ANALYST_PROMPT

        analysis_prompt = (
            f"{SPACE_ANALYST_PROMPT}\n\n"
            f"## Current Request\n"
            f"Category: {request.category}\n"
            f"Analyze this photo and return the JSON output as specified above."
        )
        space_result = await _call_claude_vision(image_b64, analysis_prompt, media_type)
        logger.info("Space analysis complete: %s", json.dumps(space_result, ensure_ascii=False)[:200])
    except Exception as e:
        logger.error("Space analysis failed: %s", e)
        # 기본값으로 계속 진행
        space_result = {
            "wall_dimensions_mm": {"width": 3000, "height": 2400},
            "utility_positions": {},
        }

    # DB에 공간 분석 저장
    try:
        client.table("space_analyses").insert({
            "project_id": request.project_id,
            "analysis_data": space_result,
            "wall_width_mm": space_result.get("wall_dimensions_mm", {}).get("width", 3000),
            "wall_height_mm": space_result.get("wall_dimensions_mm", {}).get("height", 2400),
        }).execute()
    except Exception as e:
        logger.warning("Failed to save space analysis: %s", e)

    yield {"type": "progress", "content": "space analysis complete"}

    # ─── 2. 배치 계획 (Layout Engine) ───
    yield {"type": "progress", "content": "layout design started"}
    _update_stage(request.project_id, "design")

    wall_width = space_result.get("wall_dimensions_mm", {}).get("width", 3000)
    utilities = space_result.get("utility_positions", {})
    sink_pos = utilities.get("water_supply", {}).get("position_mm")
    cooktop_pos = utilities.get("exhaust_duct", {}).get("position_mm")

    try:
        layout_data = plan_layout(
            wall_width=wall_width,
            category=request.category,
            sink_position=sink_pos,
            cooktop_position=cooktop_pos,
        )
        if "error" in layout_data:
            logger.warning("Layout returned error: %s", layout_data["error"])
        else:
            logger.info("Layout plan: %d modules, %dmm total",
                        layout_data.get("module_count", 0),
                        layout_data.get("total_module_width", 0))
    except Exception as e:
        logger.error("Layout planning failed: %s", e)
        layout_data = {"modules": [], "error": str(e)}

    # DB에 배치 저장
    try:
        client.table("layouts").insert({
            "project_id": request.project_id,
            "layout_data": layout_data,
            "total_width_mm": layout_data.get("total_width", wall_width),
        }).execute()
    except Exception as e:
        logger.warning("Failed to save layout: %s", e)

    yield {"type": "progress", "content": "layout design complete"}

    # ─── 3. 이미지 생성 (Gemini + Flux LoRA) ───
    yield {"type": "progress", "content": "image generation started"}
    _update_stage(request.project_id, "image_gen")

    # 3a. Cleanup (Gemini)
    cleanup_b64 = None
    try:
        cleanup_prompt = (
            f"Remove all existing furniture and objects from this photo. "
            f"Show only clean empty space with bare walls and floor. "
            f"Preserve original lighting and perspective exactly."
        )
        cleanup_b64 = await _call_gemini_image(cleanup_prompt, image_b64)
        await _upload_image(request.project_id, request.user_id, cleanup_b64, "cleanup")
        logger.info("Cleanup image generated")
    except Exception as e:
        logger.error("Cleanup generation failed: %s", e)
        cleanup_b64 = image_b64  # 원본으로 대체

    # 3b. Furniture (Flux LoRA)
    furniture_b64 = None
    try:
        module_desc = f"{len(layout_data.get('modules', []))} modules, {wall_width}mm wide"
        furniture_prompt = (
            f"{style} style Korean built-in furniture, {module_desc}, "
            f"photorealistic interior photography, natural lighting"
        )
        furniture_b64 = await _call_flux_lora(request.category, furniture_prompt)
        await _upload_image(request.project_id, request.user_id, furniture_b64, "furniture")
        logger.info("Furniture image generated")
    except Exception as e:
        logger.error("Furniture generation failed: %s", e)

    # 3c. Correction (Gemini)
    corrected_b64 = None
    if furniture_b64:
        try:
            correction_prompt = (
                f"Adjust this furniture image to match original space lighting and color. "
                f"Fix color temperature, shadows to look realistic in the room."
            )
            corrected_b64 = await _call_gemini_image(correction_prompt, furniture_b64)
            await _upload_image(request.project_id, request.user_id, corrected_b64, "corrected")
            logger.info("Correction image generated")
        except Exception as e:
            logger.error("Correction failed: %s", e)
            corrected_b64 = furniture_b64

    # 3d. Open Door (Gemini)
    if corrected_b64:
        try:
            contents = OPEN_DOOR_CONTENTS.get(request.category, "items on shelves")
            open_prompt = (
                f"Show all cabinet doors open revealing organized interior. "
                f"Inside: {contents}. Keep same perspective and lighting."
            )
            open_b64 = await _call_gemini_image(open_prompt, corrected_b64)
            await _upload_image(request.project_id, request.user_id, open_b64, "open")
            logger.info("Open door image generated")
        except Exception as e:
            logger.error("Open door generation failed: %s", e)

    yield {"type": "progress", "content": "image generation complete"}

    # ─── 4. 견적 산출 ───
    yield {"type": "progress", "content": "quote calculation started"}
    _update_stage(request.project_id, "quote")

    try:
        modules = layout_data.get("modules", [])
        subtotal = 0
        quote_items = []

        for m in modules:
            width_str = str(m.get("width", 600))
            base_price = BASE_PRICES.get("base_cabinet", {}).get(width_str, 180_000)
            subtotal += base_price
            quote_items.append({
                "module": f"{m.get('type', 'cabinet')} {width_str}mm",
                "price": base_price,
            })

        # 설치비
        installation = INSTALLATION_BASE.get(request.category, 150_000)
        # 상판 (기본 인조대리석)
        countertop_area = wall_width / 1000 * 0.58  # 폭 x 깊이(580mm)
        countertop_price = int(COUNTERTOP_PRICES["artificial_marble"] * countertop_area)

        supply_amount = subtotal + countertop_price + installation
        vat = int(supply_amount * 0.1)
        total = supply_amount + vat

        quote_data = {
            "items": quote_items,
            "subtotal": subtotal,
            "countertop": countertop_price,
            "installation": installation,
            "supply_amount": supply_amount,
            "vat": vat,
            "total": total,
        }

        client.table("quotes").insert({
            "project_id": request.project_id,
            "quote_data": quote_data,
            "total_amount": total,
        }).execute()

        logger.info("Quote: %s KRW", f"{total:,}")
    except Exception as e:
        logger.error("Quote calculation failed: %s", e)
        quote_data = {"error": str(e)}

    yield {"type": "progress", "content": "quote calculation complete"}

    # ─── 완료 ───
    yield {
        "type": "result",
        "project_id": request.project_id,
        "data": {
            "space_analysis": space_result,
            "layout": layout_data,
            "quote": quote_data,
        },
    }
