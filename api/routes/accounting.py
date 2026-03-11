"""매출매입 API — 경영지원"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import CurrentUser, get_current_user
from api.schemas.common import APIResponse
from shared.supabase_client import get_service_client

router = APIRouter(prefix="/accounting", tags=["Accounting"])


# ===== 매출 =====


@router.get("/revenue", response_model=APIResponse)
async def list_revenue(
    order_id: str | None = None,
    status: str | None = None,
    year: int | None = None,
    month: int | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """매출 전표 목록 조회 (Pro+)"""
    if user.plan not in ("pro", "enterprise"):
        raise HTTPException(403, "Pro 이상 플랜에서 사용 가능합니다.")

    client = get_service_client()
    query = (
        client.table("revenue_entries")
        .select("*, orders(order_number, customer_id)")
        .order("created_at", desc=True)
    )

    if order_id:
        query = query.eq("order_id", order_id)
    if status:
        query = query.eq("status", status)
    if year and month:
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month + 1:02d}-01" if month < 12 else f"{year + 1}-01-01"
        query = query.gte("created_at", start).lt("created_at", end)

    result = query.execute()
    return APIResponse(data={"items": result.data})


# ===== 매입 =====


@router.get("/expense", response_model=APIResponse)
async def list_expense(
    order_id: str | None = None,
    vendor_id: str | None = None,
    category: str | None = None,
    status: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """매입 전표 목록 조회 (Pro+)"""
    if user.plan not in ("pro", "enterprise"):
        raise HTTPException(403, "Pro 이상 플랜에서 사용 가능합니다.")

    client = get_service_client()
    query = (
        client.table("expense_entries")
        .select("*, orders(order_number), vendors(name)")
        .order("created_at", desc=True)
    )

    if order_id:
        query = query.eq("order_id", order_id)
    if vendor_id:
        query = query.eq("vendor_id", vendor_id)
    if category:
        query = query.eq("category", category)
    if status:
        query = query.eq("status", status)

    result = query.execute()
    return APIResponse(data={"items": result.data})


# ===== 손익 =====


@router.get("/pnl/{order_id}", response_model=APIResponse)
async def get_order_pnl(
    order_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """프로젝트별 손익 조회 (Pro+)"""
    if user.plan not in ("pro", "enterprise"):
        raise HTTPException(403, "Pro 이상 플랜에서 사용 가능합니다.")

    client = get_service_client()

    order = client.table("orders").select("*").eq("id", order_id).single().execute()
    if not order.data:
        raise HTTPException(404, "주문을 찾을 수 없습니다.")

    revenues = client.table("revenue_entries").select("*").eq("order_id", order_id).execute()
    expenses = client.table("expense_entries").select("*").eq("order_id", order_id).execute()

    total_rev = sum(r["amount"] for r in revenues.data)
    collected = sum(r["amount"] for r in revenues.data if r["status"] == "collected")
    total_exp = sum(e["amount"] for e in expenses.data)
    paid = sum(e["amount"] for e in expenses.data if e["status"] == "paid")

    exp_by_cat = {}
    for e in expenses.data:
        exp_by_cat[e["category"]] = exp_by_cat.get(e["category"], 0) + e["amount"]

    gross_profit = total_rev - total_exp
    margin = gross_profit / total_rev if total_rev > 0 else 0

    return APIResponse(data={
        "order_id": order_id,
        "order_number": order.data.get("order_number"),
        "contract_amount": order.data.get("contract_amount", 0),
        "revenue": {
            "total": total_rev,
            "collected": collected,
            "outstanding": total_rev - collected,
            "entries": revenues.data,
        },
        "expense": {
            "total": total_exp,
            "paid": paid,
            "outstanding": total_exp - paid,
            "by_category": exp_by_cat,
            "entries": expenses.data,
        },
        "gross_profit": gross_profit,
        "margin_rate": round(margin, 4),
    })


@router.get("/summary", response_model=APIResponse)
async def get_monthly_summary(
    year: int,
    month: int,
    user: CurrentUser = Depends(get_current_user),
):
    """월별 매출/매입 요약 (Pro+)"""
    if user.plan not in ("pro", "enterprise"):
        raise HTTPException(403, "Pro 이상 플랜에서 사용 가능합니다.")

    client = get_service_client()
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month + 1:02d}-01" if month < 12 else f"{year + 1}-01-01"

    revenues = (
        client.table("revenue_entries")
        .select("category, amount, status")
        .gte("created_at", start).lt("created_at", end)
        .execute()
    )
    expenses = (
        client.table("expense_entries")
        .select("category, amount, status")
        .gte("created_at", start).lt("created_at", end)
        .execute()
    )

    rev_total = sum(r["amount"] for r in revenues.data)
    rev_collected = sum(r["amount"] for r in revenues.data if r["status"] == "collected")
    exp_total = sum(e["amount"] for e in expenses.data)
    exp_paid = sum(e["amount"] for e in expenses.data if e["status"] == "paid")

    # 연체
    overdue_rev = (
        client.table("revenue_entries")
        .select("order_id, amount")
        .in_("status", ["pending", "invoiced"])
        .lt("due_date", date.today().isoformat())
        .execute()
    )
    overdue_exp = (
        client.table("expense_entries")
        .select("order_id, amount")
        .eq("status", "approved")
        .lt("due_date", date.today().isoformat())
        .execute()
    )

    return APIResponse(data={
        "period": f"{year}-{month:02d}",
        "revenue": {"total": rev_total, "collected": rev_collected},
        "expense": {"total": exp_total, "paid": exp_paid},
        "gross_profit": rev_total - exp_total,
        "cash_flow": rev_collected - exp_paid,
        "overdue": {
            "receivable": sum(r["amount"] for r in overdue_rev.data),
            "payable": sum(e["amount"] for e in overdue_exp.data),
        },
    })
