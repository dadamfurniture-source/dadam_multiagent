"""Async subprocess wrapper for Blender headless rendering.

Serializes scene config to JSON, invokes Blender with scene_builder.py,
and returns the rendered PNG as base64 (RGBA, transparent background).
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid

logger = logging.getLogger(__name__)

# Path to the scene builder script (runs inside Blender's Python)
SCENE_BUILDER_SCRIPT = os.path.join(os.path.dirname(__file__), "scene_builder.py")


def _add_position_labels(png_bytes: bytes, modules: list, wall_width: int) -> bytes:
    """3D 렌더 이미지에 SINK/COOKTOP 위치 라벨 오버레이.

    Gemini가 3D 가이드를 참조할 때 위치를 혼동하지 않도록 텍스트 라벨 추가.
    """
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        draw = ImageDraw.Draw(img)
        w, h = img.size

        # 폰트 (시스템 기본, 없으면 PIL 기본)
        try:
            font = ImageFont.truetype("arial.ttf", 28)
            font_sm = ImageFont.truetype("arial.ttf", 20)
        except Exception:
            font = ImageFont.load_default()
            font_sm = font

        for m in modules:
            mtype = m.get("type", "")
            mx = m.get("position_x", 0)
            mw = m.get("width", 600)
            if mtype not in ("sink_bowl", "cooktop"):
                continue

            # 모듈 중심의 이미지 X 좌표 (wall_width → 이미지 너비 비례)
            center_pct = (mx + mw / 2) / wall_width if wall_width > 0 else 0.5
            img_x = int(center_pct * w)
            # 상판 위 라벨 위치 (이미지 높이 45% 지점)
            img_y = int(h * 0.45)

            if mtype == "sink_bowl":
                label = "◀ SINK ▶"
                color = (30, 120, 255, 220)  # 파란색
                bg = (30, 120, 255, 80)
            else:
                label = "◀ COOKTOP ▶"
                color = (255, 80, 30, 220)  # 빨간색
                bg = (255, 80, 30, 80)

            # 배경 박스
            bbox = draw.textbbox((0, 0), label, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            pad = 6
            x1 = img_x - tw // 2 - pad
            y1 = img_y - th // 2 - pad
            x2 = img_x + tw // 2 + pad
            y2 = img_y + th // 2 + pad
            draw.rectangle([x1, y1, x2, y2], fill=bg)
            # 텍스트
            draw.text((img_x - tw // 2, img_y - th // 2), label, fill=color, font=font)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning("Label overlay failed: %s", e)
        return png_bytes  # 실패 시 원본 반환


async def render_cabinet_scene(
    layout_data: dict,
    camera_params: dict,
    style: str,
    category: str,
    door_state: str,
    resolution: tuple = (1024, 768),
    timeout: int = 30,
) -> str:
    """Render a cabinet scene via Blender headless.

    Args:
        layout_data: Layout Engine result (modules, wall_width, etc.)
        camera_params: Camera parameters from Vision analysis
        style: Style name (modern/nordic/classic/natural/industrial/luxury)
        category: Furniture category (sink/island/closet/etc.)
        door_state: "closed" or "open"
        resolution: Output image resolution (width, height)
        timeout: Max seconds to wait for Blender process

    Returns:
        Base64-encoded PNG with RGBA (transparent background)

    Raises:
        TimeoutError: If Blender process exceeds timeout
        RuntimeError: If Blender process fails
    """
    run_id = uuid.uuid4().hex[:8]
    input_path = os.path.join(tempfile.gettempdir(), f"scene_input_{run_id}.json")
    output_path = os.path.join(tempfile.gettempdir(), f"scene_output_{run_id}.png")

    # Build scene config
    scene_config = {
        "modules": layout_data.get("modules", []),
        "wall_width": layout_data.get("wall_width", 3000),
        "category": category,
        "style": style,
        "door_state": door_state,
        "camera_params": camera_params,
        "resolution": list(resolution),
        "output_path": output_path,
    }

    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(scene_config, f, ensure_ascii=False)

    logger.info(
        "Blender render: %s state=%s modules=%d [%s]",
        category,
        door_state,
        len(scene_config["modules"]),
        run_id,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "blender",
            "--background",
            "--factory-startup",
            "--python",
            SCENE_BUILDER_SCRIPT,
            "--",
            input_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")[-500:]
            logger.error("Blender failed (rc=%d): %s", proc.returncode, err_msg)
            raise RuntimeError(f"Blender process failed (rc={proc.returncode}): {err_msg}")

        if not os.path.exists(output_path):
            raise RuntimeError("Blender produced no output image")

        with open(output_path, "rb") as f:
            png_bytes = f.read()

        b64 = base64.b64encode(png_bytes).decode()
        logger.info("Blender render complete: %d bytes [%s]", len(png_bytes), run_id)
        return b64

    except asyncio.TimeoutError:
        logger.error("Blender timed out after %ds [%s]", timeout, run_id)
        if proc.returncode is None:
            proc.kill()
        raise TimeoutError(f"Blender render timed out ({timeout}s)")

    finally:
        # Cleanup temp files
        for path in (input_path, output_path):
            try:
                os.unlink(path)
            except OSError:
                pass
