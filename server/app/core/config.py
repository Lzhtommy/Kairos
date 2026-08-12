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

    # Data provider: "auto" | "seed" | "akshare" | "tencent"
    provider: str = "auto"

    # AI provider: "auto" | "rule" | "deepseek"
    ai_provider: str = "auto"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Collector: only run inside A-share trading sessions
    collect_interval_seconds: int = 60
    enable_collector: bool = True

    # 行业轮动模块的板块数据源："auto" | "sw" | "eastmoney" | "off"
    # （auto 先试申万研究口径，再回落东财行业板块；首次落库后按库内口径锁定）
    sector_source: str = "auto"

    # CORS
    cors_origins: str = "*"

    # Notifications (strategy-run pushes + data alerts). Email needs SMTP;
    # 465/SSL because cloud providers block port 25.
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # 数据断供告警的管理员渠道（"email" | "webhook"）
    alert_channel: str = "none"
    alert_target: str = ""

    # Where backtest curve files are written
    reports_dir: str = "./data/reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
