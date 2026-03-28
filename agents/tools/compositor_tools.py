"""3D Render-Guided Compositor.

Uses Blender's 3D render as a REFERENCE IMAGE for Gemini,
rather than pixel-level alpha compositing (which requires
exact camera calibration that's impossible from a single photo).

Strategy: Send the 3D render as an extra reference to Gemini so it
knows the exact layout, module positions, door vs drawer placement,
sink/faucet position — then Gemini generates the final photorealistic
result matching the original photo's perspective.
"""

import logging

from agents.tools.image_tools import _call_gemini_image

logger = logging.getLogger(__name__)

import random

# 기본 이미지: 무채색 계열 랜덤 (검정 제외)
_NEUTRAL_COLORS = [
    "sand gray flat-panel",
    "fog gray flat-panel",
    "milk white flat-panel",
    "cashmere flat-panel",
    "warm gray flat-panel",
    "ivory white flat-panel",
]

STYLE_SHORT = {
    "modern": None,  # None → 랜덤 무채색 선택
    "nordic": None,
    "classic": None,
    "natural": None,
    "industrial": None,
    "luxury": None,
}

def _get_neutral_style() -> str:
    return random.choice(_NEUTRAL_COLORS)


async def generate_closed_door(
    original_b64: str,
    render_b64: str,
    style: str,
    category: str,
    placement_note: str = "",
    reference_images: list[str] | None = None,
    wall_width: int = 0,
    module_count: int = 0,
    module_desc: str = "",
) -> str:
    """Generate closed-door furniture image using 3D render as layout guide."""
    extra = [render_b64]
    if reference_images:
        extra.extend(reference_images[:1])

    style_label = STYLE_SHORT.get(style) or _get_neutral_style()

    module_instruction = f"{module_desc} " if module_desc else ""

    logger.info("Selected cabinet color: %s", style_label)

    prompt = (
        f"Edit this photo: remove people, tools, construction equipment, debris. "
        f"If the room is under construction: "
        f"replace cement/concrete floor with wood laminate flooring, "
        f"apply clean white wallpaper to exposed ceiling and bare wood surfaces, "
        f"fill ceiling holes with recessed LED downlights, "
        f"make the space look like a finished modern Korean apartment. "
        f"Keep existing wall tiles and backsplash exactly. "
        f"Install {style_label} {category}. "
        f"Handleless flat panel doors with finger groove along top edge. "
        f"Upper cabinets flush ceiling, lower cabinets with countertop, full wall edge-to-edge. "
        f"{module_instruction}"
        f"2nd image = 3D layout guide, copy positions exactly. "
        f"Replace sink bowl, faucet, cooktop, and range hood with brand new ones. "
        f"Cooktop must be flush-mounted built-in (completely flat, embedded into countertop). "
        f"Cooktop cabinet: exactly 2 stacked flat drawer panels below. {placement_note}"
        f"Clean finished floor."
    )

    if len(prompt) > 1500:
        prompt = prompt[:1497] + "..."

    logger.info("Closed-door prompt (%d chars): %s", len(prompt), prompt[:150])

    result_b64 = await _call_gemini_image(prompt, original_b64, extra_images=extra)
    logger.info("Closed-door generation complete")
    return result_b64


async def generate_open_door(
    furniture_b64: str,
    render_b64: str,
    style: str,
    category: str,
    open_contents: str = "items on shelves",
    reference_images: list[str] | None = None,
) -> str:
    """Generate open-door image from the CLOSED furniture result.

    Uses the closed-door result as base so cabinet structure is identical.
    """
    extra = [render_b64]
    if reference_images:
        extra.extend(reference_images[:1])

    prompt = (
        f"Edit this photo: open all cabinet doors 90deg outward, pull drawers 40% forward. "
        f"2nd image = open layout guide. Inside: {open_contents}. "
        f"Keep the same cabinet structure, color, countertop, sink faucet position, cooktop position. "
        f"Doors and drawers are handleless flat panels with finger groove along top edge. "
        f"Keep walls, tiles, floor, ceiling, perspective identical."
    )

    logger.info("Open-door prompt (%d chars): %s", len(prompt), prompt[:150])

    result_b64 = await _call_gemini_image(prompt, furniture_b64, extra_images=extra)
    logger.info("Open-door generation complete")
    return result_b64


# Backward-compatible alias
async def composite_render_onto_photo(
    original_b64: str,
    render_b64: str,
    style: str,
    category: str,
    reference_images: list[str] | None = None,
    wall_width: int = 0,
) -> str:
    """Alias for generate_closed_door (backward compatibility)."""
    return await generate_closed_door(
        original_b64,
        render_b64,
        style,
        category,
        reference_images=reference_images,
        wall_width=wall_width,
    )


async def inpaint_sink_and_cooktop(
    furniture_b64: str,
    modules: list,
    wall_width: int,
) -> str:
    """싱크볼/쿡탑을 정확한 위치에 분리 인페인팅.

    Step 1: 싱크 영역 마스크 → 싱크볼+수전 인페인팅
    Step 2: 쿡탑 영역 마스크 → 매립형 쿡탑 인페인팅

    마스크로 영역을 지정하므로 위치를 바꿀 수 없음.
    """
    import base64 as b64mod
    import io
    from PIL import Image, ImageDraw

    img_bytes = b64mod.b64decode(furniture_b64)
    result_b64 = furniture_b64

    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    for m in modules:
        mtype = m.get("type", "")
        if mtype not in ("sink_bowl", "cooktop"):
            continue

        mx = m.get("position_x", 0)
        mw = m.get("width", 600)

        # 모듈의 이미지 좌표 계산 (wall_width → 이미지 너비 비례)
        left_pct = mx / wall_width if wall_width > 0 else 0
        right_pct = (mx + mw) / wall_width if wall_width > 0 else 1
        # 상판 영역 (이미지 높이 38~48% 지점 — 상판 표면)
        mask_left = int(left_pct * w) + 10
        mask_right = int(right_pct * w) - 10
        mask_top = int(h * 0.38)
        mask_bottom = int(h * 0.48)

        # 마스킹 (흰색)
        cur_img = Image.open(io.BytesIO(b64mod.b64decode(result_b64)))
        draw = ImageDraw.Draw(cur_img)
        draw.rectangle([mask_left, mask_top, mask_right, mask_bottom], fill=(255, 255, 255))

        buf = io.BytesIO()
        cur_img.save(buf, format="PNG")
        masked_b64 = b64mod.b64encode(buf.getvalue()).decode()

        # 인페인팅 프롬프트
        if mtype == "sink_bowl":
            inpaint_prompt = (
                "Fill the white area with a rectangular stainless steel sink bowl "
                "embedded in the countertop, with a tall gooseneck faucet. "
                "Keep everything else identical."
            )
        else:
            inpaint_prompt = (
                "Fill the white area with a flush-mounted built-in cooktop "
                "(completely flat black glass surface embedded in the countertop, no protruding edges). "
                "Keep everything else identical."
            )

        logger.info("Inpainting %s at %d%%-%d%%", mtype, int(left_pct*100), int(right_pct*100))
        result_b64 = await _call_gemini_image(inpaint_prompt, masked_b64)

    return result_b64
