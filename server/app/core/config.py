import json
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database — SQLite for MVP, swap to mysql+pymysql://... to migrate.
    database_url: str = "sqlite:///./kairos.db"

    # Auth。留空或仍是模板占位值时，首次启动随机生成并存到 jwt_secret_file（见 _resolve_jwt_secret）
    jwt_secret: str = ""
    jwt_secret_file: str = "./data/jwt_secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # Data provider: "auto" | "seed" | "akshare" | "tencent"
    provider: str = "auto"

    # AI provider: "auto" | "rule" | "deepseek" | "openai" | "anthropic"（见 app/services/llm.py）
    ai_provider: str = "auto"
    llm_api_key: str = ""
    llm_base_url: str = ""  # 留空用各 provider 官方地址
    llm_model: str = ""  # 留空用各 provider 默认模型（openai 必填）
    llm_extra_body: str = ""  # JSON，浅合并进请求体，null 值删除字段
    # 旧配置，LLM_* 留空时 deepseek / auto 回落读取
    deepseek_api_key: str = ""
    # deepseek-chat 已过官方停用日（2026-07-24）；flash 默认开思考，各调用处显式关闭
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Collector: only run inside A-share trading sessions
    collect_interval_seconds: int = 60
    enable_collector: bool = True
    # 模拟盘盘后结算，暂停中（前端入口已隐藏）。恢复时设 ENABLE_PAPER=true，
    # settle_all 会按交易日历自动补结算暂停期间的日子
    enable_paper: bool = False
    # AI 操盘手（app/services/agent.py）：管理员建账户后盘前 / 盘后自动运行，需配好 LLM
    enable_agent: bool = True
    # 联网搜索（Tavily）。留空时 agent 只有站内数据 + 东财快讯，没有自由搜索工具
    tavily_api_key: str = ""

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

    # SQLite 每日备份（gzip 压缩，保留最近 N 份）；仍在同一数据卷上，异地副本见 deploy/README.md
    backup_dir: str = "./data/backups"
    backup_keep: int = 3

    @field_validator("llm_extra_body")
    @classmethod
    def _extra_body_is_json_object(cls, v: str) -> str:
        if v and not isinstance(json.loads(v), dict):
            raise ValueError("LLM_EXTRA_BODY 必须是 JSON 对象")
        return v


# 历史模板 / compose 里出现过的占位值：照抄就等于公开了签名密钥，任何人都能伪造 token
_PLACEHOLDER_SECRETS = {"", "dev-secret-change-me", "change-me-in-production", "change-me"}


def _resolve_jwt_secret(s: Settings) -> None:
    if s.jwt_secret not in _PLACEHOLDER_SECRETS:
        return
    path = Path(s.jwt_secret_file)
    try:
        if path.is_file() and path.read_text().strip():
            s.jwt_secret = path.read_text().strip()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        s.jwt_secret = secrets.token_urlsafe(48)
        path.write_text(s.jwt_secret)
        path.chmod(0o600)
        logging.getLogger("kairos").warning("JWT_SECRET 未设置，已随机生成并保存到 %s", path)
    except OSError as exc:  # 只读文件系统等：退化为进程内随机密钥，重启后需重新登录
        s.jwt_secret = secrets.token_urlsafe(48)
        logging.getLogger("kairos").warning("JWT_SECRET 未设置且无法写入 %s（%s），本次使用临时密钥", path, exc)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    _resolve_jwt_secret(s)
    return s


settings = get_settings()
