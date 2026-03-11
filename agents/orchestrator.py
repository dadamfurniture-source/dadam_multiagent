"""CEO Agent - 전체 파이프라인 오케스트레이터"""

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncGenerator

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

from agents.prompts import (
    DESIGN_PLANNER_PROMPT,
    DETAIL_DESIGNER_PROMPT,
    IMAGE_GENERATOR_PROMPT,
    QA_REVIEWER_PROMPT,
    QUOTE_CALCULATOR_PROMPT,
    SPACE_ANALYST_PROMPT,
)
from shared.constants import PLANS


@dataclass
class ProjectRequest:
    project_id: str
    user_id: str
    user_plan: str  # free, basic, pro, enterprise
    category: str  # sink, island, closet, etc.
    style: str | None  # modern, nordic, etc.
    budget: int | None  # KRW
    image_url: str  # 업로드된 사진 URL
    notes: str | None  # 추가 요청사항


@dataclass
class ProjectResult:
    project_id: str
    space_analysis: dict | None = None
    layout: dict | None = None
    simulation_images: list[str] | None = None
    quote: dict | None = None
    detail_design: dict | None = None
    bom: dict | None = None
    qa_report: dict | None = None
    status: str = "processing"
    error: str | None = None


def _build_agents(user_plan: str) -> dict[str, AgentDefinition]:
    """요금제에 따라 사용 가능한 에이전트 구성"""

    # 기본 에이전트 (모든 플랜)
    agents = {
        "space-analyst": AgentDefinition(
            description="Analyze space photos to extract wall dimensions, utilities, and measurements. Use first.",
            prompt=SPACE_ANALYST_PROMPT,
            tools=[
                "mcp__vision__analyze_space",
                "mcp__supabase__read_project",
            ],
            model="opus",
        ),
        "design-planner": AgentDefinition(
            description="Plan furniture module layout based on space analysis. Use after space analysis is complete.",
            prompt=DESIGN_PLANNER_PROMPT,
            tools=[
                "mcp__supabase__read_project",
                "mcp__layout__plan_furniture_layout",
                "mcp__layout__get_open_door_contents",
                "mcp__pricing__get_modules",
                "mcp__feedback__search_similar_cases",
                "mcp__feedback__get_active_constraints",
            ],
            model="sonnet",
        ),
        "image-generator": AgentDefinition(
            description="Generate simulation images from layout plan. Use after design planning is complete.",
            prompt=IMAGE_GENERATOR_PROMPT,
            tools=[
                "mcp__image__generate_cleanup",
                "mcp__image__generate_furniture",
                "mcp__image__generate_correction",
                "mcp__image__generate_open",
                "mcp__supabase__upload_image",
            ],
            model="sonnet",
        ),
        "quote-calculator": AgentDefinition(
            description="Calculate quote from module layout. Use after design planning is complete.",
            prompt=QUOTE_CALCULATOR_PROMPT,
            tools=[
                "mcp__pricing__get_prices",
                "mcp__pricing__get_installation_cost",
                "mcp__supabase__save_quote",
                "mcp__feedback__get_price_calibration",
            ],
            model="haiku",
        ),
    }

    # Pro+ 에이전트
    plan_features = PLANS.get(user_plan, {}).get("features", [])

    if "detail_design" in plan_features:
        agents["detail-designer"] = AgentDefinition(
            description="Generate manufacturing-grade detail design drawings (Pro+). Use after quote is complete.",
            prompt=DETAIL_DESIGNER_PROMPT,
            tools=[
                "mcp__supabase__read_project",
                "mcp__drawing__generate_svg",
                "mcp__supabase__save_design",
            ],
            model="opus",
        )

    if "bom" in plan_features:
        agents["bom-generator"] = AgentDefinition(
            description="Generate Bill of Materials (BOM) from detail design (Pro+). Use after detail design.",
            prompt="Generate a complete Bill of Materials from the detail design. List every component with exact quantities, dimensions (mm), material specifications, and unit costs. Group by: panels, hardware (hinges/slides/handles), countertop, and accessories.",
            tools=[
                "mcp__supabase__read_project",
                "mcp__pricing__get_materials",
            ],
            model="sonnet",
        )

    # QA runs automatically for Pro+
    if user_plan in ("pro", "enterprise"):
        agents["qa-reviewer"] = AgentDefinition(
            description="QA reviewer for final quality validation of all design outputs (Pro+). Use last.",
            prompt=QA_REVIEWER_PROMPT,
            tools=["mcp__supabase__read_project"],
            model="opus",
        )

    return agents


