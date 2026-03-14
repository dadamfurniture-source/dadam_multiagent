"""CEO Agent - 직접 API 호출 오케스트레이터 (claude_agent_sdk 제거)

파이프라인: 공간분석 → 배치계획 → 이미지생성 → 견적산출
"""

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncGenerator

from agents.layout_engine import OPEN_DOOR_CONTENTS, plan_layout
from agents.tools.compositor_tools import generate_closed_door, generate_open_door
from agents.tools.image_tools import (
    _call_gemini_image,
    _call_replicate_inpaint,
    _create_furniture_mask,
)
from agents.tools.pricing_tools import (
    BASE_PRICES,
    COUNTERTOP_PRICES,
    INSTALLATION_BASE,
)
from agents.tools.vision_tools import _call_claude_vision
from shared.constants import CATEGORIES_EN
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
        "elegant traditional wood panel finish with warm brown tones. Brass or gold-tone handles."
    ),
    "natural": (
        "natural wood grain finish with organic earth tones. Matte finish, minimal hardware."
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
    "Sink area must have a visible sink bowl (stainless steel basin) with a faucet centered above it. "
    "Cabinets under cooktop/induction area must be DRAWERS (not doors). "
    "Keep original wall tiles exactly. "
)


async def _fetch_reference_images(category: str, style: str, limit: int = 2) -> list[str]:
    """Fetch matching reference images from style_references table.

    Returns base64-encoded images for the given category+style combination.
    Used to guide Blender materials and AI harmonization.
    """
    try:
        client = get_service_client()
        refs = (
            client.table("style_references")
            .select("image_url")
            .eq("category", category)
            .eq("style", style)
            .eq("is_active", True)
            .limit(limit)
            .execute()
        )

        result = []
        for ref in refs.data:
            try:
                b64, _ = await _download_image_b64(ref["image_url"])
                result.append(b64)
            except Exception as e:
                logger.warning("Failed to download reference image: %s", e)
        return result
    except Exception as e:
        logger.warning("Failed to fetch reference images: %s", e)
        return []


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

    client.table("generated_images").insert(
        {
            "project_id": project_id,
            "image_url": image_url,
            "type": image_type,
        }
    ).execute()

    return image_url


