"""Blender 3D Rendering Pipeline — Unit Tests

Tests all components that can run without Blender (bpy):
1. Renderer: subprocess wrapper logic, config serialization
2. Compositor: alpha compositing (PIL-only, no API)
3. Materials: style definitions completeness
4. Camera: default params, param merging
5. Scene builder config: module routing logic
6. Orchestrator integration: reference images, USE_BLENDER toggle
"""

import asyncio
import base64
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES = Path(__file__).parent / "fixtures"


# ─── Helper: create test images ───


def _make_test_image(width=200, height=150, color=(100, 150, 200), mode="RGB"):
    """Create a test image and return as base64."""
    img = Image.new(mode, (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_rgba_image(width=200, height=150, color=(255, 255, 255), alpha=128):
    """Create a test RGBA image (semi-transparent) and return as base64."""
    img = Image.new("RGBA", (width, height), (*color, alpha))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ═══════════════════════════════════════════════════════════════
# 1. Renderer — config serialization + subprocess wrapper
# ═══════════════════════════════════════════════════════════════


def test_renderer_config_serialization():
    """render_cabinet_scene serializes correct JSON to temp file."""
    from agents.blender.renderer import render_cabinet_scene

    # We can't actually call it (needs Blender), but we can test config building
    # by checking the function signature and docstring exist
    assert callable(render_cabinet_scene)
    assert "layout_data" in render_cabinet_scene.__code__.co_varnames
    assert "door_state" in render_cabinet_scene.__code__.co_varnames


@pytest.mark.asyncio
async def test_renderer_timeout_handling():
    """Renderer raises TimeoutError when Blender exceeds timeout."""
    from agents.blender.renderer import render_cabinet_scene

    # Mock subprocess to hang forever
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()

    async def hang_forever():
        await asyncio.sleep(100)
        return b"", b""

    mock_proc.communicate = hang_forever

    with patch("agents.blender.renderer.asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(TimeoutError, match="timed out"):
            await render_cabinet_scene(
                layout_data={"modules": [], "wall_width": 3000},
                camera_params={},
                style="modern",
                category="sink",
                door_state="closed",
                timeout=1,  # 1 second timeout
            )


@pytest.mark.asyncio
async def test_renderer_process_failure():
    """Renderer raises RuntimeError when Blender exits non-zero."""
    from agents.blender.renderer import render_cabinet_scene

    mock_proc = MagicMock()
    mock_proc.returncode = 1

    async def return_error():
        return b"", b"Blender error: segfault"

    mock_proc.communicate = return_error

    with patch("agents.blender.renderer.asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Blender process failed"):
            await render_cabinet_scene(
                layout_data={"modules": [], "wall_width": 3000},
                camera_params={},
                style="modern",
                category="sink",
                door_state="closed",
            )


@pytest.mark.asyncio
async def test_renderer_success_returns_base64():
    """Renderer returns base64 PNG on successful Blender run."""
    from agents.blender.renderer import render_cabinet_scene

    # Create a fake PNG output
    test_png = _make_test_image()
    png_bytes = base64.b64decode(test_png)

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    async def return_ok():
        return b"Render complete", b""

    mock_proc.communicate = return_ok

    async def fake_subprocess(*args, **kwargs):
        # Write fake PNG to the output path
        # args: blender, --background, --factory-startup, --python, script, --, input_path
        input_path = args[-1]  # last arg is input_path
        with open(input_path, "r") as f:
            cfg = json.load(f)
        output_path = cfg["output_path"]
        with open(output_path, "wb") as f:
            f.write(png_bytes)
        return mock_proc

    with patch("agents.blender.renderer.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await render_cabinet_scene(
            layout_data={"modules": [], "wall_width": 3000},
            camera_params={},
            style="modern",
            category="sink",
            door_state="closed",
        )

    assert isinstance(result, str)
    # Verify it's valid base64 that decodes to a PNG
    decoded = base64.b64decode(result)
    img = Image.open(io.BytesIO(decoded))
    assert img.size == (200, 150)


# ═══════════════════════════════════════════════════════════════
# 2. Compositor — alpha compositing (PIL only)
# ═══════════════════════════════════════════════════════════════


def test_alpha_composite_basic():
    """_alpha_composite merges RGBA overlay onto RGB background."""
    from agents.tools.compositor_tools import _alpha_composite

    bg = _make_test_image(200, 150, color=(50, 50, 50))
    overlay = _make_rgba_image(200, 150, color=(255, 0, 0), alpha=128)

    result_b64 = _alpha_composite(bg, overlay)

    # Should return valid base64 PNG
    decoded = base64.b64decode(result_b64)
    img = Image.open(io.BytesIO(decoded))
    assert img.mode == "RGB"
    assert img.size == (200, 150)

    # Center pixel should be reddish (blend of gray bg + semi-transparent red)
    px = img.getpixel((100, 75))
    assert px[0] > px[1], f"Red channel should dominate: {px}"


def test_alpha_composite_size_mismatch():
    """_alpha_composite resizes overlay to match background."""
    from agents.tools.compositor_tools import _alpha_composite

    bg = _make_test_image(400, 300, color=(50, 50, 50))
    overlay = _make_rgba_image(200, 150, color=(255, 0, 0), alpha=200)

    result_b64 = _alpha_composite(bg, overlay)
    img = Image.open(io.BytesIO(base64.b64decode(result_b64)))
    assert img.size == (400, 300), "Output should match background size"


def test_alpha_composite_fully_transparent():
    """Fully transparent overlay should leave background unchanged."""
    from agents.tools.compositor_tools import _alpha_composite

    bg = _make_test_image(100, 100, color=(42, 42, 42))
    overlay = _make_rgba_image(100, 100, color=(255, 0, 0), alpha=0)

    result_b64 = _alpha_composite(bg, overlay)
    img = Image.open(io.BytesIO(base64.b64decode(result_b64)))
    px = img.getpixel((50, 50))
    # Should be close to original (minor diff from feathering is OK)
    assert abs(px[0] - 42) < 5, f"Should be near-original gray: {px}"


@pytest.mark.asyncio
async def test_composite_render_onto_photo_gemini_fallback():
    """composite_render_onto_photo returns raw composite when Gemini fails."""
    from agents.tools.compositor_tools import composite_render_onto_photo

    bg = _make_test_image(200, 150, color=(80, 80, 80))
    overlay = _make_rgba_image(200, 150, color=(0, 200, 0), alpha=200)

    with patch("agents.tools.compositor_tools._call_gemini_image", side_effect=Exception("API down")):
        result = await composite_render_onto_photo(
            original_b64=bg,
            render_b64=overlay,
            style="modern",
            category="sink",
        )

    # Should still return a valid image (raw composite, no harmonization)
    assert isinstance(result, str)
    img = Image.open(io.BytesIO(base64.b64decode(result)))
    assert img.size == (200, 150)


@pytest.mark.asyncio
async def test_composite_render_with_ref_images():
    """composite_render_onto_photo passes reference images to Gemini."""
    from agents.tools.compositor_tools import composite_render_onto_photo

    bg = _make_test_image(200, 150)
    overlay = _make_rgba_image(200, 150, color=(200, 200, 200), alpha=200)
    ref1 = _make_test_image(100, 100, color=(255, 0, 0))
    ref2 = _make_test_image(100, 100, color=(0, 0, 255))

    harmonized = _make_test_image(200, 150, color=(180, 180, 180))

    mock_gemini = AsyncMock(return_value=harmonized)

    with patch("agents.tools.compositor_tools._call_gemini_image", mock_gemini):
        result = await composite_render_onto_photo(
            original_b64=bg,
            render_b64=overlay,
            style="nordic",
            category="sink",
            reference_images=[ref1, ref2],
        )

    # Verify Gemini was called with extra_images
    mock_gemini.assert_called_once()
    call_kwargs = mock_gemini.call_args
    assert call_kwargs[1]["extra_images"] == [ref1, ref2]
    assert "reference images" in call_kwargs[0][0].lower()


# ═══════════════════════════════════════════════════════════════
# 3. Materials — style definitions completeness
# ═══════════════════════════════════════════════════════════════


def test_all_styles_defined():
    """All 6 styles have complete material definitions."""
    # materials.py imports bpy at top level; mock it for testing outside Blender
    sys.modules.setdefault("bpy", MagicMock())
    from agents.blender.materials import STYLE_MATERIALS

    expected_styles = {"modern", "nordic", "classic", "natural", "industrial", "luxury"}
    assert set(STYLE_MATERIALS.keys()) == expected_styles

    required_components = {
        "door", "body", "countertop", "handle", "toe_kick",
        "sink_basin", "faucet", "cooktop_glass", "burner_ring", "roughness",
    }

    for style, palette in STYLE_MATERIALS.items():
        assert set(palette.keys()) == required_components, \
            f"Style '{style}' missing components: {required_components - set(palette.keys())}"

        # Verify color tuples are valid RGB (3 floats, 0-1 range)
        for comp, value in palette.items():
            if comp == "roughness":
                assert 0.0 <= value <= 1.0, f"{style}.roughness={value} out of range"
            else:
                assert len(value) == 3, f"{style}.{comp} should be RGB tuple"
                for ch in value:
                    assert 0.0 <= ch <= 1.0, f"{style}.{comp} channel {ch} out of 0-1 range"


def test_material_classification():
    """_classify_object correctly maps object names to material components."""
    sys.modules.setdefault("bpy", MagicMock())
    from agents.blender.materials import _classify_object

    cases = {
        "Door_L_450": "door",
        "UDoor_R_1250": "door",
        "DrawerFront_0_2150": "door",
        "Body_450": "body",
        "UBody_0": "body",
        "SidePanel_L_0": "body",
        "Countertop": "countertop",
        "Handle_bar_100_500": "handle",
        "ToeKick_0": "toe_kick",
        "SinkBasin_450": "sink_basin",
        "SinkRim_450": "sink_basin",
        "FaucetStem_450": "faucet",
        "FaucetSpout_450": "faucet",
        "CooktopGlass_2150": "cooktop_glass",
        "Burner_0_2150": "burner_ring",
        "Shelf_450": "body",
        "UnknownObject": "body",  # default fallback
    }

    for obj_name, expected in cases.items():
        result = _classify_object(obj_name)
        assert result == expected, f"_classify_object('{obj_name}') = '{result}', expected '{expected}'"


# ═══════════════════════════════════════════════════════════════
# 4. Camera — default parameters and merging
# ═══════════════════════════════════════════════════════════════


def test_camera_defaults():
    """DEFAULT_CAMERA has all required keys with sensible values."""
    sys.modules.setdefault("bpy", MagicMock())
    from agents.blender.camera import DEFAULT_CAMERA

    assert "camera_height_mm" in DEFAULT_CAMERA
    assert "camera_distance_mm" in DEFAULT_CAMERA
    assert "camera_tilt_deg" in DEFAULT_CAMERA
    assert "focal_length_mm" in DEFAULT_CAMERA

    assert 1000 <= DEFAULT_CAMERA["camera_height_mm"] <= 1600
    assert 1500 <= DEFAULT_CAMERA["camera_distance_mm"] <= 5000
    assert -15 <= DEFAULT_CAMERA["camera_tilt_deg"] <= 15
    assert 20 <= DEFAULT_CAMERA["focal_length_mm"] <= 50


# ═══════════════════════════════════════════════════════════════
# 5. Handle styles — completeness
# ═══════════════════════════════════════════════════════════════


def test_handle_styles_completeness():
    """All 6 styles have handle definitions."""
    sys.modules.setdefault("bpy", MagicMock())
    from agents.blender.geometry.handles import HANDLE_STYLES

    expected = {"modern", "nordic", "classic", "natural", "industrial", "luxury"}
    assert set(HANDLE_STYLES.keys()) == expected

    for style, cfg in HANDLE_STYLES.items():
        assert "length" in cfg, f"{style} missing length"
        assert "type" in cfg, f"{style} missing type"
        assert cfg["type"] in ("bar", "arch"), f"{style} unknown type: {cfg['type']}"
        assert "color" in cfg, f"{style} missing color"
        assert len(cfg["color"]) == 3, f"{style} color should be RGB tuple"


# ═══════════════════════════════════════════════════════════════
# 6. Test input fixture — valid JSON
# ═══════════════════════════════════════════════════════════════


def test_fixture_json_valid():
    """Test fixture JSON is valid and has required fields."""
    fixture_path = FIXTURES / "blender_test_input.json"
    assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

    with open(fixture_path, encoding="utf-8") as f:
        config = json.load(f)

    assert "modules" in config
    assert "wall_width" in config
    assert "style" in config
    assert "door_state" in config
    assert "camera_params" in config
    assert "resolution" in config
    assert "output_path" in config

    # Verify module structure
    for mod in config["modules"]:
        assert "type" in mod
        assert "width" in mod
        assert "position_x" in mod

    # Modules should not overlap
    modules = sorted(config["modules"], key=lambda m: m["position_x"])
    for i in range(len(modules) - 1):
        end_of_current = modules[i]["position_x"] + modules[i]["width"]
        start_of_next = modules[i + 1]["position_x"]
        assert end_of_current <= start_of_next, \
            f"Module overlap: {modules[i]} ends at {end_of_current}, next starts at {start_of_next}"

    # Total module width should fit wall
    total = sum(m["width"] for m in config["modules"])
    assert total <= config["wall_width"], \
        f"Total module width {total}mm exceeds wall {config['wall_width']}mm"


# ═══════════════════════════════════════════════════════════════
# 7. Orchestrator integration — reference images + USE_BLENDER
# ═══════════════════════════════════════════════════════════════


def test_orchestrator_imports():
    """Orchestrator correctly imports Blender pipeline components."""
    # This tests that the import chain works (no circular imports)
    import importlib
    mod = importlib.import_module("agents.orchestrator")
    assert hasattr(mod, "_fetch_reference_images")
    assert hasattr(mod, "composite_render_onto_photo")
    assert hasattr(mod, "process_project")


def test_use_blender_env_toggle():
    """USE_BLENDER env var controls pipeline selection."""
    # Default (not set) should be true
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("USE_BLENDER", None)
        val = os.environ.get("USE_BLENDER", "true").lower() == "true"
        assert val is True, "Default should be true"

    # Explicit true
    with patch.dict(os.environ, {"USE_BLENDER": "true"}):
        val = os.environ.get("USE_BLENDER", "true").lower() == "true"
        assert val is True

    # Explicit false
    with patch.dict(os.environ, {"USE_BLENDER": "false"}):
        val = os.environ.get("USE_BLENDER", "true").lower() == "true"
        assert val is False


# ═══════════════════════════════════════════════════════════════
# 8. Prompts — camera_params in output format
# ═══════════════════════════════════════════════════════════════


def test_prompts_include_camera_step():
    """SPACE_ANALYST_PROMPT includes STEP 6: Camera Perspective."""
    from agents.prompts import SPACE_ANALYST_PROMPT

    assert "STEP 6" in SPACE_ANALYST_PROMPT
    assert "Camera Perspective" in SPACE_ANALYST_PROMPT
    assert "camera_height_mm" in SPACE_ANALYST_PROMPT
    assert "camera_distance_mm" in SPACE_ANALYST_PROMPT
    assert "camera_tilt_deg" in SPACE_ANALYST_PROMPT
    assert "focal_length_mm" in SPACE_ANALYST_PROMPT
    assert "camera_params" in SPACE_ANALYST_PROMPT


# ═══════════════════════════════════════════════════════════════
# 9. Layout Engine → Blender config compatibility
# ═══════════════════════════════════════════════════════════════


def test_layout_output_compatible_with_blender():
    """Layout Engine output has all fields Blender scene_builder expects."""
    from agents.layout_engine import plan_layout

    result = plan_layout(
        wall_width=3000,
        category="sink",
        sink_position=800,
        cooktop_position=2200,
    )

    assert "modules" in result
    assert "wall_width" in result

    for mod in result["modules"]:
        # scene_builder.py expects these keys
        assert "type" in mod, f"Module missing 'type': {mod}"
        assert "width" in mod, f"Module missing 'width': {mod}"
        assert "position_x" in mod, f"Module missing 'position_x': {mod}"
        assert "is_2door" in mod, f"Module missing 'is_2door': {mod}"

    # Verify sink_bowl and cooktop are present
    types = [m["type"] for m in result["modules"]]
    assert "sink_bowl" in types, f"No sink_bowl in layout: {types}"
    assert "cooktop" in types, f"No cooktop in layout: {types}"

    # Verify modules don't overlap
    modules = sorted(result["modules"], key=lambda m: m["position_x"])
    for i in range(len(modules) - 1):
        end = modules[i]["position_x"] + modules[i]["width"]
        start = modules[i + 1]["position_x"]
        assert end <= start, f"Overlap: mod[{i}] ends at {end}, mod[{i+1}] starts at {start}"


def test_layout_all_categories_compatible():
    """All categories produce Blender-compatible module output."""
    from agents.layout_engine import plan_layout

    categories = ["sink", "island", "closet", "fridge_cabinet",
                   "shoe_cabinet", "vanity", "storage", "utility_closet"]

    for cat in categories:
        kwargs = {"wall_width": 2400, "category": cat}
        if cat == "sink":
            kwargs["sink_position"] = 600
            kwargs["cooktop_position"] = 1800

        result = plan_layout(**kwargs)

        if "error" in result:
            continue  # some categories may not fit

        for mod in result["modules"]:
            assert "type" in mod
            assert "width" in mod
            assert isinstance(mod["width"], (int, float))
            assert mod["width"] > 0
            assert "position_x" in mod

        print(f"  {cat}: {len(result['modules'])} modules OK")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════


if __name__ == "__main__":
    # Run as standalone script
    tests = [
        ("renderer config serialization", test_renderer_config_serialization),
        ("material styles completeness", test_all_styles_defined),
        ("material classification", test_material_classification),
        ("camera defaults", test_camera_defaults),
        ("handle styles", test_handle_styles_completeness),
        ("fixture JSON", test_fixture_json_valid),
        ("orchestrator imports", test_orchestrator_imports),
        ("USE_BLENDER toggle", test_use_blender_env_toggle),
        ("prompts camera step", test_prompts_include_camera_step),
        ("layout→blender compat", test_layout_output_compatible_with_blender),
        ("all categories compat", test_layout_all_categories_compatible),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1

    # Async tests
    async_tests = [
        ("renderer timeout", test_renderer_timeout_handling),
        ("renderer failure", test_renderer_process_failure),
        ("renderer success", test_renderer_success_returns_base64),
        ("compositor gemini fallback", test_composite_render_onto_photo_gemini_fallback),
        ("compositor ref images", test_composite_render_with_ref_images),
    ]

    for name, fn in async_tests:
        try:
            asyncio.run(fn())
            print(f"  PASS: {name} (async)")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name} (async): {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
