"""3D Render + Original Photo Compositor.

Composites Blender's transparent-background render onto the cleaned-up
original photo, then uses Gemini for lighting/texture harmonization.
"""

import base64
import io
import logging

from PIL import Image, ImageFilter

from agents.tools.image_tools import _call_gemini_image

logger = logging.getLogger(__name__)


def _alpha_composite(background_b64: str, overlay_b64: str) -> str:
    """Composite RGBA overlay onto RGB background using alpha channel.

    Args:
        background_b64: Background image (cleanup result) as base64
        overlay_b64: Overlay image with alpha (Blender render) as base64

    Returns:
        Composited image as base64 PNG
    """
    bg = Image.open(io.BytesIO(base64.b64decode(background_b64))).convert("RGBA")
    overlay = Image.open(io.BytesIO(base64.b64decode(overlay_b64))).convert("RGBA")

    # Resize overlay to match background if needed
    if overlay.size != bg.size:
        overlay = overlay.resize(bg.size, Image.LANCZOS)

    # Feather the alpha edges for smoother blending
    r, g, b, a = overlay.split()
    a_feathered = a.filter(ImageFilter.GaussianBlur(radius=2))
    overlay = Image.merge("RGBA", (r, g, b, a_feathered))

    # Composite
    result = Image.alpha_composite(bg, overlay)

    buf = io.BytesIO()
    result.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def composite_render_onto_photo(
    original_b64: str,
    render_b64: str,
    style: str,
    category: str,
    reference_images: list[str] | None = None,
) -> str:
    """Composite 3D render onto photo and harmonize with AI.

    Pipeline:
    1. Alpha-composite Blender render (transparent bg) onto cleanup image
    2. Send to Gemini for lighting/texture harmonization
    3. Optionally use reference images as style guide

    Args:
        original_b64: Cleaned-up original photo (base64)
        render_b64: Blender render with RGBA transparent background (base64)
        style: Style name for harmonization prompt
        category: Furniture category
        reference_images: Optional list of style reference images (base64)

    Returns:
        Final harmonized image as base64 PNG
    """
    # Step 1: Alpha composite
    composited_b64 = _alpha_composite(original_b64, render_b64)
    logger.info("Alpha composite complete")

    # Step 2: AI harmonization via Gemini
    style_instruction = ""
    if reference_images:
        style_instruction = "Match the style, color, and texture of the reference images provided. "

    harmonize_prompt = (
        f"Make this composited {category} furniture photorealistic. "
        f"{style_instruction}"
        f"Match lighting, shadows, reflections to the room environment. "
        f"Keep furniture positions and proportions EXACTLY. "
        f"Only adjust lighting, texture, shadows, reflections. "
        f"Style: {style}."
    )

    if len(harmonize_prompt) > 500:
        harmonize_prompt = harmonize_prompt[:497] + "..."

    try:
        harmonized_b64 = await _call_gemini_image(
            harmonize_prompt,
            composited_b64,
            extra_images=reference_images,
        )
        logger.info("Gemini harmonization complete")
        return harmonized_b64
    except Exception as e:
        logger.warning("Gemini harmonization failed: %s, returning raw composite", e)
        return composited_b64
