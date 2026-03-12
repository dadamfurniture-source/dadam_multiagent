"""Image Generation MCP Tools — Gemini 2.5 Flash Image + Flux LoRA

Pipeline: Cleanup(Gemini) → Furniture(Flux LoRA) → Correction(Gemini) → Open(Gemini)
Prompts must be kept under 500 chars for Gemini reliability.
"""

import asyncio
import base64
import json

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from shared.config import settings
from shared.constants import LORA_MODELS

GOOGLE_API_KEY = settings.google_api_key
REPLICATE_API_TOKEN = settings.replicate_api_token

# Production-verified Gemini model for image generation
GEMINI_MODEL = "gemini-2.5-flash-image"


async def _call_gemini_image(prompt: str, reference_image_b64: str | None = None) -> str:
    """Call Gemini Image API. Returns base64-encoded image.

    IMPORTANT: Keep prompt under 500 chars for reliability.
    Longer prompts may cause IMAGE_OTHER errors in production.
    """
    if len(prompt) > 500:
        prompt = prompt[:497] + "..."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    parts = []
    if reference_image_b64:
        parts.append({
            "inlineData": {
                "mimeType": "image/png",
                "data": reference_image_b64,
            }
        })
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

    # Extract image from response
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "inlineData" in part:
                return part["inlineData"]["data"]

    # Check for safety/error blocks
    block_reason = data.get("promptFeedback", {}).get("blockReason")
    if block_reason:
        raise ValueError(f"Gemini blocked: {block_reason}")

    raise ValueError("No image in Gemini response")


async def _call_flux_lora(category: str, prompt: str) -> str:
    """Call Replicate Flux LoRA for furniture image generation. Returns base64."""
    lora_name = LORA_MODELS.get(category, category)
    model_owner = "dadamfurniture-source"
    trigger_word = f"DADAM_{lora_name.upper()}"

    full_prompt = f"{trigger_word} {prompt}"

    headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}"}

    async with httpx.AsyncClient(timeout=180) as client:
        # Create prediction
        resp = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers=headers,
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

        # Poll for completion (max 3 minutes)
        prediction_url = prediction["urls"]["get"]
        for _ in range(60):
            await asyncio.sleep(3)
            status_resp = await client.get(prediction_url, headers=headers)
            status_data = status_resp.json()

            if status_data["status"] == "succeeded":
                image_url = status_data["output"][0]
                img_resp = await client.get(image_url)
                return base64.b64encode(img_resp.content).decode()

            if status_data["status"] == "failed":
                raise ValueError(f"Flux LoRA failed: {status_data.get('error')}")

    raise TimeoutError("Flux LoRA prediction timed out (3min)")


# =============================================================================
# MCP Tools — All descriptions in English for agent consumption
# =============================================================================


@tool(
    "generate_cleanup",
    "Remove existing furniture/clutter from original photo, producing a clean empty space image.",
    {
        "type": "object",
        "properties": {
            "original_image_b64": {
                "type": "string",
                "description": "Original site photo as base64",
            },
            "space_description": {
                "type": "string",
                "description": "Brief space description (e.g. 'Korean apartment kitchen with tile backsplash')",
            },
        },
        "required": ["original_image_b64"],
    },
)
async def generate_cleanup(args: dict) -> dict:
    desc = args.get("space_description", "Korean apartment kitchen")
    prompt = (
        f"Remove all existing furniture, appliances and objects from this photo. "
        f"Show only the clean empty {desc} with bare walls and floor. "
        f"Preserve original lighting, wall color and perspective exactly."
    )

    result_b64 = await _call_gemini_image(prompt, args["original_image_b64"])
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({"image_base64": result_b64, "stage": "cleanup"}),
        }]
    }


@tool(
    "generate_furniture",
    "Generate furniture installation image using Flux LoRA model for the given category.",
    {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Furniture category (sink, island, closet, etc.)",
            },
            "style": {
                "type": "string",
                "description": "Design style (modern, nordic, classic, natural, industrial, luxury)",
            },
            "layout_description": {
                "type": "string",
                "description": "Compressed layout description in English (<500 chars). Include dimensions, module count, finish type.",
            },
            "cleanup_image_b64": {
                "type": "string",
                "description": "Cleanup image base64 (for reference context)",
            },
        },
        "required": ["category", "layout_description"],
    },
)
async def generate_furniture(args: dict) -> dict:
    style = args.get("style", "modern")
    prompt = (
        f"{style} style Korean built-in furniture, {args['layout_description']}, "
        f"photorealistic interior photography, natural lighting"
    )

    result_b64 = await _call_flux_lora(args["category"], prompt)
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({"image_base64": result_b64, "stage": "furniture"}),
        }]
    }


@tool(
    "generate_correction",
    "Correct furniture image to match original space — fix color temperature, lighting, perspective alignment.",
    {
        "type": "object",
        "properties": {
            "furniture_image_b64": {
                "type": "string",
                "description": "Generated furniture image as base64",
            },
            "original_image_b64": {
                "type": "string",
                "description": "Original site photo for color/lighting reference",
            },
            "correction_prompt": {
                "type": "string",
                "description": "Correction instructions in English (<500 chars)",
            },
        },
        "required": ["furniture_image_b64", "correction_prompt"],
    },
)
async def generate_correction(args: dict) -> dict:
    prompt = args["correction_prompt"]
    result_b64 = await _call_gemini_image(prompt, args["furniture_image_b64"])
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({"image_base64": result_b64, "stage": "correction"}),
        }]
    }


@tool(
    "generate_open",
    "Generate open-door view showing interior storage configuration of the furniture.",
    {
        "type": "object",
        "properties": {
            "furniture_image_b64": {
                "type": "string",
                "description": "Corrected furniture image as base64",
            },
            "category": {
                "type": "string",
                "description": "Furniture category (for appropriate interior contents)",
            },
            "open_prompt": {
                "type": "string",
                "description": "Open-door view instructions in English (<500 chars)",
            },
        },
        "required": ["furniture_image_b64", "open_prompt"],
    },
)
async def generate_open(args: dict) -> dict:
    result_b64 = await _call_gemini_image(
        args["open_prompt"], args["furniture_image_b64"]
    )
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({"image_base64": result_b64, "stage": "open"}),
        }]
    }


# MCP Server
image_server = create_sdk_mcp_server(
    name="image",
    version="1.0.0",
    tools=[generate_cleanup, generate_furniture, generate_correction, generate_open],
)
