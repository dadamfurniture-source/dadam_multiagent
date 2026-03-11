"""다담 SaaS FastAPI 서버"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import accounting, feedback, orders, projects
from shared.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("다담 SaaS API 서버 시작")
    yield
    print("다담 SaaS API 서버 종료")


app = FastAPI(
    title="다담 SaaS API",
    description="AI 주문제작 가구 시뮬레이션 & 견적 플랫폼",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(projects.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(accounting.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "dadam-saas", "version": "0.1.0"}
