"""이미지 생성 MCP 도구 - Gemini + Flux LoRA"""

import base64
import json
import os

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from shared.constants import LORA_MODELS

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
GEMINI_MODEL = "gemini-2.5-flash-preview-04-17"


async def _call_gemini_image(prompt: str, reference_image_b64: str | None = None) -> str:
    """Gemini API로 이미지 생성, base64 반환"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    parts = []
    if reference_image_b64:
        parts.append(
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": reference_image_b64,
                }
            }
        )
    parts.append({"text": prompt})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            params={"key": GOOGLE_API_KEY},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    # 이미지 파트 추출
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "inlineData" in part:
                return part["inlineData"]["data"]

    raise ValueError("Gemini 응답에 이미지가 없습니다")


async def _call_flux_lora(category: str, prompt: str) -> str:
    """Replicate Flux LoRA로 가구 이미지 생성, base64 반환"""
    lora_name = LORA_MODELS.get(category, category)
    model_owner = "dadamfurniture-source"
    trigger_word = f"DADAM_{lora_name.upper()}"

    full_prompt = f"{trigger_word} {prompt}"

    async with httpx.AsyncClient(timeout=180) as client:
        # 예측 생성
        resp = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"},
            json={
                "model": f"{model_owner}/{lora_name}",
                "input": {
                    "prompt": full_prompt,
                    "num_outputs": 1,
                    "guidance_scale": 3.5,
                    "num_inference_steps": 28,
                    "output_format": "png",
                },
            },
        )
        resp.raise_for_status()
        prediction = resp.json()

        # 완료 대기 (폴링)
        prediction_url = prediction["urls"]["get"]
        for _ in range(60):  # 최대 3분
            import asyncio

            await asyncio.sleep(3)
            status_resp = await client.get(
                prediction_url,
                headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"},
            )
            status_data = status_resp.json()

            if status_data["status"] == "succeeded":
                image_url = status_data["output"][0]
                # 이미지 다운로드 후 base64 변환
                img_resp = await client.get(image_url)
                return base64.b64encode(img_resp.content).decode()

            if status_data["status"] == "failed":
                raise ValueError(f"Flux LoRA 실패: {status_data.get('error')}")

    raise TimeoutError("Flux LoRA 타임아웃")


@tool(
    "generate_cleanup",
    "원본 사진에서 기존 가구/잡동사니를 제거한 깨끗한 공간 이미지를 생성합니다.",
    {
        "type": "object",
        "properties": {
            "original_image_b64": {"type": "string", "description": "원본 사진 base64"},
            "space_description": {"type": "string", "description": "공간 설명 (간략히)"},
        },
        "required": ["original_image_b64"],
    },
)
async def generate_cleanup(args: dict) -> dict:
    desc = args.get("space_description", "Korean apartment kitchen")
    prompt = f"Remove all furniture and objects. Show clean empty {desc}. Clean floor and walls only."

    result_b64 = await _call_gemini_image(prompt, args["original_image_b64"])
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"image_base64": result_b64, "type": "cleanup"}),
            }
        ]
    }


@tool(
    "generate_furniture",
    "Flux LoRA 모델로 해당 카테고리의 가구가 설치된 이미지를 생성합니다.",
    {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "가구 카테고리"},
            "style": {"type": "string", "description": "스타일 (modern, nordic 등)"},
            "layout_description": {"type": "string", "description": "배치 설명 (영문, 300자 이내)"},
            "cleanup_image_b64": {"type": "string", "description": "클린업 이미지 base64"},
        },
        "required": ["category", "layout_description"],
    },
)
async def generate_furniture(args: dict) -> dict:
    style = args.get("style", "modern")
    prompt = f"{style} style, {args['layout_description']}, photorealistic, interior photography"

    result_b64 = await _call_flux_lora(args["category"], prompt)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"image_base64": result_b64, "type": "furniture"}),
            }
        ]
    }


@tool(
    "generate_correction",
    "생성된 가구 이미지의 색상, 조명, 원근을 원본 공간에 맞게 보정합니다.",
    {
        "type": "object",
        "properties": {
            "furniture_image_b64": {"type": "string", "description": "가구 이미지 base64"},
            "correction_prompt": {"type": "string", "description": "보정 지시 (300자 이내)"},
        },
        "required": ["furniture_image_b64", "correction_prompt"],
    },
)
async def generate_correction(args: dict) -> dict:
    result_b64 = await _call_gemini_image(
        args["correction_prompt"], args["furniture_image_b64"]
    )
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"image_base64": result_b64, "type": "correction"}),
            }
        ]
    }


@tool(
    "generate_open",
    "가구의 수납 내부 구성 이미지를 생성합니다 (문 열린 상태).",
    {
        "type": "object",
        "properties": {
            "furniture_image_b64": {"type": "string", "description": "가구 이미지 base64"},
            "open_prompt": {"type": "string", "description": "내부 구성 설명 (300자 이내)"},
        },
        "required": ["furniture_image_b64", "open_prompt"],
    },
)
async def generate_open(args: dict) -> dict:
    result_b64 = await _call_gemini_image(
        args["open_prompt"], args["furniture_image_b64"]
    )
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"image_base64": result_b64, "type": "open"}),
            }
        ]
    }


# MCP 서버 생성
image_server = create_sdk_mcp_server(
    name="image",
    version="1.0.0",
    tools=[generate_cleanup, generate_furniture, generate_correction, generate_open],
)
