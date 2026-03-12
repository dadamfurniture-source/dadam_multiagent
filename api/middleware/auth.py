"""인증 미들웨어 — Supabase JWT 검증"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from shared.supabase_client import get_service_client

security = HTTPBearer()


class CurrentUser(BaseModel):
    id: str
    email: str
    plan: str
    company_name: str | None = None
    company_type: str | None = None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    """JWT 토큰에서 현재 사용자 정보 추출"""
    token = credentials.credentials

    try:
        # Supabase Auth로 토큰 검증
        client = get_service_client()
        user_response = client.auth.get_user(token)

        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

        user = user_response.user

        # 프로필에서 플랜 정보 조회
        profile = (
            client.table("profiles")
            .select("plan, company_name, company_type")
            .eq("id", user.id)
            .single()
            .execute()
        )

        return CurrentUser(
            id=user.id,
            email=user.email,
            plan=profile.data.get("plan", "free") if profile.data else "free",
            company_name=profile.data.get("company_name") if profile.data else None,
            company_type=profile.data.get("company_type") if profile.data else None,
        )

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="인증 실패")


async def require_plan(minimum_plan: str):
    """최소 요금제 확인 데코레이터 팩토리"""
    plan_order = {"free": 0, "basic": 1, "pro": 2, "enterprise": 3}

    def checker(user: CurrentUser = Depends(get_current_user)):
        user_level = plan_order.get(user.plan, 0)
        required_level = plan_order.get(minimum_plan, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"이 기능은 {minimum_plan} 이상 플랜에서 사용 가능합니다. "
                       f"현재 플랜: {user.plan}",
            )
        return user

    return checker
