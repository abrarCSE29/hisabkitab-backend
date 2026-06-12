from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Known development-only secret; production refuses to start with it (see create_app).
DEV_PLACEHOLDER_JWT_SECRET = "local-dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_name: str = "HisabKitab API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    log_file: str = "server.log"  # set LOG_FILE= (empty) to log to stdout only

    # Comma-separated list of allowed browser origins; "*" only for local dev.
    cors_origins: str = "*"

    # MongoDB Atlas (M0 free tier friendly defaults)
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "hisabkitab"

    # Supabase Auth
    supabase_url: str = ""
    supabase_jwt_secret: str = DEV_PLACEHOLDER_JWT_SECRET
    supabase_jwt_audience: str = "authenticated"

    # Groq (FR-6 OCR, OpenAI-compatible API)
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    ocr_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"


@lru_cache
def get_settings() -> Settings:
    return Settings()
