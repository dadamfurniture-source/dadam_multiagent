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
from shared.constants import CATEGORIES, CATEGORIES_EN, PLANS
from shared.supabase_client import get_service_client

logger = logging.getLogger(__name__)

# 스타일별 색상/재질 가이드
STYLE_GUIDE = {
    "modern": (
        "flat solid color finish, NO wood grain texture. "
        "Colors: white, sand gray, fog gray, or milk white. "
        "Glass doors with aluminum frames (black nickel frame with bronze glass, or silver frame with sky blue glass)."
    ),
    "nordic": (
        "light natural wood grain finish with warm Scandinavian tones. "
        "Glass doors with aluminum frames (silver frame with sky blue glass)."
    ),
    "classic": (
        "elegant traditional wood panel finish with warm brown tones. "
        "Brass or gold-tone handles."
    ),
    "natural": (
        "natural wood grain finish with organic earth tones. "
        "Matte finish, minimal hardware."
    ),
    "industrial": (
        "dark matte finish with metal accents. "
        "Colors: charcoal, dark gray, black. Metal frame glass doors."
    ),
    "luxury": (
        "high-gloss lacquer finish in premium colors. "
        "Colors: champagne gold, pearl white, deep navy. Gold-tone hardware."
    ),
}

# 이미지 생성 공통 규칙
IMAGE_RULES = (
    "No ovens or electronic appliances. "
    "No tall cabinets for sink category. "
    "Keep original wall tiles exactly. "
)


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
    """Download image from URL and return (base64, media_type).

    Supports both public URLs and Supabase storage paths.
    For private buckets, uses signed URL via service client.
    """
    import httpx

    # Supabase private bucket — generate signed URL
    if "supabase.co/storage/v1/object" in url:
        try:
            client = get_service_client()
            # Extract path from URL: .../object/public/originals/user_id/project_id/file
            # or .../object/originals/...
            path_part = url.split("/object/")[1] if "/object/" in url else ""
            # Remove "public/" prefix if present
            if path_part.startswith("public/"):
                path_part = path_part[7:]
            # Split bucket/path
            parts = path_part.split("/", 1)
            if len(parts) == 2:
                bucket_name = parts[0]
                file_path = parts[1].rstrip("?")  # Remove trailing ?
                signed = client.storage.from_(bucket_name).create_signed_url(file_path, 300)
                url = signed.get("signedURL", signed.get("signedUrl", url))
                logger.info("Using signed URL for %s/%s", bucket_name, file_path)
        except Exception as e:
            logger.warning("Failed to create signed URL, trying direct: %s", e)

    # Clean URL
    url = url.rstrip("?")

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
    """Upload base64 image to Supabase storage (public bucket) and record in generated_images."""
    client = get_service_client()
    image_bytes = base64.b64decode(image_b64)
    path = f"{user_id}/{project_id}/{image_type}.png"

    # Use public bucket for generated images
    client.storage.from_("generated-images").upload(
        path, image_bytes, {"content-type": "image/png"}
    )
    image_url = client.storage.from_("generated-images").get_public_url(path)
    # Remove trailing ? from URL
    image_url = image_url.rstrip("?")

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
            "original_image_url": request.image_url,
            "analysis_json": space_result,
            "walls": space_result.get("wall_dimensions_mm"),
            "pipes": space_result.get("utility_positions"),
            "space_summary": f"Wall {space_result.get('wall_dimensions_mm', {}).get('width', 3000)}mm x {space_result.get('wall_dimensions_mm', {}).get('height', 2400)}mm",
            "confidence": space_result.get("confidence", 0.7),
        }).execute()
    except Exception as e:
        logger.warning("Failed to save space analysis: %s", e)

    yield {"type": "progress", "content": "space analysis complete"}

    # ─── 2. 배치 계획 (Layout Engine) ───
    yield {"type": "progress", "content": "layout design started"}
    _update_stage(request.project_id, "design")

    wall_width = space_result.get("wall_dimensions_mm", {}).get("width", 3000)
    utilities = space_result.get("utility_positions", {})
    # Claude Vision 출력 키: from_origin_mm (position_mm이 아님)
    water_supply = utilities.get("water_supply", {})
    exhaust_duct = utilities.get("exhaust_duct", {})
    sink_pos = water_supply.get("from_origin_mm") or water_supply.get("position_mm")
    cooktop_pos = exhaust_duct.get("from_origin_mm") or exhaust_duct.get("position_mm")
    logger.info("Utility positions — sink: %s, cooktop: %s", sink_pos, cooktop_pos)

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
            "layout_json": layout_data,
            "modules": layout_data.get("modules", []),
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
            f"Remove all furniture, construction debris, tools, trash, "
            f"and work materials from this photo. "
            f"Fix unfinished surfaces: fill gaps, repair rough edges, "
            f"complete any incomplete wall/floor finishes. "
            f"Keep original wall tiles and floor exactly. "
            f"Clean empty space, natural lighting, same perspective."
        )
        cleanup_b64 = await _call_gemini_image(cleanup_prompt, image_b64)
        await _upload_image(request.project_id, request.user_id, cleanup_b64, "cleanup")
        logger.info("Cleanup image generated")
    except Exception as e:
        logger.error("Cleanup generation failed: %s", e)
        cleanup_b64 = image_b64  # 원본으로 대체

    # 3b. Furniture (Flux LoRA → Gemini fallback)
    furniture_b64 = None
    category_name = CATEGORIES_EN.get(request.category, request.category)
    style_desc = STYLE_GUIDE.get(style, STYLE_GUIDE["modern"])
    module_desc = f"{len(layout_data.get('modules', []))} modules, {wall_width}mm wide"

    # 배관 위치 기반 배치 지시 (싱크대 카테고리)
    placement_note = ""
    if request.category == "sink":
        parts = []
        if sink_pos:
            parts.append(f"Sink bowl at {sink_pos}mm from left wall")
        if cooktop_pos:
            parts.append(f"cooktop/induction at {cooktop_pos}mm from left wall")
        if parts:
            placement_note = ". ".join(parts) + ". "
        placement_note += "No tall cabinets. "

    furniture_prompt = (
        f"Install {style} Korean {category_name} in this photo. "
        f"{style_desc} {module_desc}. {placement_note}"
        f"{IMAGE_RULES}Photorealistic, natural lighting."
    )

    # 참고 이미지 조회 (style_references 테이블)
    ref_images_b64 = []
    try:
        refs = (
            client.table("style_references")
            .select("image_url")
            .eq("category", request.category)
            .eq("style", style)
            .eq("is_active", True)
            .limit(2)
            .execute()
        )
        if refs.data:
            for ref in refs.data:
                try:
                    ref_b64, _ = await _download_image_b64(ref["image_url"])
                    ref_images_b64.append(ref_b64)
                except Exception:
                    pass
            if ref_images_b64:
                logger.info("Loaded %d reference images for %s/%s", len(ref_images_b64), request.category, style)
    except Exception as e:
        logger.warning("Failed to load reference images: %s", e)

    try:
        furniture_b64 = await _call_flux_lora(request.category, furniture_prompt)
        await _upload_image(request.project_id, request.user_id, furniture_b64, "furniture")
        logger.info("Furniture image generated via Flux LoRA")
    except Exception as e:
        logger.warning("Flux LoRA failed (%s), falling back to Gemini for furniture", e)
        try:
            base_img = cleanup_b64 or image_b64
            # 참고 이미지가 있으면 프롬프트에 안내 추가
            ref_prompt = furniture_prompt
            if ref_images_b64:
                ref_prompt = f"Use the reference images as style guide. {furniture_prompt}"
            furniture_b64 = await _call_gemini_image(ref_prompt, base_img, extra_images=ref_images_b64)
            await _upload_image(request.project_id, request.user_id, furniture_b64, "furniture")
            logger.info("Furniture image generated via Gemini (with %d refs)", len(ref_images_b64))
        except Exception as e2:
            logger.error("Furniture generation failed (both LoRA and Gemini): %s", e2)

    # 3c. Correction (Gemini)
    corrected_b64 = None
    if furniture_b64:
        try:
            correction_prompt = (
                f"Adjust furniture to match original space. "
                f"Keep wall tiles exactly as original. "
                f"Fix color temperature, shadows for realism. {IMAGE_RULES}"
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
            "items_json": quote_data,
            "subtotal": subtotal,
            "installation_fee": installation,
            "tax_amount": vat,
            "total_price": total,
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
