"""Space Analysis Vision MCP Tool — Claude Vision API for site photo analysis

Analyzes customer-uploaded photos to extract:
- Wall dimensions (tile-based measurement)
- Utility positions (water supply, exhaust duct, gas pipe)
- Obstacles (windows, doors, columns)
- Furniture placement recommendations
"""

import base64
import json
import os

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from agents.prompts import SPACE_ANALYST_PROMPT

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
VISION_MODEL = "claude-sonnet-4-20250514"


async def _call_claude_vision(
    image_b64: str,
    prompt: str,
    media_type: str = "image/jpeg",
) -> dict:
    """Call Claude Vision API with image and return parsed JSON response."""

    url = "https://api.anthropic.com/v1/messages"

    body = {
        "model": VISION_MODEL,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    # Extract text content from response
    text_content = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_content += block["text"]

    # Parse JSON from response (may be wrapped in ```json blocks)
    text_content = text_content.strip()
    if text_content.startswith("```"):
        # Remove markdown code block wrapper
        lines = text_content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text_content = "\n".join(lines)

    return json.loads(text_content)


@tool(
    "analyze_space",
    "Analyze a site photo to extract wall dimensions, utility positions, obstacles, and furniture placement recommendations.",
    {
        "type": "object",
        "properties": {
            "image_b64": {
                "type": "string",
                "description": "Site photo as base64-encoded string",
            },
            "image_url": {
                "type": "string",
                "description": "Alternatively, public URL of the site photo",
            },
            "category": {
                "type": "string",
                "description": "Furniture category (sink, island, closet, etc.) for context-specific analysis",
            },
            "media_type": {
                "type": "string",
                "description": "Image MIME type (default: image/jpeg)",
            },
        },
        "required": [],
    },
)
async def analyze_space(args: dict) -> dict:
    image_b64 = args.get("image_b64")
    image_url = args.get("image_url")
    category = args.get("category", "sink")
    media_type = args.get("media_type", "image/jpeg")

    # If URL provided, download and convert to base64
    if not image_b64 and image_url:
        async with httpx.AsyncClient(timeout=30) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            image_b64 = base64.b64encode(img_resp.content).decode()
            # Detect media type from content-type header
            ct = img_resp.headers.get("content-type", "image/jpeg")
            if "png" in ct:
                media_type = "image/png"
            elif "webp" in ct:
                media_type = "image/webp"

    if not image_b64:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"error": "Either image_b64 or image_url is required"}),
            }]
        }

    # Build analysis prompt with category context
    analysis_prompt = (
        f"{SPACE_ANALYST_PROMPT}\n\n"
        f"## Current Request\n"
        f"Category: {category}\n"
        f"Analyze this photo and return the JSON output as specified above."
    )

    try:
        result = await _call_claude_vision(image_b64, analysis_prompt, media_type)
    except json.JSONDecodeError:
        # If JSON parsing fails, return raw analysis with error flag
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "error": "Failed to parse structured analysis. Retry with clearer photo.",
                    "confidence": "low",
                }),
            }]
        }

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False),
        }]
    }


@tool(
    "analyze_space_quick",
    "Quick space analysis — returns only wall dimensions and key utility positions. Faster and cheaper than full analysis.",
    {
        "type": "object",
        "properties": {
            "image_b64": {
                "type": "string",
                "description": "Site photo as base64",
            },
            "image_url": {
                "type": "string",
                "description": "Public URL of the site photo",
            },
        },
        "required": [],
    },
)
async def analyze_space_quick(args: dict) -> dict:
    image_b64 = args.get("image_b64")
    image_url = args.get("image_url")
    media_type = "image/jpeg"

    if not image_b64 and image_url:
        async with httpx.AsyncClient(timeout=30) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            image_b64 = base64.b64encode(img_resp.content).decode()

    if not image_b64:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"error": "Either image_b64 or image_url is required"}),
            }]
        }

    quick_prompt = """Analyze this Korean apartment photo quickly.
Return ONLY JSON with these fields:
{
  "wall_dimensions_mm": {"width": ..., "height": ...},
  "water_supply_detected": true/false,
  "water_supply_position_mm": ... or null,
  "exhaust_duct_detected": true/false,
  "exhaust_duct_position_mm": ... or null,
  "gas_pipe_detected": true/false,
  "space_summary": "brief description"
}
Use Korean standard tiles (300x600mm) for measurement if visible.
Output ONLY valid JSON, no extra text."""

    try:
        result = await _call_claude_vision(image_b64, quick_prompt, media_type)
    except json.JSONDecodeError:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"error": "Analysis failed", "confidence": "low"}),
            }]
        }

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False),
        }]
    }


# MCP Server
vision_server = create_sdk_mcp_server(
    name="vision",
    version="1.0.0",
    tools=[analyze_space, analyze_space_quick],
)
