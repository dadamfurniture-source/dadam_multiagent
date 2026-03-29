"""3D Render-Guided Compositor — Codex 프롬프트 + Blender 가이드 통합.

태그 기반 프롬프트 ([WALL TILE LOCK], [PLATE LOCK], [COMPOSITE] 등)로
벽타일 원본 유지 + 정확한 색상 지정 + 위치 고정.
"""

import logging
import random

from agents.tools.image_tools import _call_gemini_image

logger = logging.getLogger(__name__)

# ─── 색상 카탈로그 (Codex 기반, HEX 포함) ───

COLOR_CATALOG = {
    "base": [
        {"name_ko": "샌드그레이", "name_en": "sand-gray", "hex": "#d8d2c8", "prompt": "soft sand gray painted flat panel finish"},
        {"name_ko": "라이트그레이", "name_en": "light-gray", "hex": "#d9dde2", "prompt": "light gray painted flat panel finish"},
        {"name_ko": "포그그레이", "name_en": "fog-gray", "hex": "#cfd4d1", "prompt": "fog gray painted flat panel finish"},
        {"name_ko": "캐시미어", "name_en": "cashmere", "hex": "#d8d0c5", "prompt": "cashmere painted flat panel finish"},
        {"name_ko": "화이트", "name_en": "white", "hex": "#f5f5f3", "prompt": "pure white smooth matte flat panel finish"},
        {"name_ko": "밀크화이트", "name_en": "milk-white", "hex": "#f3efe8", "prompt": "milk white painted flat panel finish"},
        {"name_ko": "듀이클라우드", "name_en": "dewy-cloud", "hex": "#e7e9ea", "prompt": "dewy cloud painted flat panel finish"},
    ],
    "countertop": [
        {"name_ko": "화이트세라믹", "hex": "#efede8", "prompt": "bright white ceramic countertop with subtle stone movement"},
        {"name_ko": "소프트그레이", "hex": "#dadbdd", "prompt": "soft light gray ceramic countertop with refined matte texture"},
        {"name_ko": "웜아이보리", "hex": "#ebe3d7", "prompt": "warm ivory engineered stone countertop"},
        {"name_ko": "프로스트화이트", "hex": "#f4f4f1", "prompt": "frost white solid surface countertop"},
    ],
    "alt_lower": [
        {"name_ko": "딥그린", "name_en": "deep-green", "hex": "#234235", "prompt": "deep green painted flat panel finish"},
        {"name_ko": "딥블루", "name_en": "deep-blue", "hex": "#1f3f6b", "prompt": "deep blue painted flat panel finish"},
        {"name_ko": "퍼플", "name_en": "purple", "hex": "#6b5876", "prompt": "muted purple painted flat panel finish"},
        {"name_ko": "브릭테라코타", "name_en": "brick-terracotta", "hex": "#9c5f49", "prompt": "brick terracotta painted flat panel finish"},
        {"name_ko": "네이처오크", "name_en": "nature-oak", "hex": "#c9ab7a", "prompt": "natural oak wood grain finish"},
        {"name_ko": "월넛", "name_en": "walnut", "hex": "#6a4a36", "prompt": "dark walnut wood grain finish"},
        {"name_ko": "콘크리트", "name_en": "concrete", "hex": "#9ea3a7", "prompt": "architectural concrete-texture cabinet finish"},
        {"name_ko": "웜토프", "name_en": "warm-taupe", "hex": "#8d7b70", "prompt": "warm taupe painted flat panel finish"},
    ],
}


def pick_color_scheme(seed: str | None = None) -> dict:
    """기본 이미지용 색상 선택 (상부/하부 동일 무채색 + 상판)."""
    rng = random.Random(seed) if seed else random
    door = rng.choice(COLOR_CATALOG["base"])
    ct = rng.choice(COLOR_CATALOG["countertop"])
    return {"upper": door, "lower": door, "countertop": ct}


