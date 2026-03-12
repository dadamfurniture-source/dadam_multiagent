"""다담 SaaS FastAPI 서버"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import accounting, exports, feedback, orders, payments, projects
from shared.config import settings

STATIC_DIR = Path(__file__).parent.parent / "static"


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
app.include_router(exports.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")


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