def _build_orchestrator_prompt(request: ProjectRequest) -> str:
    """Build orchestrator prompt for given project request"""

    plan_features = PLANS.get(request.user_plan, {}).get("features", [])
    has_detail = "detail_design" in plan_features
    has_bom = "bom" in plan_features
    is_pro = request.user_plan in ("pro", "enterprise")

    step_num = 1
    steps = [
        f"{step_num}. Use space-analyst to analyze the uploaded site photo. Extract wall dimensions, utility positions, and obstacles.",
    ]
    step_num += 1
    steps.append(f"{step_num}. Use design-planner to create furniture module layout based on space analysis. Pass wall_width, category, sink/cooktop positions.")
    step_num += 1
    steps.append(f"{step_num}. Use image-generator to create simulation images (cleanup → furniture → correction → open-door).")
    step_num += 1
    steps.append(f"{step_num}. Use quote-calculator to calculate pricing from the module layout.")

    if has_detail:
        step_num += 1
        steps.append(f"{step_num}. Use detail-designer to generate manufacturing-grade design drawings.")
    if has_bom:
        step_num += 1
        steps.append(f"{step_num}. Use bom-generator to create Bill of Materials from detail design.")
    if is_pro:
        step_num += 1
        steps.append(f"{step_num}. Use qa-reviewer for final quality validation of all outputs.")

    steps_text = "\n".join(steps)
    style_text = f"Preferred style: {request.style}" if request.style else "Style: auto-recommend"
    budget_text = f"Budget: {request.budget:,} KRW" if request.budget else "Budget: not specified"
    notes_text = f"Additional notes: {request.notes}" if request.notes else ""

    return f"""Process a custom furniture simulation request.

## Project Information
- Project ID: {request.project_id}
- Category: {request.category}
- {style_text}
- {budget_text}
- Photo URL: {request.image_url}
{notes_text}

## Processing Pipeline
{steps_text}

IMPORTANT:
- Pass each agent's output as input to the next agent in the chain.
- All prompts for image generation must be in English and under 500 characters.
- The layout plan must use the layout engine (plan_furniture_layout tool) for precise module distribution.
- Return the final consolidated result as JSON with keys: space_analysis, layout, images, quote.
"""


async def process_project(request: ProjectRequest) -> AsyncGenerator[dict, None]:
    """메인 오케스트레이터 - 프로젝트 처리 파이프라인 실행

    Yields:
        진행 상황 및 결과 딕셔너리
    """
    yield {"type": "status", "stage": "started", "project_id": request.project_id}

    agents = _build_agents(request.user_plan)
    prompt = _build_orchestrator_prompt(request)

    result = ProjectResult(project_id=request.project_id)

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=["Agent", "mcp__supabase__update_project"],
                agents=agents,
            ),
        ):
            # 에이전트 진행 상황 스트리밍
            if hasattr(message, "content"):
                yield {"type": "progress", "content": str(message.content)}

            if hasattr(message, "result"):
                result.status = "completed"
                yield {
                    "type": "result",
                    "project_id": request.project_id,
                    "data": message.result,
                }

    except Exception as e:
        result.status = "failed"
        result.error = str(e)
        yield {
            "type": "error",
            "project_id": request.project_id,
            "error": str(e),
        }
