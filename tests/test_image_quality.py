"""이미지 생성 품질 검증 — Gemini API 10회 테스트

테스트 이미지로 Gemini 이미지 생성을 10회 반복 실행하고,
결과를 db/testimage/results/ 에 저장합니다.
"""

import asyncio
import base64
import json
import os
import sys
import time

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.layout_engine import plan_layout
from agents.tools.image_tools import (
    _call_flux_canny_pro,
    _call_gemini_image,
    _composite_inpaint_result,
    _create_furniture_mask,
    cleanup_photo,
)

# ─── 설정 ───
TEST_IMAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "testimage", "KakaoTalk_20260206_063235558.jpg",
)
OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "resultimage",
)
NUM_TESTS = 100
WALL_WIDTH = 3200  # mm (테스트용 벽 폭)
CATEGORY = "sink"
STYLE = "modern"


def build_prompt(wall_width: int, category: str, style: str) -> str:
    """orchestrator.py와 동일한 방식으로 프롬프트 생성."""
    layout_data = plan_layout(
        wall_width=wall_width,
        category=category,
        sink_position=int(wall_width * 0.25),
        cooktop_position=int(wall_width * 0.75),
    )
    modules = layout_data.get("modules", [])

    # module_desc 생성 (orchestrator.py와 동일)
    module_parts = []
    for m in modules:
        mtype = m.get("type", "cabinet")
        mw = m.get("width", 600)
        mx = m.get("position_x", 0)
        pct = int(mx / wall_width * 100) if wall_width > 0 else 0
        if mtype == "sink_bowl":
            module_parts.append(f"sink-bowl({mw}mm, at {pct}%)")
        elif mtype == "cooktop":
            module_parts.append(
                f"cooktop({mw}mm, at {pct}%)+3-DRAWERS-below(NOT oven, NOT open)"
            )
        elif m.get("is_2door"):
            module_parts.append(f"2-door-cabinet({mw}mm, at {pct}%)")
        else:
            module_parts.append(f"1-door-cabinet({mw}mm, at {pct}%)")

    module_desc = (
        f"{len(modules)} lower cabinets spanning {wall_width}mm, left to right: "
        f"[{' | '.join(module_parts)}]. "
        f"Every module MUST have a door or drawer front — NO open/empty sections."
    )

    # layout_desc
    layout_desc = (
        "STRAIGHT single-wall layout ONLY. All cabinets in a flat line on ONE wall. "
        "NO L-shape, NO corner wrapping, NO side-wall cabinets. "
    )

    style_short = {
        "modern": "white flat-panel",
        "nordic": "light wood grain",
        "classic": "warm brown wood panel",
        "natural": "natural wood matte",
        "industrial": "dark charcoal matte",
        "luxury": "high-gloss pearl white",
    }.get(style, "white flat-panel")

    wall_fill = (
        f"Cabinets MUST span the ENTIRE wall width ({wall_width}mm) from left edge to right edge. "
        f"NO gaps on left or right side. "
    )

    # placement_note
    sink_pct = 25
    cooktop_pct = 75
    placement_note = (
        f"Stainless steel sink bowl EXACTLY at {sink_pct}% from left (water pipe position). "
        f"cooktop at {cooktop_pct}% from left with 2 horizontal pull-out DRAWERS with handles below "
        f"(NOT oven, NOT open shelf, NOT empty cavity). "
        "No tall cabinets. "
    )

    prompt = (
        f"Remove ALL people, clothes, tools, debris, objects ON the floor from this photo. "
        f"Then install {layout_desc}{style_short} {CATEGORY}. "
        f"Upper wall cabinets flush with ceiling. Lower base cabinets with countertop. "
        f"{wall_fill}"
        f"Lower cabinet layout: {module_desc} "
        f"{placement_note}"
        f"PRESERVE original wall tiles, tile color, tile pattern, ceiling EXACTLY. "
        f"Clean bare floor. Photorealistic."
    )

    return prompt, layout_data


async def run_single_test(test_num: int, image_b64: str, prompt: str, flux_prompt: str) -> dict:
    """단일 테스트: Gemini 생성 → FLUX Canny-Pro 구조 강제 → 원본 합성."""
    start = time.time()
    result = {
        "test_num": test_num,
        "status": "unknown",
        "elapsed_sec": 0,
        "error": None,
        "output_file": None,
        "prompt_length": len(prompt),
        "pipeline": "unknown",
    }

    try:
        # Step 1: Gemini cleanup — 사람/잡동사니 제거
        clean_b64 = await cleanup_photo(image_b64)
        t1 = time.time() - start
        print(f"  Test {test_num:2d}: Cleanup OK ({t1:.1f}s)", end="")

        # Step 2: Gemini로 가구 이미지 생성 (FLUX control_image용)
        gemini_b64 = await _call_gemini_image(prompt, clean_b64)
        t2 = time.time() - start
        print(f" → Gemini OK ({t2:.1f}s)", end="")

        # Step 3: FLUX Canny-Pro — Gemini 결과를 control_image로 구조 강제
        try:
            flux_b64 = await _call_flux_canny_pro(
                prompt=flux_prompt,
                control_image_b64=gemini_b64,
                guidance=30,
                steps=28,
            )
            t3 = time.time() - start
            print(f" → FLUX OK ({t3:.1f}s)", end="")

            # Step 4: clean 원본 위에 합성 (마스크 밖 = 깨끗한 원본)
            mask_b64 = _create_furniture_mask(clean_b64, CATEGORY, None)
            result_b64 = _composite_inpaint_result(clean_b64, flux_b64, mask_b64)
            result["pipeline"] = "cleanup+gemini+flux+composite"

        except Exception as flux_err:
            print(f" → FLUX failed ({flux_err}), using Gemini", end="")
            result_b64 = gemini_b64
            result["pipeline"] = "cleanup+gemini-fallback"

        elapsed = time.time() - start
        result["elapsed_sec"] = round(elapsed, 1)
        result["status"] = "success"

        # 결과 이미지 저장
        output_file = os.path.join(OUTPUT_DIR, f"test_{test_num:02d}.png")
        with open(output_file, "wb") as f:
            f.write(base64.b64decode(result_b64))
        result["output_file"] = output_file

        print(f" → Total ({elapsed:.1f}s) -> {output_file}")

    except Exception as e:
        elapsed = time.time() - start
        result["elapsed_sec"] = round(elapsed, 1)
        result["status"] = "failed"
        result["error"] = str(e)
        print(f"  Test {test_num:2d}: FAILED ({elapsed:.1f}s) - {e}")

    return result


