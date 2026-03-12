"""인증 미들웨어 — Supabase JWT + API Key 인증"""

import hashlib
from datetime import datetime

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
    via_api_key: bool = False  # API Key 인증 여부
    api_key_id: str | None = None


def _authenticate_api_key(token: str) -> CurrentUser | None:
    """API Key (dk_live_...) 인증"""
    if not token.startswith("dk_live_"):
        return None

    client = get_service_client()
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    result = (
        client.table("api_keys")
        .select("id, user_id, scopes, is_active, expires_at")
        .eq("key_hash", key_hash)
        .eq("is_active", True)
        .execute()
    )

    if not result.data:
        return None

    key_data = result.data[0]

    # 만료 확인
    if key_data.get("expires_at"):
        if datetime.fromisoformat(key_data["expires_at"].replace("Z", "+00:00")) < datetime.utcnow().replace(tzinfo=None):
            return None

    # 사용 시간 업데이트
    client.table("api_keys").update({
        "last_used_at": datetime.utcnow().isoformat(),
    }).eq("id", key_data["id"]).execute()

    # 유저 프로필 조회
    profile = (
        client.table("profiles")
        .select("plan, company_name, company_type")
        .eq("id", key_data["user_id"])
        .single()
        .execute()
    )

    # 이메일 조회
    user_auth = client.auth.admin.get_user_by_id(key_data["user_id"])
    email = user_auth.user.email if user_auth and user_auth.user else ""

    return CurrentUser(
        id=key_data["user_id"],
        email=email,
        plan=profile.data.get("plan", "free") if profile.data else "free",
        company_name=profile.data.get("company_name") if profile.data else None,
        company_type=profile.data.get("company_type") if profile.data else None,
        via_api_key=True,
        api_key_id=key_data["id"],
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    """JWT 토큰 또는 API Key에서 현재 사용자 정보 추출"""
    token = credentials.credentials

    try:
        # API Key 인증 시도 (dk_live_ 접두사)
        if token.startswith("dk_live_"):
            user = _authenticate_api_key(token)
            if not user:
                raise HTTPException(status_code=401, detail="유효하지 않은 API Key입니다.")
            return user

        # Supabase JWT 인증
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
