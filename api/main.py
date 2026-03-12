"""다담 SaaS FastAPI 서버"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.middleware.error_handler import register_error_handlers
from api.middleware.logging_mw import RequestLoggingMiddleware
from api.middleware.rate_limit import RateLimitMiddleware
from api.middleware.security_headers import SecurityHeadersMiddleware
from api.routes import accounting, admin, enterprise, exports, feedback, orders, payments, projects
from shared.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.getLogger(__name__).info("다담 SaaS API 서버 시작 (env=%s)", settings.environment)
    yield
    logging.getLogger(__name__).info("다담 SaaS API 서버 종료")


app = FastAPI(
    title="다담 SaaS API",
    description="AI 주문제작 가구 시뮬레이션 & 견적 플랫폼",
    version="0.1.0",
    lifespan=lifespan,
)

# 미들웨어 (역순으로 등록 = 먼저 등록한 것이 바깥 레이어)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "stripe-signature"],
)

# 글로벌 에러 핸들러
register_error_handlers(app)

# 라우터 등록
app.include_router(projects.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(accounting.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(exports.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(enterprise.router, prefix="/api/v1")


@app.get("/api/v1/config")
async def frontend_config():
    """프론트엔드에 필요한 공개 설정값 (Supabase URL, anon key)"""
    return {
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "dadam-saas", "version": "0.1.0"}


# Static files & HTML pages
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/{page}.html")
async def serve_page(page: str):
    if "/" in page or "\\" in page or ".." in page:
        return FileResponse(STATIC_DIR / "index.html")
    file_path = STATIC_DIR / f"{page}.html"
    if file_path.exists() and file_path.resolve().parent == STATIC_DIR.resolve():
        return FileResponse(file_path)
    return FileResponse(STATIC_DIR / "index.html")
