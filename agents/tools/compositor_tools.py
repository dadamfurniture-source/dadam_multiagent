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
) -> str:
    """Generate closed-door furniture image using 3D render as layout guide.

    Args:
        original_b64: Original site photo (base64)
        render_b64: Blender render showing exact cabinet layout (base64)
        style: Style name
        category: Furniture category
        placement_note: Extra placement instructions (sink/cooktop positions)
        reference_images: Optional style reference images (base64)

    Returns:
        Photorealistic furniture image as base64 PNG
    """
    extra = [render_b64]
    if reference_images:
        extra.extend(reference_images[:1])

    style_label = STYLE_SHORT.get(style, "white flat-panel")

    prompt = (
        f"Remove all clutter, debris, tools. If construction site, finish the room: "
        f"lay wood flooring, patch ceiling, clean walls. "
        f"Then install {style_label} {category} cabinets. "
        f"The SECOND image shows exact 3D layout — copy module positions, "
        f"door/drawer placement, sink bowl+faucet EXACTLY. "
        f"Cooktop area: DRAWERS below (not doors, not empty). "
        f"{placement_note}"
        f"Upper cabinets flush ceiling. Continuous countertop. Photorealistic."
    )

    if len(prompt) > 500:
        prompt = prompt[:497] + "..."

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

    Uses the closed-door result as base (not original photo) so the
    cabinet structure is identical — only doors swing open.

    Args:
        furniture_b64: Closed-door furniture image (base64) — used as base
        render_b64: Blender open-door render (base64) — layout guide
        style: Style name
        category: Furniture category
        open_contents: What's inside the cabinets
        reference_images: Optional style reference images (base64)

    Returns:
        Open-door furniture image as base64 PNG
    """
    extra = [render_b64]
    if reference_images:
        extra.extend(reference_images[:1])

    prompt = (
        f"Open ALL cabinet doors and drawers in this image. "
        f"The SECOND image shows the exact open layout — match it. "
        f"Swing doors open 90 degrees. Pull drawers forward. "
        f"Inside cabinets: {open_contents}. "
        f"Do NOT change walls, tiles, floor, ceiling, countertop, or perspective. "
        f"Keep the SAME cabinet structure, only open the doors."
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
) -> str:
    """Alias for generate_closed_door (backward compatibility)."""
    return await generate_closed_door(
        original_b64, render_b64, style, category,
        reference_images=reference_images,
    )
