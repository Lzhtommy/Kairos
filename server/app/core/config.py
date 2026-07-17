from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database — SQLite for MVP, swap to mysql+pymysql://... to migrate.
    database_url: str = "sqlite:///./kairos.db"

    # Auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # Data provider: "auto" | "seed" | "akshare"
    provider: str = "auto"

    # AI provider: "auto" | "rule" | "claude"
    ai_provider: str = "auto"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Collector: only run inside A-share trading sessions
    collect_interval_seconds: int = 60
    enable_collector: bool = True

    # CORS
    cors_origins: str = "*"

    # Where backtest curve files are written
    reports_dir: str = "./data/reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
