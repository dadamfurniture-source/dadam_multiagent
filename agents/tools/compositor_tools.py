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

STYLE_SHORT = {
    "modern": "white flat-panel",
    "nordic": "light wood grain",
    "classic": "warm brown wood panel",
    "natural": "natural wood matte",
    "industrial": "dark charcoal matte",
    "luxury": "high-gloss pearl white",
}


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

    style_label = STYLE_SHORT.get(style, "white flat-panel")

    # 벽 폭/모듈 정보 — 빈틈 없이 채우기 지시
    wall_fill = (
        f"Cabinets MUST span the ENTIRE wall width ({wall_width}mm) with NO gaps on left or right. "
        f"Left edge of cabinets = left wall edge. Right edge = right wall edge. "
    ) if wall_width > 0 else (
        "Cabinets MUST span the ENTIRE wall from left edge to right edge with NO gaps. "
    )

    # 모듈 구성 지시
    module_instruction = (
        f"Lower cabinet layout: {module_desc} "
    ) if module_desc else ""

    prompt = (
        f"Remove ALL people, workers, tools, debris, objects ON the floor from this photo. "
        f"PRESERVE wall tiles, tile color, tile pattern, backsplash, perspective EXACTLY. "
        f"Bare concrete floor → add wood flooring. Unfinished ceiling → patch. "
        f"Install {style_label} {category}: "
        f"UPPER wall cabinets touching ceiling + LOWER base cabinets + countertop. "
        f"{wall_fill}"
        f"{module_instruction}"
        f"The 2nd image = 3D layout guide. Copy positions and FULL WIDTH EXACTLY. "
        f"Cooktop zone: 2 horizontal pull-out DRAWERS with handles below (NOT oven, NOT open, NOT empty). "
        f"{placement_note}Photorealistic. Clean bare floor."
    )

    if len(prompt) > 1400:
        prompt = prompt[:1397] + "..."

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
        f"Edit this EXACT image: open all cabinet doors and pull out all drawers. "
        f"The 2nd image = 3D open layout guide. Match it. "
        f"Keep IDENTICAL cabinet structure, style, color, handles, countertop. "
        f"Upper+lower doors swing 90deg outward. Drawers pulled 40% forward. "
        f"Inside: {open_contents}. "
        f"Do NOT change walls, tiles, floor, ceiling, or perspective AT ALL."
    )

    if len(prompt) > 500:
        prompt = prompt[:497] + "..."

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
