"""다담 SaaS 설정"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    # API Keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    replicate_api_token: str = os.getenv("REPLICATE_API_TOKEN", "")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")

    # Supabase
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # App
    environment: str = os.getenv("ENVIRONMENT", "development")
    api_base_url: str = os.getenv("API_BASE_URL", "http://localhost:8000")
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