async def main():
    print("=" * 60)
    print("이미지 생성 품질 검증 테스트")
    print("=" * 60)

    # 출력 디렉토리 생성
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 테스트 이미지 로드
    with open(TEST_IMAGE_PATH, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    print(f"Input image: {TEST_IMAGE_PATH}")

    # 프롬프트 생성
    prompt, layout_data = build_prompt(WALL_WIDTH, CATEGORY, STYLE)
    print(f"\nPrompt ({len(prompt)} chars):")
    print(f"  {prompt[:200]}...")
    print(f"\nLayout: {len(layout_data['modules'])} modules, "
          f"total_width={layout_data['total_module_width']}mm, "
          f"remainder={layout_data['remainder_mm']}mm")
    for m in layout_data["modules"]:
        print(f"  {m['type']:12s} {m['width']:4d}mm  pos_x={m['position_x']:4d}mm")

    # FLUX 프롬프트 (구조 강제용 — 짧고 시각적)
    flux_prompt = (
        f"Photorealistic Korean apartment kitchen interior. "
        f"white flat-panel cabinets. "
        f"Upper wall cabinets flush with ceiling. Lower base cabinets with countertop. "
        f"Stainless steel sink bowl with faucet. Induction cooktop with 2 pull-out drawers below. "
        f"Red/burgundy wall tiles as backsplash. "
        f"Clean bare floor. Natural interior lighting."
    )

    # 프롬프트 저장
    prompt_file = os.path.join(OUTPUT_DIR, "prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(f"=== Gemini Prompt ===\n{prompt}\n\n=== FLUX Prompt ===\n{flux_prompt}")
    print(f"\nPrompt saved: {prompt_file}")

    # 이전 실패 결과가 있으면 해당 번호만 재시도
    summary_file = os.path.join(OUTPUT_DIR, "test_summary.json")
    retry_nums = None
    if os.path.exists(summary_file):
        with open(summary_file, encoding="utf-8") as sf:
            prev = json.load(sf)
        failed_nums = [r["test_num"] for r in prev.get("results", []) if r["status"] == "failed"]
        if failed_nums:
            retry_nums = failed_nums
            print(f"\n--- Retrying {len(failed_nums)} failed tests ---")

    test_nums = retry_nums if retry_nums else list(range(1, NUM_TESTS + 1))
    print(f"\n--- Running {len(test_nums)} tests (Cleanup → Gemini → FLUX Canny → Composite) ---")
    results = []

    # 이전 성공 결과 보존
    prev_success = {}
    if retry_nums and os.path.exists(summary_file):
        for r in prev.get("results", []):
            if r["status"] == "success":
                prev_success[r["test_num"]] = r

    for i in test_nums:
        result = await run_single_test(i, image_b64, prompt, flux_prompt)
        results.append(result)

    # 이전 성공 + 새 결과 병합
    if prev_success:
        all_results = list(prev_success.values()) + results
        all_results.sort(key=lambda r: r["test_num"])
        results = all_results

    # 결과 요약
    success_count = sum(1 for r in results if r["status"] == "success")
    fail_count = sum(1 for r in results if r["status"] == "failed")
    avg_time = (
        sum(r["elapsed_sec"] for r in results if r["status"] == "success") / success_count
        if success_count > 0 else 0
    )

    summary = {
        "total_tests": NUM_TESTS,
        "success": success_count,
        "failed": fail_count,
        "avg_time_sec": round(avg_time, 1),
        "prompt_length": len(prompt),
        "wall_width_mm": WALL_WIDTH,
        "category": CATEGORY,
        "style": STYLE,
        "layout_modules": layout_data["modules"],
        "results": results,
    }

    # 결과 JSON 저장
    summary_file = os.path.join(OUTPUT_DIR, "test_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Results: {success_count}/{NUM_TESTS} success, {fail_count} failed")
    print(f"Avg time: {avg_time:.1f}s per image")
    print(f"Summary: {summary_file}")
    print(f"Images:  {OUTPUT_DIR}/test_*.png")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
