"""주문/운영 API — 상담~설치~A/S 생명주기"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import CurrentUser, get_current_user
from api.schemas.common import APIResponse
from shared.supabase_client import get_service_client

router = APIRouter(prefix="/orders", tags=["Orders"])


class OrderCreateRequest(BaseModel):
    project_id: str
    quote_id: str
    delivery_address: str | None = None
    notes: str | None = None


class OrderStatusUpdate(BaseModel):
    status: str
    reason: str | None = None


# ===== 주문 CRUD =====


@router.post("", response_model=APIResponse)
async def create_order(
    body: OrderCreateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """견적 기반으로 주문 생성 (상담 시작)"""
    client = get_service_client()

    # 견적 조회
    quote = client.table("quotes").select("*").eq("id", body.quote_id).single().execute()
    if not quote.data:
        raise HTTPException(404, "견적을 찾을 수 없습니다.")

    order = (
        client.table("orders")
        .insert({
            "project_id": body.project_id,
            "customer_id": user.id,
            "quote_id": body.quote_id,
            "contract_amount": quote.data["total_price"],
            "delivery_address": body.delivery_address,
            "notes": body.notes,
            "status": "consulting",
        })
        .execute()
    )

    # 상태 이력 기록
    client.table("order_status_history").insert({
        "order_id": order.data[0]["id"],
        "to_status": "consulting",
        "changed_by": user.id,
        "reason": "주문 생성",
    }).execute()

    return APIResponse(
        message="주문이 생성되었습니다.",
        data=order.data[0],
    )


@router.get("", response_model=APIResponse)
async def list_orders(
    page: int = 1,
    per_page: int = 20,
    status: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """내 주문 목록 조회"""
    client = get_service_client()

    query = (
        client.table("orders")
        .select("*, projects(name, category)", count="exact")
        .eq("customer_id", user.id)
        .order("created_at", desc=True)
        .range((page - 1) * per_page, page * per_page - 1)
    )

    if status:
        query = query.eq("status", status)

    result = query.execute()

    return APIResponse(
        data={
            "items": result.data,
            "total": result.count or 0,
            "page": page,
            "per_page": per_page,
        }
    )


@router.get("/{order_id}", response_model=APIResponse)
async def get_order(
    order_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """주문 상세 조회 (일정, 매출, A/S 포함)"""
    client = get_service_client()

    order = (
        client.table("orders")
        .select("*, projects(name, category, style)")
        .eq("id", order_id)
        .eq("customer_id", user.id)
        .single()
        .execute()
    )

    if not order.data:
        raise HTTPException(404, "주문을 찾을 수 없습니다.")

    # 관련 데이터
    schedules = (
        client.table("schedules")
        .select("*")
        .eq("order_id", order_id)
        .order("scheduled_at")
        .execute()
    )

    history = (
        client.table("order_status_history")
        .select("*")
        .eq("order_id", order_id)
        .order("created_at")
        .execute()
    )

    as_tickets = (
        client.table("after_service_tickets")
        .select("*")
        .eq("order_id", order_id)
        .execute()
    )

    return APIResponse(
        data={
            "order": order.data,
            "schedules": schedules.data,
            "history": history.data,
            "as_tickets": as_tickets.data,
        }
    )


@router.get("/{order_id}/timeline", response_model=APIResponse)
async def get_order_timeline(
    order_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """주문 타임라인 (상태이력 + 일정 통합)"""
    client = get_service_client()

    # 소유권 확인
    order = (
        client.table("orders")
        .select("id")
        .eq("id", order_id)
        .eq("customer_id", user.id)
        .single()
        .execute()
    )
    if not order.data:
        raise HTTPException(404, "주문을 찾을 수 없습니다.")

    history = (
        client.table("order_status_history")
        .select("to_status, reason, created_at")
        .eq("order_id", order_id)
        .order("created_at")
        .execute()
    )

    schedules = (
        client.table("schedules")
        .select("type, title, scheduled_at, status")
        .eq("order_id", order_id)
        .order("scheduled_at")
        .execute()
    )

    # 통합 타임라인
    timeline = []
    for h in history.data:
        timeline.append({
            "type": "status_change",
            "status": h["to_status"],
            "reason": h.get("reason"),
            "at": h["created_at"],
        })
    for s in schedules.data:
        timeline.append({
            "type": "schedule",
            "schedule_type": s["type"],
            "title": s["title"],
            "status": s["status"],
            "at": s["scheduled_at"],
        })

    timeline.sort(key=lambda x: x["at"])

    return APIResponse(data={"timeline": timeline})
