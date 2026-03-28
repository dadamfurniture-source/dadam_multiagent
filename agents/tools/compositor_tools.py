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

    prompt = (
        f"Edit this photo: remove people, tools, debris. "
        f"Keep existing wall tiles, backsplash, ceiling exactly. "
        f"Install {style_label} {category}. "
        f"Handleless flat panel doors with finger groove along top edge. "
        f"Upper cabinets flush ceiling, lower cabinets with countertop, full wall edge-to-edge. "
        f"{module_instruction}"
        f"2nd image = 3D layout guide, copy positions exactly. "
        f"Sink faucet must be placed directly above the water pipe position visible in the original photo. "
        f"Cooktop must be a flush-mounted built-in type (completely flat, embedded into the countertop surface, no protruding edges). "
        f"Cooktop cabinet: exactly 2 stacked flat drawer panels below. {placement_note}"
        f"Clean floor."
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