def _update_stage(project_id: str, stage: str):
    """Update project pipeline stage."""
    client = get_service_client()
    client.table("projects").update(
        {
            "pipeline_stage": stage,
        }
    ).eq("id", project_id).execute()


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
        logger.info(
            "Space analysis complete: %s", json.dumps(space_result, ensure_ascii=False)[:200]
        )
    except Exception as e:
        logger.error("Space analysis failed: %s", e)
        # 기본값으로 계속 진행
        space_result = {
            "wall_dimensions_mm": {"width": 3000, "height": 2400},
            "utility_positions": {},
        }

    # DB에 공간 분석 저장
    try:
        client.table("space_analyses").insert(
            {
                "project_id": request.project_id,
                "original_image_url": request.image_url,
                "analysis_json": space_result,
                "walls": space_result.get("wall_dimensions_mm"),
                "pipes": space_result.get("utility_positions"),
                "space_summary": f"Wall {space_result.get('wall_dimensions_mm', {}).get('width', 3000)}mm x {space_result.get('wall_dimensions_mm', {}).get('height', 2400)}mm",
                "confidence": float(space_result["confidence"])
                if isinstance(space_result.get("confidence"), (int, float))
                else 0.7,
            }
        ).execute()
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
            logger.info(
                "Layout plan: %d modules, %dmm total",
                layout_data.get("module_count", 0),
                layout_data.get("total_module_width", 0),
            )
    except Exception as e:
        logger.error("Layout planning failed: %s", e)
        layout_data = {"modules": [], "error": str(e)}

    # DB에 배치 저장
    try:
        client.table("layouts").insert(
            {
                "project_id": request.project_id,
                "layout_json": layout_data,
                "modules": layout_data.get("modules", []),
                "total_width_mm": layout_data.get("total_width", wall_width),
            }
        ).execute()
    except Exception as e:
        logger.warning("Failed to save layout: %s", e)

    yield {"type": "progress", "content": "layout design complete"}

    # ─── 3. 이미지 생성 (Blender 3D → AI Compositor, fallback: Gemini-only) ───
    yield {"type": "progress", "content": "image generation started"}
    _update_stage(request.project_id, "image_gen")

    category_name = CATEGORIES_EN.get(request.category, request.category)
    STYLE_GUIDE.get(style, STYLE_GUIDE["modern"])
    module_desc = f"{len(layout_data.get('modules', []))} modules, {wall_width}mm wide"

    # 벽 형태 (1자/L자/U자) — Claude Vision 분석 결과
    wall_layout = space_result.get("wall_layout", "straight")
    layout_desc = ""
    if wall_layout == "straight":
        layout_desc = (
            "STRAIGHT single-wall layout ONLY. All cabinets in a flat line on ONE wall. "
            "NO L-shape, NO corner wrapping, NO side-wall cabinets. "
        )
    elif wall_layout == "L-shape":
        layout_desc = "L-shaped corner layout. Cabinets wrap around the corner. "
    elif wall_layout == "U-shape":
        layout_desc = "U-shaped layout. Cabinets on three walls. "
    logger.info("Wall layout: %s", wall_layout)

    # 배관 위치 기반 배치 지시 (싱크대 카테고리)
    placement_note = ""
    if request.category == "sink":
        parts = []
        if sink_pos:
            pct = int(sink_pos / wall_width * 100) if wall_width > 0 else 30
            parts.append(
                f"Stainless steel sink bowl EXACTLY at {pct}% from left (water pipe position)"
            )
        if cooktop_pos:
            pct2 = int(cooktop_pos / wall_width * 100) if wall_width > 0 else 70
            parts.append(f"cooktop zone with DRAWERS below at {pct2}% from left")
        if parts:
            placement_note = ". ".join(parts) + ". "
        placement_note += "No tall cabinets. "

    furniture_b64 = None
    open_b64 = None

    # ── 2.5 참고 이미지 조회 (category + style 매칭) ──
    ref_images = await _fetch_reference_images(request.category, style)
    if ref_images:
        logger.info("Found %d reference images for %s/%s", len(ref_images), request.category, style)

    # ── 3a. Blender 3D Rendering Pipeline ──
    use_blender = os.environ.get("USE_BLENDER", "true").lower() == "true"
    camera_params = space_result.get("camera_params", {})

    if use_blender:
        try:
            from agents.blender import render_cabinet_scene

            # Render closed doors (3D reference image)
            closed_render = await render_cabinet_scene(
                layout_data=layout_data,
                camera_params=camera_params,
                style=style,
                category=request.category,
                door_state="closed",
            )
            logger.info("Blender closed-door render complete")

            # Render open doors (same scene, only door rotation changes)
            open_render = await render_cabinet_scene(
                layout_data=layout_data,
                camera_params=camera_params,
                style=style,
                category=request.category,
                door_state="open",
            )
            logger.info("Blender open-door render complete")

            # Generate closed-door image: Gemini uses 3D render as layout guide
            furniture_b64 = await generate_closed_door(
                original_b64=image_b64,
                render_b64=closed_render,
                style=style,
                category=request.category,
                placement_note=placement_note,
                reference_images=ref_images,
            )
            await _upload_image(request.project_id, request.user_id, furniture_b64, "furniture")
            logger.info("Blender-guided furniture generation complete")

            # Generate open-door from CLOSED result (ensures same structure)
            contents = OPEN_DOOR_CONTENTS.get(request.category, "items on shelves")
            open_b64 = await generate_open_door(
                furniture_b64=furniture_b64,
                render_b64=open_render,
                style=style,
                category=request.category,
                open_contents=contents,
                reference_images=ref_images,
            )
            await _upload_image(request.project_id, request.user_id, open_b64, "open")
            logger.info("Blender-guided open-door generation complete")

        except Exception as e:
            logger.warning("Blender pipeline failed: %s, falling back to Gemini-only", e)
            furniture_b64 = None
            open_b64 = None

    # ── 3b. Fallback: Gemini-only pipeline (existing code, preserved 100%) ──
    if not furniture_b64:
        style_short = {
            "modern": "white flat-panel",
            "nordic": "light wood grain",
            "classic": "warm brown wood panel",
            "natural": "natural wood matte",
            "industrial": "dark charcoal matte",
            "luxury": "high-gloss pearl white",
        }.get(style, "white flat-panel")

        furniture_prompt = (
            f"Remove ALL objects, people, clothes, tools, debris from this photo. "
            f"Then install {layout_desc}{style_short} {category_name}. "
            f"Upper wall cabinets flush with ceiling. Lower base cabinets with countertop. "
            f"{placement_note}"
            f"PRESERVE original walls, tiles, floor, ceiling EXACTLY. Photorealistic."
        )
        if len(furniture_prompt) > 500:
            furniture_prompt = furniture_prompt[:497] + "..."
        logger.info(
            "Furniture prompt (%d chars): %s", len(furniture_prompt), furniture_prompt[:200]
        )

        try:
            furniture_b64 = await _call_gemini_image(
                furniture_prompt, image_b64, extra_images=ref_images or None
            )
            await _upload_image(request.project_id, request.user_id, furniture_b64, "furniture")
            logger.info("Furniture generated via Gemini (single-step cleanup+install)")
        except Exception as e:
            logger.warning("Gemini furniture failed: %s, trying Replicate inpaint", e)

        # Fallback: Cleanup → Replicate 인페인팅 (Gemini 실패 시)
        if not furniture_b64:
            try:
                cleanup_prompt = (
                    "Remove all furniture, objects, people, clothes, debris. "
                    "Show only clean empty room with bare walls and floor. "
                    "Keep wall color, tiles, lighting EXACTLY."
                )
                cleanup_b64 = await _call_gemini_image(cleanup_prompt, image_b64)
                mask_b64 = _create_furniture_mask(cleanup_b64, request.category, space_result)
                neg_prompt = ""
                if wall_layout == "straight":
                    neg_prompt = "L-shaped, corner cabinet, wraparound, bent, angled"
                inpaint_prompt = (
                    f"{layout_desc}{style} style {category_name}, "
                    f"{module_desc}. {placement_note}"
                    f"Photorealistic interior. {IMAGE_RULES}"
                )
                if len(inpaint_prompt) > 500:
                    inpaint_prompt = inpaint_prompt[:497] + "..."
                furniture_b64 = await _call_replicate_inpaint(
                    cleanup_b64, mask_b64, inpaint_prompt, negative_prompt=neg_prompt
                )
                await _upload_image(request.project_id, request.user_id, furniture_b64, "furniture")
                logger.info("Furniture generated via Replicate fallback")
            except Exception as e:
                logger.error("Furniture generation failed (all methods): %s", e)

    # 3c. Open Door (Gemini) — fallback 경로에서만 (Blender가 이미 생성한 경우 skip)
    if furniture_b64 and not open_b64:
        try:
            contents = OPEN_DOOR_CONTENTS.get(request.category, "items on shelves")
            open_prompt = (
                f"Edit this image: open all cabinet doors showing interior. "
                f"Inside: {contents}. "
                f"Do NOT change walls, tiles, floor, or perspective."
            )
            open_b64 = await _call_gemini_image(
                open_prompt, furniture_b64, extra_images=ref_images or None
            )
            await _upload_image(request.project_id, request.user_id, open_b64, "open")
            logger.info("Open door image generated via Gemini")
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
            quote_items.append(
                {
                    "module": f"{m.get('type', 'cabinet')} {width_str}mm",
                    "price": base_price,
                }
            )

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

        client.table("quotes").insert(
            {
                "project_id": request.project_id,
                "items_json": quote_data,
                "subtotal": subtotal,
                "installation_fee": installation,
                "tax_amount": vat,
                "total_price": total,
            }
        ).execute()

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
