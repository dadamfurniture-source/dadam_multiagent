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
            description="공간 사진을 분석하여 벽면, 배관, 치수 정보를 추출하는 에이전트. 사진 분석이 필요할 때 사용.",
            prompt=SPACE_ANALYST_PROMPT,
            tools=[
                "mcp__vision__analyze_space",
                "mcp__supabase__read_project",
            ],
            model="opus",
        ),
        "design-planner": AgentDefinition(
            description="공간 분석 결과를 기반으로 가구 모듈 배치를 계획하는 에이전트. 공간 분석 완료 후 사용.",
            prompt=DESIGN_PLANNER_PROMPT,
            tools=[
                "mcp__supabase__read_project",
                "mcp__pricing__get_modules",
            ],
            model="sonnet",
        ),
        "image-generator": AgentDefinition(
            description="배치 계획을 기반으로 시뮬레이션 이미지를 생성하는 에이전트. 배치 계획 완료 후 사용.",
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
            description="배치 계획의 모듈 구성으로 견적을 산출하는 에이전트. 배치 계획 완료 후 사용.",
            prompt=QUOTE_CALCULATOR_PROMPT,
            tools=[
                "mcp__pricing__get_prices",
                "mcp__pricing__get_installation_cost",
                "mcp__supabase__save_quote",
            ],
            model="haiku",
        ),
    }

    # Pro+ 에이전트
    plan_features = PLANS.get(user_plan, {}).get("features", [])

    if "detail_design" in plan_features:
        agents["detail-designer"] = AgentDefinition(
            description="상세 제작 설계도를 생성하는 에이전트 (Pro 이상). 견적 완료 후 상세 설계가 필요할 때 사용.",
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
            description="자재 명세서(BOM)를 생성하는 에이전트 (Pro 이상). 상세 설계 완료 후 사용.",
            prompt="상세 설계를 기반으로 자재 명세서를 생성합니다. 모든 부품, 수량, 규격을 정확히 산출하세요.",
            tools=[
                "mcp__supabase__read_project",
                "mcp__pricing__get_materials",
            ],
            model="sonnet",
        )

    # QA는 Pro+ 에서만 자동 실행
    if user_plan in ("pro", "enterprise"):
        agents["qa-reviewer"] = AgentDefinition(
            description="설계 결과의 품질을 검증하는 에이전트 (Pro 이상). 모든 설계 완료 후 최종 검증에 사용.",
            prompt=QA_REVIEWER_PROMPT,
            tools=["mcp__supabase__read_project"],
            model="opus",
        )

    return agents


def _build_orchestrator_prompt(request: ProjectRequest) -> str:
    """요청에 따른 오케스트레이터 프롬프트 생성"""

    plan_features = PLANS.get(request.user_plan, {}).get("features", [])
    has_detail = "detail_design" in plan_features
    has_bom = "bom" in plan_features
    is_pro = request.user_plan in ("pro", "enterprise")

    steps = [
        "1. space-analyst 에이전트로 업로드된 사진의 공간을 분석하세요.",
        "2. design-planner 에이전트로 분석된 공간에 가구 배치를 계획하세요.",
        "3. image-generator 에이전트로 시뮬레이션 이미지를 생성하세요.",
        "4. quote-calculator 에이전트로 견적을 산출하세요.",
    ]

    if has_detail:
        steps.append("5. detail-designer 에이전트로 상세 제작 설계도를 생성하세요.")
    if has_bom:
        steps.append("6. bom-generator 에이전트로 자재 명세서를 생성하세요.")
    if is_pro:
        steps.append(f"{'7' if has_bom else '6' if has_detail else '5'}. qa-reviewer 에이전트로 최종 품질 검증하세요.")

    steps_text = "\n".join(steps)
    style_text = f"선호 스타일: {request.style}" if request.style else "스타일: 자동 추천"
    budget_text = f"예산: {request.budget:,}원" if request.budget else "예산: 미지정"
    notes_text = f"추가 요청: {request.notes}" if request.notes else ""

    return f"""고객의 주문제작 가구 시뮬레이션 요청을 처리하세요.

## 프로젝트 정보
- 프로젝트 ID: {request.project_id}
- 품목: {request.category}
- {style_text}
- {budget_text}
- 사진 URL: {request.image_url}
{notes_text}

## 처리 순서
{steps_text}

각 에이전트의 결과를 다음 에이전트에 전달하고, 최종 결과를 JSON으로 통합하여 반환하세요.
에이전트 호출 시 이전 단계의 결과를 명확히 전달하세요.
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
