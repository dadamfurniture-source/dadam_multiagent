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


async def composite_render_onto_photo(
    original_b64: str,
    render_b64: str,
    style: str,
    category: str,
    reference_images: list[str] | None = None,
) -> str:
    """Use 3D render as layout reference for AI image generation.

    Instead of alpha-compositing (which fails without camera calibration),
    we pass the Blender render as a reference image to Gemini alongside
    the original photo. Gemini uses the 3D render to understand exact
    module positions, door/drawer placement, and sink/faucet locations.

    Args:
        original_b64: Cleaned-up original photo (base64)
        render_b64: Blender render showing exact cabinet layout (base64)
        style: Style name
        category: Furniture category
        reference_images: Optional style reference images (base64)

    Returns:
        Final photorealistic image as base64 PNG
    """
    # Build extra images: 3D render first (layout guide), then style refs
    extra = [render_b64]
    if reference_images:
        extra.extend(reference_images[:1])  # max 2 extra total in Gemini

    style_short = {
        "modern": "white flat-panel",
        "nordic": "light wood grain",
        "classic": "warm brown wood panel",
        "natural": "natural wood matte",
        "industrial": "dark charcoal matte",
        "luxury": "high-gloss pearl white",
    }.get(style, "white flat-panel")

    prompt = (
        f"Install {style_short} {category} cabinets in this photo. "
        f"The SECOND image shows the exact 3D layout — match module positions, "
        f"door count, drawer placement, sink bowl and faucet positions EXACTLY. "
        f"Upper cabinets flush with ceiling. Countertop continuous across all modules. "
        f"PRESERVE original walls, tiles, floor, ceiling, perspective EXACTLY. "
        f"Photorealistic."
    )

    if len(prompt) > 500:
        prompt = prompt[:497] + "..."

    logger.info("Compositor prompt (%d chars): %s", len(prompt), prompt[:150])

    try:
        result_b64 = await _call_gemini_image(
            prompt,
            original_b64,
            extra_images=extra,
        )
        logger.info("Render-guided generation complete")
        return result_b64
    except Exception as e:
        logger.warning("Render-guided generation failed: %s", e)
        raise