def pick_alt_color_scheme(seed: str | None = None) -> dict:
    """대체 스타일용 색상 (하부 컬러 + 상부 무채색 + 상판)."""
    rng = random.Random(f"alt-{seed}") if seed else random
    lower = rng.choice(COLOR_CATALOG["alt_lower"])
    upper = rng.choice(COLOR_CATALOG["base"])
    ct = rng.choice(COLOR_CATALOG["countertop"])
    return {"upper": upper, "lower": lower, "countertop": ct}


# ─── 프롬프트 빌더 (Codex 태그 기반) ───

def _build_closed_prompt(
    colors: dict,
    module_desc: str = "",
    is_construction: bool = True,
) -> str:
    upper = colors["upper"]
    lower = colors["lower"]
    ct = colors["countertop"]

    construction_block = (
        "If the room is under construction: "
        "replace cement floor with wood laminate, "
        "wallpaper exposed ceiling and bare wood, "
        "fill ceiling holes with recessed LED downlights. "
    ) if is_construction else ""

    return (
        f"[EDIT MODE] Edit the first image. Do not generate a new scene.\n"
        f"[COMPOSITE] Photoreal furniture compositing task, not room redesign.\n"
        f"[PLATE LOCK] Keep exact same camera, lens, perspective, crop, horizon.\n"
        f"[BACKGROUND LOCK] Do not alter non-cabinet pixels except tiny contact shadows.\n"
        f"[WALL TILE LOCK] Keep backsplash and wall tile identical to original: "
        f"tile size, pattern, grout lines, grout color, gloss/matte finish, reflections.\n"
        f"[DECLUTTER] Remove people, tools, debris, dishes, countertop clutter. Clean countertop and floor.\n"
        f"{construction_block}"
        f"[UPPER COLOR] {upper['prompt']}, exact HEX {upper['hex']}\n"
        f"[LOWER COLOR] {lower['prompt']}, exact HEX {lower['hex']}\n"
        f"[COUNTERTOP] {ct['prompt']}\n"
        f"[STYLE] Handleless flat panel doors with finger groove along top edge.\n"
        f"Upper cabinets flush ceiling, lower cabinets with countertop, full wall edge-to-edge.\n"
        f"[COOKTOP] Flush-mounted built-in, 2-drawer base below.\n"
        f"[APPLIANCES] Replace sink bowl, faucet, cooktop, hood with brand new ones.\n"
        f"[EXISTING] If refrigerator visible, preserve it exactly.\n"
        f"{module_desc}\n"
        f"2nd image = 3D layout guide, copy positions exactly."
    )


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
    design_seed: str | None = None,
) -> str:
    """Generate closed-door furniture image using 3D render + tag-based prompt."""
    extra = [render_b64]

    colors = pick_color_scheme(seed=design_seed)
    logger.info("Color scheme: upper=%s, lower=%s, countertop=%s",
                colors["upper"]["name_ko"], colors["lower"]["name_ko"], colors["countertop"]["name_ko"])

    prompt = _build_closed_prompt(colors, module_desc)

    if len(prompt) > 1500:
        prompt = prompt[:1497] + "..."

    logger.info("Closed-door prompt (%d chars): %s", len(prompt), prompt[:200])

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
    """Generate open-door image (preserved for backward compat, currently unused)."""
    extra = [render_b64]
    prompt = (
        f"Edit this photo: open all cabinet doors 90deg outward, pull drawers 40% forward. "
        f"2nd image = open layout guide. Inside: {open_contents}. "
        f"[PLATE LOCK] Keep exact same camera, perspective, background.\n"
        f"[WALL TILE LOCK] Keep wall tile identical.\n"
        f"Keep cabinet structure, color, countertop, sink, cooktop positions."
    )
    result_b64 = await _call_gemini_image(prompt, furniture_b64, extra_images=extra)
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
    return await generate_closed_door(
        original_b64, render_b64, style, category,
        reference_images=reference_images, wall_width=wall_width,
    )
